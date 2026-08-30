"""
Agent Friday — HardwareProfile

The machine, described well enough to place models on it. Detected, cached,
serialized, and refreshed when the hardware actually changes.

Why this exists (decision D4, docs/audits/decisions-2026-08.md): Friday had no
hardware-profile concept anywhere. Detection existed in three unrelated places
and fed only Ollama *install advice* and a binary voice CPU/GPU gate -- nothing
detected ever influenced chat, image, or embedding model selection. This module
is the single detector those three collapse into.

It EXTENDS the existing detection rather than forking a fourth:
  * routing/ollama_manager.detect_hardware  -- GPU name/VRAM, RAM, platform
  * services/nemo_voice.gpu_tier_ready      -- CUDA availability + free VRAM
  * services/compute_provider._compute_specs -- CPU/RAM, with gpu_* declared
                                                and never populated

Three things it does that none of those did:

1. **Multi-GPU is first-class.** `nvidia-smi --query-gpu` emits ONE LINE PER
   GPU. `ollama_manager.detect_hardware` does `stdout.strip().split(",")` and
   reads parts[0]/parts[1] -- on a two-GPU host that reads the first GPU's name
   and VRAM and silently discards the rest. Every GPU is parsed here, kept in
   index order, and every budget downstream is computed per device.

2. **The GPU baseline is measured, not assumed.** On the reference instance the
   Windows compositor holds 1261 MiB of the 12282 MiB card with zero models
   resident. A VRAM budget that ignores it overcommits by exactly that much.

3. **Every estimate records its method.** Memory bandwidth is a heuristic from
   SMBIOS module speed and channel count, not a microbenchmark; disk read rate
   is a real sequential read whose page-cache state is disclosed. A number
   without its method is a number you cannot later distrust correctly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent_friday.core import runtime_dir

_log = logging.getLogger("friday.hardware_profile")

# Windows: never flash a console window for a probe (same flag the rest of the
# tree guards on; see routing/ollama_manager.py).
_POPEN_FLAGS = 0x08000000 if sys.platform == "win32" else 0

PROFILE_VERSION = 1

# SMBIOSMemoryType -> bandwidth class. 26 = DDR4, 34/35 = DDR5, 30 = LPDDR4.
_SMBIOS_MEM_TYPE = {
    20: "ddr", 21: "ddr2", 24: "ddr3", 26: "ddr4",
    30: "lpddr4", 34: "ddr5", 35: "lpddr5",
}

# OS memory reserve (policy rule R1). Windows genuinely needs more headroom
# than Linux before paging becomes destructive.
OS_RESERVE_MIB = {"windows": 6144, "linux": 4096, "darwin": 4096}


def _run(cmd, timeout=15):
    """Best-effort subprocess text capture. Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, creationflags=_POPEN_FLAGS)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  OS / CPU / RAM
# ─────────────────────────────────────────────────────────────────────────────

def _os_family() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def detect_os() -> dict:
    return {"family": _os_family(), "version": platform.version(),
            "release": platform.release()}


def detect_cpu() -> dict:
    threads = os.cpu_count() or 1
    physical, model = None, platform.processor() or ""
    try:
        import psutil
        physical = psutil.cpu_count(logical=False)
    except Exception:
        pass
    if _os_family() == "windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_Processor | "
                    "Select-Object -First 1 Name,NumberOfCores | "
                    "ConvertTo-Json -Compress)"])
        try:
            d = json.loads(out)
            model = d.get("Name", model) or model
            physical = physical or d.get("NumberOfCores")
        except Exception:
            pass
    return {"threads": threads, "physical_cores": physical,
            "model": model.strip()}


def detect_ram() -> dict:
    total_mib = available_mib = 0
    try:
        import psutil
        vm = psutil.virtual_memory()
        total_mib = round(vm.total / 1048576)
        available_mib = round(vm.available / 1048576)
    except Exception:
        if _os_family() == "linux":
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal"):
                            total_mib = round(int(line.split()[1]) / 1024)
                        elif line.startswith("MemAvailable"):
                            available_mib = round(int(line.split()[1]) / 1024)
            except Exception:
                pass
    return {"total_mib": total_mib, "available_mib": available_mib}


def detect_memory_bandwidth() -> dict:
    """Bandwidth CLASS, by heuristic, with the method recorded.

    Not a microbenchmark: this reads the SMBIOS module type, clock, and channel
    count and multiplies. It is right about the class (which is what placement
    needs -- 'is CPU offload going to be painful?') and approximate about the
    number. `method` says so, so a caller can never mistake it for measured.
    """
    cls, gb_s, method = "unknown", None, "heuristic-smbios"
    if _os_family() == "windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_PhysicalMemory | "
                    "Select-Object Speed,SMBIOSMemoryType | "
                    "ConvertTo-Json -Compress)"])
        try:
            d = json.loads(out)
            mods = d if isinstance(d, list) else [d]
            if mods:
                speed = int(mods[0].get("Speed") or 0)
                mtype = int(mods[0].get("SMBIOSMemoryType") or 0)
                cls = _SMBIOS_MEM_TYPE.get(mtype, "unknown")
                # channels ~= populated modules (dual-channel boards pair them)
                channels = min(len(mods), 4) or 1
                if speed:
                    gb_s = round(speed * 8 * channels / 1000, 1)
        except Exception:
            pass
    elif _os_family() == "darwin":
        # Apple silicon shares one pool between CPU and GPU; the whole
        # VRAM-vs-RAM split the policy assumes does not apply. Flagged so the
        # policy can refuse rather than guess (fixture P6).
        if platform.machine().startswith("arm"):
            cls, method = "unified", "declared-platform"
    return {"class": cls, "gb_s_estimate": gb_s, "method": method}


# ─────────────────────────────────────────────────────────────────────────────
#  GPUs — multi-GPU correct, baseline measured
# ─────────────────────────────────────────────────────────────────────────────

def _compute_class(name: str) -> str:
    """Coarse capability bucket, enough to decide whether an FP8 build loads."""
    n = (name or "").lower()
    if any(t in n for t in ("h100", "h200", "b100", "b200", "gb200")):
        return "datacenter-fp8"
    if any(t in n for t in ("rtx 50", "rtx 40", "l40", "l4", "ada")):
        return "consumer-fp8"          # Ada+ has native FP8
    if any(t in n for t in ("rtx 30", "a100", "a10", "ampere")):
        return "consumer-bf16"
    if "rtx 20" in n or "turing" in n:
        return "consumer-fp16"
    return "unknown"


def detect_gpus() -> list:
    """Every GPU, in index order, with its measured idle baseline.

    The bug this replaces: `nvidia-smi --query-gpu=name,memory.total
    --format=csv,noheader,nounits` prints one line PER GPU.
    ollama_manager.detect_hardware splits the whole blob on "," and reads
    parts[0]/parts[1], so on a multi-GPU host VRAM parsing is undefined and
    every GPU after the first is invisible. Here each line is one device.
    """
    out = _run(["nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used,driver_version",
                "--format=csv,noheader,nounits"], timeout=20)
    gpus = []
    for line in (out or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
            total = int(parts[2])
            used = int(parts[3])
        except (ValueError, IndexError):
            continue
        gpus.append({
            "index": idx,
            "name": parts[1],
            "vram_total_mib": total,
            # Live reading. NOT the idle floor -- see vram_baseline_mib.
            "vram_used_mib": used,
            # The idle floor is deliberately NOT set here. `memory.used` at an
            # arbitrary moment includes whatever we ourselves have resident:
            # detecting while a model was loaded once recorded a "baseline" of
            # 11120 MiB on a 12282 MiB card, which would have left the policy a
            # ~1 GB budget and refused every placement. Only refresh_baseline()
            # with a verified-idle GPU may write this.
            "vram_baseline_mib": None,
            "vram_baseline_at": None,
            "compute_class": _compute_class(parts[1]),
            "driver": parts[4] if len(parts) > 4 else None,
            "vendor": "nvidia",
        })
    return sorted(gpus, key=lambda g: g["index"])


# Used when the idle floor has never been measured. Conservative on purpose: a
# desktop compositor really does hold ~1 GB, and under-reserving overcommits the
# card, which fails at load time rather than at plan time.
DEFAULT_VRAM_BASELINE_MIB = {"windows": 1024, "darwin": 512, "linux": 256}


# What the desktop is allowed to shrink to. A compositor driving two displays
# does not fit in the old 1 GB default, and being wrong DOWNWARD here is what
# breaks screens -- being wrong upward only costs a seat.
MIN_DISPLAY_RESERVE_MIB = {"windows": 2560, "darwin": 1024, "linux": 512}

# The most of a dedicated card a DESKTOP may plausibly be holding. Above this
# the reading is not describing a compositor, and refresh_display_reserve()
# discards it rather than budgeting against it. Deliberately loose: the failure
# this catches reported 26,463 MiB on a 12,282 MiB card, so a tight bound buys
# nothing and would start rejecting real multi-monitor draws.
MAX_DISPLAY_FRACTION = 0.5

_DISPLAY_CACHE: tuple = (0.0, None)
_DISPLAY_TTL_S = 20.0

# Rejected display readings, newest per GPU index, for anyone asking why the
# budget looks the way it does. Populated by refresh_display_reserve() and
# surfaced through residency_policy.gpu_budgets() into
# /api/residency/status -> budgets[].baseline_rejected.
_DISPLAY_REJECTIONS: dict = {}


def display_rejections() -> dict:
    """Readings discarded as physically impossible, newest per GPU index.

    Empty is the healthy state. A non-empty entry means the WDDM counter is
    reporting something that is not resident VRAM, and the budget below it is
    running on the cached floor instead of a live measurement.
    """
    return {k: dict(v) for k, v in _DISPLAY_REJECTIONS.items()}


def live_display_mib(os_family: str) -> int | None:
    """VRAM held right now by everything that is NOT one of our model servers.

    Windows only for the moment. Note that `nvidia-smi` CANNOT attribute
    per-process VRAM under WDDM -- it reports N/A for every process -- so the
    reading comes from the OS performance counters instead. Any wizard that
    ships to Windows will hit that same wall.

    Returns None when it cannot tell, which the caller reads as "keep the
    cached floor" rather than as zero.
    """
    # Platform guard BEFORE the cache read: keying the cache by value alone
    # let a cached Windows reading leak out of a non-Windows call.
    if os_family != "windows":
        return None
    if os.environ.get("FRIDAY_TESTING") == "1":
        # The one function here that touches the machine, so it is the one that
        # goes quiet under test -- otherwise a plan would depend on whatever the
        # developer happens to have open. Callers monkeypatch this to drive the
        # reserve logic; nothing above it needs a test-only branch.
        return None
    now = time.time()
    stamp, hit = _DISPLAY_CACHE
    if hit is not None and (now - stamp) < _DISPLAY_TTL_S:
        return hit

    # The counter's InstanceName is `pid_1234_luid_...` -- it carries NO process
    # name, so the exclusion has to resolve each PID first. Filtering on the
    # instance string directly matches nothing and silently sums the model
    # servers into the display reserve, which reads as a card several GB larger
    # than it is.
    ps = (
        "$n=@{};Get-Process|ForEach-Object{$n[$_.Id]=$_.ProcessName};"
        "$t=0;(Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage')."
        "CounterSamples|ForEach-Object{"
        "if($_.InstanceName -match 'pid_(\\d+)'){"
        "$p=[int]$Matches[1];$nm=$n[$p];"
        "if($nm -and $nm -notmatch '^(llama-server|ollama|python)$'){"
        "$t+=$_.CookedValue}}};[int]($t/1MB)"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15)
        val = int((out.stdout or "").strip())
    except Exception:
        return None
    if val <= 0:
        return None
    val = max(val, MIN_DISPLAY_RESERVE_MIB.get(os_family, 512))
    globals()["_DISPLAY_CACHE"] = (now, val)
    return val


def effective_baseline_mib(gpu: dict, os_family: str) -> int:
    """The floor to budget against. PURE -- a function of the profile, nothing else.

    Reads `vram_display_reserve_mib` when a sampler has written one, and never
    returns less than the cached idle floor. It does NOT probe: planning has to
    be deterministic and replayable, which is the whole point of the policy
    engine's golden fixtures. `refresh_display_reserve()` does the sampling and
    writes the number in as data; this function only ever reads it.

    Why the field exists at all: the boot-time idle measurement alone was the
    defect that cost a monitor on 2026-08-17. A baseline sampled once, on an
    idle desktop, cannot describe a compositor whose appetite moves with
    monitor count, resolution, and how much browser is open. On the reference
    box the cached floor read 542 MiB while dwm actually held 2,778 MiB -- a
    2.2 GB under-reservation, more than an entire brain seat. The arbiter duly
    planned seats into memory Windows needed to draw the screen.

    A cached constant is wrong on every machine; it just differs in how much.
    """
    measured = gpu.get("vram_baseline_mib")
    floor = (measured if isinstance(measured, int) and measured >= 0
             else DEFAULT_VRAM_BASELINE_MIB.get(os_family, 512))
    reserve = gpu.get("vram_display_reserve_mib")
    # A reserve at or above the card's own capacity is not a large reading, it
    # is a broken one -- the desktop cannot hold the whole card and leave the
    # driver running. refresh_display_reserve() rejects these at the source and
    # says so in the log; this is the same rule applied again on the way OUT, so
    # a profile written by an older build (or hand-edited) cannot drive the
    # budget to zero and refuse every seat in silence. Still pure: the test is a
    # comparison between two fields of the profile it was handed.
    total = gpu.get("vram_total_mib")
    if (isinstance(reserve, int) and isinstance(total, int) and total > 0
            and reserve >= total):
        return floor
    if isinstance(reserve, int) and reserve > floor:
        return reserve
    return floor


def refresh_display_reserve(profile: dict) -> dict:
    """Sample what the desktop is holding NOW and write it into the profile.

    The arbiter calls this before it plans, so the plan plans against the
    screen the owner is actually looking at rather than the one that existed at
    boot. Never lowers a reading below MIN_DISPLAY_RESERVE_MIB -- being wrong
    downward here is what breaks displays; being wrong upward only costs a seat.

    A machine where the probe cannot answer keeps whatever it had, so this is
    strictly additive: no platform is worse off than before it existed.
    """
    fam = (profile.get("os_family")
           or ("windows" if sys.platform.startswith("win") else "linux"))
    live = live_display_mib(fam)
    if live is None:
        return profile

    # ── Physical sanity, because the counter is not bounded by the card ──────
    #
    # `\GPU Process Memory(*)\Dedicated Usage` counts COMMITTED allocations, not
    # what is resident on the device, and WDDM lets a process commit far more
    # than the GPU has. Measured on this box 2026-08-23: Chrome alone reported
    # 25,808 MiB across four counter instances on a 12,282 MiB card, for a total
    # of 26,459 MiB. An earlier sample of 13,831 MiB had already been written
    # into the in-memory profile, which drove `gpu_budgets` to
    # max(0, 12282 - 1024 - 13831) = 0 and produced ten refusals in Settings ->
    # Intelligence while the same page showed 10.1 GB free.
    #
    # The ceiling comes from the PROFILE, not a fresh probe. An earlier version
    # of this guard re-read `nvidia-smi` for a tighter bound; it was tighter and
    # it was wrong, because it judged the profile it was editing against a
    # different machine reading, and under test it rejected a perfectly good
    # 2,778 MiB compositor by comparing it to the developer's real card. A rule
    # that needs the machine to evaluate cannot be exercised without one.
    #
    # MAX_DISPLAY_FRACTION is the judgement: a desktop compositor holding more
    # than half a dedicated GPU is not a large measurement, it is a broken one.
    # Above that we cannot tell what the number means, so we refuse to use it.
    # When the profile does not state a total we cannot judge at all, and an
    # unjudgeable reading is accepted rather than silently dropped.
    #
    # A broken reading is DISCARDED, not scaled. Scaling would invent a number;
    # discarding falls back to the cached idle floor, which is what
    # `live_display_mib` returning None already means. And it is LOUD: silently
    # flooring to something plausible is how this comes back in six months with
    # nobody able to see it happening.
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for g in profile.get("gpus", []):
        idx = g.get("index")
        total = g.get("vram_total_mib")
        ceiling = (int(total * MAX_DISPLAY_FRACTION)
                   if isinstance(total, int) and total > 0 else None)
        if ceiling is not None and live > ceiling:
            rejection = {
                "raw_mib": live,
                "ceiling_mib": ceiling,
                "gpu_total_mib": total,
                "kept_mib": effective_baseline_mib(g, fam),
                "source": "wddm-dedicated-usage-counter",
                "at": stamp,
            }
            _DISPLAY_REJECTIONS[idx] = rejection
            g["vram_display_reserve_rejected"] = rejection
            _log.error(
                "GPU %s display reserve reading discarded as impossible: the "
                "WDDM counter reports %d MiB held by the desktop on a %d MiB "
                "card, above the %d MiB ceiling (%.0f%% of the card). Falling "
                "back to %d MiB. The counter measures committed allocations, "
                "not resident VRAM; a browser can commit more than the card "
                "physically has, so this reading is not usable.",
                idx, live, total, ceiling, MAX_DISPLAY_FRACTION * 100,
                rejection["kept_mib"])
            continue
        g["vram_display_reserve_mib"] = live
        g["vram_display_reserve_at"] = stamp
        g.pop("vram_display_reserve_rejected", None)
        _DISPLAY_REJECTIONS.pop(idx, None)
    return profile


def refresh_baseline(profile: dict, *, assert_idle: bool = False) -> dict:
    """Record each GPU's idle floor.

    `assert_idle` is the caller promising that nothing of ours is resident --
    the Arbiter calls this at boot, before it loads anything. Without that
    promise the live reading is not a baseline and is refused, because a
    poisoned floor is worse than a defaulted one: it is wrong and it is cached.
    """
    if not assert_idle:
        return profile
    live = {g["index"]: g for g in detect_gpus()}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for g in profile.get("gpus", []):
        cur = live.get(g["index"])
        if cur:
            g["vram_baseline_mib"] = cur["vram_used_mib"]
            g["vram_baseline_at"] = stamp
    save(profile)
    return profile


# ─────────────────────────────────────────────────────────────────────────────
#  Disk — free space and a real read rate, for load-time estimates
# ─────────────────────────────────────────────────────────────────────────────

def measure_disk_read_mib_s(sample_path: Path | None = None,
                            target_bytes: int = 512 * 1024 * 1024) -> dict:
    """Sequential read rate, used to estimate model load time.

    Reads a real large file if one is available (an Ollama blob), else writes
    and reads back a temp file. The page-cache state is NOT controlled, so this
    is a warm-ish upper bound -- recorded in `method` rather than pretended
    away, because an over-optimistic rate produces transition timeouts that are
    too tight, which is the failure that matters.
    """
    src = sample_path
    if src is None:
        blobs = Path(os.environ.get(
            "OLLAMA_MODELS", Path.home() / ".ollama" / "models")) / "blobs"
        if blobs.is_dir():
            cands = [p for p in blobs.glob("sha256-*")
                     if p.is_file() and p.stat().st_size > target_bytes]
            if cands:
                src = max(cands, key=lambda p: p.stat().st_size)
    if src is None or not Path(src).exists():
        return {"read_mib_s": None, "method": "unavailable"}
    # perf_counter, not time.time(): time.time()'s resolution on Windows is
    # ~15.6 ms, and a cached 1 MiB read finishes well inside that, so `el`
    # came out exactly 0.0 and the guard below reported "unavailable" — the
    # measurement silently failed on the FASTEST disks. perf_counter is
    # monotonic with ns resolution, so a real read always takes > 0.
    read, t0 = 0, time.perf_counter()
    try:
        with open(src, "rb", buffering=0) as f:
            while read < target_bytes:
                b = f.read(8 * 1024 * 1024)
                if not b:
                    break
                read += len(b)
    except Exception:
        return {"read_mib_s": None, "method": "unavailable"}
    el = time.perf_counter() - t0
    if el <= 0 or not read:
        return {"read_mib_s": None, "method": "unavailable"}
    return {"read_mib_s": round((read / 1048576) / el, 1),
            "method": "sequential-blob-read-warm"}


def detect_disk(measure_rate: bool = False, prior: dict | None = None) -> dict:
    """Free space always; read rate only when asked (it costs seconds)."""
    try:
        free_mib = round(shutil.disk_usage(str(runtime_dir().parent)).free
                         / 1048576)
    except Exception:
        free_mib = 0
    rate = {"read_mib_s": (prior or {}).get("read_mib_s"),
            "method": (prior or {}).get("method", "inherited")}
    if measure_rate or rate["read_mib_s"] is None:
        m = measure_disk_read_mib_s()
        if m["read_mib_s"] is not None:
            rate = m
    return {"free_mib": free_mib, **rate}


# ─────────────────────────────────────────────────────────────────────────────
#  Profile assembly, identity, cache
# ─────────────────────────────────────────────────────────────────────────────

def _profile_id(os_d, cpu, ram, gpus, mem) -> str:
    """Stable over free space and driver patches; changes on real hardware change.

    Deliberately EXCLUDES available RAM, free disk, and the GPU idle baseline --
    those move minute to minute and would make the id useless as a cache key.
    """
    ident = {
        "v": PROFILE_VERSION,
        "os": os_d.get("family"),
        "threads": cpu.get("threads"),
        "cores": cpu.get("physical_cores"),
        "cpu": cpu.get("model"),
        "ram_mib": ram.get("total_mib"),
        "mem_class": mem.get("class"),
        "gpus": [(g["index"], g["name"], g["vram_total_mib"]) for g in gpus],
    }
    blob = json.dumps(ident, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def cache_path() -> Path:
    return runtime_dir() / "residency" / "hardware-profile.json"


def detect(measure_disk_rate: bool = False, prior: dict | None = None) -> dict:
    os_d = detect_os()
    cpu = detect_cpu()
    ram = detect_ram()
    mem = detect_memory_bandwidth()
    gpus = detect_gpus()
    disk = detect_disk(measure_rate=measure_disk_rate,
                       prior=(prior or {}).get("disk"))
    return {
        "profile_version": PROFILE_VERSION,
        "profile_id": _profile_id(os_d, cpu, ram, gpus, mem),
        "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "os": os_d,
        "cpu": cpu,
        "ram": ram,
        "memory_bandwidth": mem,
        "gpus": gpus,
        "disk": disk,
        "os_reserve_mib": OS_RESERVE_MIB.get(os_d["family"], 4096),
    }


def load_cached() -> dict | None:
    try:
        return json.loads(cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def save(profile: dict) -> None:
    """Atomic write; a half-written profile must never be readable."""
    p = cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass          # a profile that cannot be cached is still usable in-memory


# In-process memo. Detection is NOT cheap: detect_cpu() spawns PowerShell and
# detect_gpus() spawns nvidia-smi, so a bare get() costs ~1-4s on Windows.
# `_pick_local_model` consults the profile on every routing decision, which
# turned a sub-millisecond choice into a multi-second one — measured at 4.6s
# per call in the test suite before this memo existed. The disk cache alone did
# not help, because get() re-detected in order to compare identities.
_MEMO: dict = {"at": 0.0, "profile": None}
_MEMO_TTL_S = 60.0


def get(force: bool = False, measure_disk_rate: bool = False) -> dict:
    """The cached profile, re-detected when the hardware identity changed.

    Cheap, mutable fields (free disk, available RAM, GPU idle baseline) are
    refreshed on every call; the expensive disk-rate measurement is inherited
    from the cache unless explicitly requested.

    Memoised for `_MEMO_TTL_S` — hardware does not change second to second, and
    the callers that matter (routing, health) are on hot paths.
    """
    now = time.time()
    if (not force and not measure_disk_rate
            and _MEMO["profile"] is not None
            and (now - _MEMO["at"]) < _MEMO_TTL_S):
        return _MEMO["profile"]

    # The on-disk profile is read even when forcing. `force` means "re-detect
    # now", not "discard what was measured": the GPU idle floor can only be
    # taken at a known-idle moment, so a forced re-detect that dropped it would
    # silently destroy the one reading the VRAM budget depends on — and then
    # persist the loss.
    cached = load_cached()
    fresh = detect(measure_disk_rate=measure_disk_rate, prior=cached)
    if cached and cached.get("profile_id") == fresh["profile_id"]:
        for old, new in zip(cached.get("gpus", []), fresh.get("gpus", [])):
            if old.get("vram_baseline_mib") is not None:
                new["vram_baseline_mib"] = old["vram_baseline_mib"]
                new["vram_baseline_at"] = old.get("vram_baseline_at")
    if cached and cached.get("profile_id") == fresh["profile_id"] and not force:
        # Same machine: keep the recorded detection time and any measured rate,
        # take the live volatile readings.
        cached["ram"] = fresh["ram"]
        cached["disk"]["free_mib"] = fresh["disk"]["free_mib"]
        for old, new in zip(cached.get("gpus", []), fresh.get("gpus", [])):
            # Live usage tracks; the measured idle floor is PRESERVED. Only
            # refresh_baseline(assert_idle=True) may overwrite that, or a
            # detection taken mid-load would silently destroy a good baseline.
            old["vram_used_mib"] = new["vram_used_mib"]
            old.setdefault("vram_baseline_mib", None)
            old.setdefault("vram_baseline_at", None)
        if measure_disk_rate:
            cached["disk"] = fresh["disk"]
        save(cached)
        _MEMO["at"], _MEMO["profile"] = now, cached
        return cached
    save(fresh)
    _MEMO["at"], _MEMO["profile"] = now, fresh
    return fresh


def summary(profile: dict | None = None) -> str:
    """One-line human form, for logs and refusal messages."""
    p = profile or get()
    g = ", ".join("%s %d MiB" % (x["name"], x["vram_total_mib"])
                  for x in p.get("gpus", [])) or "no GPU"
    return "%s | %s (%s threads) | %d MiB RAM (%s) | %s" % (
        p["os"]["family"], p["cpu"]["model"], p["cpu"]["threads"],
        p["ram"]["total_mib"], p["memory_bandwidth"]["class"], g)


# ─────────────────────────────────────────────────────────────────────────────
#  Displays — VRAM the OS needs that nothing was counting
#
#  2026-08-17. Stephen's second monitor vanished from Windows while still
#  showing a stale frame. The cause was an indirect (USB) display driver
#  crashing — `TrgIdd.dll`, device `Trigger 6 External Graphics`, which went to
#  status Error — and NOT an NVIDIA reset: zero TDR/4101 events in twelve hours.
#
#  But the card was at 11,691 MiB used of 12,282 (322 MiB free) after the
#  heartbeat loaded a 9.6 GB model four minutes earlier, and an indirect display
#  driver allocates GPU memory for its framebuffer. Contributing: likely.
#  Proven: no.
#
#  What IS proven is the budgeting gap it exposed. `refresh_baseline` measures
#  `memory.used` ONCE at Arbiter boot and freezes it as the idle floor — it read
#  542 MiB on this machine, against a documented Windows compositor cost of
#  ~1 GB. Nothing counts monitors, nothing counts resolution, nothing notices an
#  indirect adapter, and nothing re-measures when the display setup changes. So
#  the arbiter can plan the card full while the desktop still needs room.
# ─────────────────────────────────────────────────────────────────────────────

# Per-monitor framebuffer cost, generously rounded. A 4K surface at 32bpp is
# ~32 MiB, but the compositor keeps several buffers per output plus overlay and
# scaling surfaces, so the honest per-display figure is far above the naive one.
_DISPLAY_BASE_MIB = 256          # compositor itself, first display included
_DISPLAY_PER_EXTRA_MIB = 192     # each additional attached monitor
_DISPLAY_HIDPI_EXTRA_MIB = 128   # per monitor above 2560 wide
# An indirect/USB display adapter renders on the host GPU and copies out. It
# costs more than a directly attached panel and it is the fragile one.
_DISPLAY_INDIRECT_EXTRA_MIB = 256


def detect_displays() -> dict:
    """Attached monitors and adapters. Best-effort; never raises.

    Returns {count, hidpi, indirect, adapters:[{name,status,indirect}], ok}.
    `ok` is False when an adapter is present but in an error state — which is
    exactly the condition Windows was in after the driver crash.
    """
    out = {"count": 0, "hidpi": 0, "indirect": 0, "adapters": [], "ok": True}
    if platform.system().lower() != "windows":
        return out
    ps = (
        r"$m=@(Get-CimInstance -Namespace root\wmi "
        "WmiMonitorBasicDisplayParams -ErrorAction SilentlyContinue);"
        "$v=@(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue"
        "|Select-Object Name,Status,CurrentHorizontalResolution);"
        "[Console]::Out.Write((ConvertTo-Json @{monitors=$m.Count;video=$v} "
        "-Depth 4 -Compress))"
    )
    try:
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20).stdout.strip()
        data = json.loads(raw) if raw else {}
    except Exception:
        return out
    vids = data.get("video") or []
    if isinstance(vids, dict):
        vids = [vids]
    out["count"] = int(data.get("monitors") or 0)
    for v in vids:
        name = str(v.get("name") or v.get("Name") or "")
        status = str(v.get("status") or v.get("Status") or "")
        width = v.get("currentHorizontalResolution") or v.get(
            "CurrentHorizontalResolution") or 0
        # An indirect display driver has no PCI adapter RAM and a virtual-ish
        # name; the reliable tell on Windows is a non-NVIDIA/AMD/Intel adapter.
        indirect = not any(k in name.lower()
                           for k in ("nvidia", "amd", "radeon", "intel"))
        out["adapters"].append({"name": name, "status": status,
                                "indirect": indirect})
        if indirect:
            out["indirect"] += 1
        if status and status.lower() != "ok":
            out["ok"] = False
        try:
            if int(width or 0) > 2560:
                out["hidpi"] += 1
        except Exception:
            pass
    return out


def display_reserve_mib(displays: dict | None = None) -> int:
    """VRAM to hold back for the desktop, scaled to what is attached.

    This is the number the old code did not have. It replaces a single
    boot-time snapshot with something that grows when he plugs a monitor in.
    """
    d = displays if displays is not None else detect_displays()
    count = max(1, int(d.get("count") or 1))
    mib = _DISPLAY_BASE_MIB
    mib += _DISPLAY_PER_EXTRA_MIB * (count - 1)
    mib += _DISPLAY_HIDPI_EXTRA_MIB * int(d.get("hidpi") or 0)
    mib += _DISPLAY_INDIRECT_EXTRA_MIB * int(d.get("indirect") or 0)
    return int(mib)


def vram_headroom(gpu_index: int = 0) -> dict:
    """Live free VRAM against the display reserve.

    The check that was missing: the plan did arithmetic against its own ceiling
    and nothing ever asked the card what was actually free before committing a
    load. 322 MiB free is not a number a plan should be allowed to reach.
    """
    gpus = detect_gpus()
    gpu = next((g for g in gpus if g.get("index") == gpu_index), None)
    if not gpu:
        return {"ok": True, "reason": "no GPU detected"}
    total = int(gpu.get("vram_total_mib") or 0)
    used = int(gpu.get("vram_used_mib") or 0)
    free = max(0, total - used)
    reserve = display_reserve_mib()
    return {
        "ok": free >= reserve,
        "total_mib": total, "used_mib": used, "free_mib": free,
        "display_reserve_mib": reserve,
        "shortfall_mib": max(0, reserve - free),
    }

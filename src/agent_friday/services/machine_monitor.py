"""
machine_monitor — see the machine while Friday works.

`docs/design/headroom.md` §4.3, §12 Phase 1. A sampler, not a decider — the
same rule `gpu_headroom` states about itself: **it only ever reports.** It
never evicts, kills, or throttles anything (HR7). Two functions:

  sample()   -> one snapshot of the machine, right now. GPU numbers, RAM
                available, free space on the SYSTEM volume (not wherever
                models live), what a foreign tenant is holding on the card,
                and the WDDM shared-usage counter if it can be read.
  verdict()  -> per resource, ok | at_risk | breached, each with a `basis`
                so a caller can render "unknown" honestly rather than a
                guessed green tick (HR1).
  tick()     -> one sample()+verdict() cycle, ALSO dispatching the verdict
                to `residency_arbiter.Arbiter.respond_to_monitor` (§7, §12
                Phase 5) — a dispatch, not a decision: this file still never
                decides what to do with a breach, it hands the reading to
                the one thing in the process allowed to act on it.

ONE nvidia-smi call. `gpu_headroom.gpu_memory()` used to make its own; the
query here is that same call, extended with the four fields §4.3 asks for
(`utilization.gpu, power.draw, power.limit, clocks.sm`), and
`gpu_headroom.gpu_memory()` now reads `gpu_rows()` below instead of shelling
out a second time.

WHAT THIS FILE DOES NOT BUILD. §4.2 of the spec is a full three-level
Headroom Contract — `working` / `away` / `yield`, each with a VRAM-slack
floor and a RAM-available floor. Choosing those numbers, and which level is
the shipped default, is **D1** — Stephen's decision, not made here (see
`headroom_contract.py`'s own docstring, which carries the same line for the
display-reserve half of §4.2). `verdict()` below reports two resources
honestly instead of guessing at D1's numbers:

  * **display** — decided. Reads Phase 0's
    `headroom_contract.resolve_display_reserve()`, the same figure
    `Arbiter.grant()`'s R-DISPLAY-RESERVE check now enforces.
  * **disk_system** — decided. `residency_policy.DISK_FLOOR_MIB` (R8, an
    EXISTING 10 GiB constant) applied to the volume holding `%SystemRoot%`
    rather than the model-store volume. This is not a new number; it is the
    gap named HR5 in the spec — none of R8, the Ollama-pull preflight, or
    the vault-tier disk check watches the system volume, and the incident
    that filled C: to 0 bytes and crashed the live app on 2026-09-04
    (`friday_test_home_*` leaks, not a model) would not have been seen by
    any of them.
  * **vram_slack**, **ram_available** — D1-gated. `basis: "unknown"`, never
    `ok`, never a guessed floor. HR1: a verdict with no basis is not a
    verdict.

The thrash signature (§3.3, §4.3) needs neither number and is built in full:
it is telemetry plus a signature validated against the REPORTED figures in
§3, not a Stephen decision.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

_log = logging.getLogger("friday.machine_monitor")

# Windows: never flash a console window for a probe (the flag the rest of the
# tree guards subprocess calls with; see hardware_profile.py, gpu_headroom.py).
_POPEN_FLAGS = 0x08000000 if sys.platform == "win32" else 0


def _run(cmd, timeout=15):
    """Best-effort subprocess text capture. Never raises.

    A free function so tests can monkeypatch it directly (the same shape
    `hardware_profile._run` and `gpu_headroom.gpu_memory`'s inline subprocess
    call use) rather than mocking `subprocess.run` globally.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, creationflags=_POPEN_FLAGS)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _opt_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _opt_int(s):
    v = _opt_float(s)
    return int(v) if v is not None else None


# ── GPU rows: the ONE nvidia-smi call ───────────────────────────────────────
#
# name/memory.total/memory.used/memory.free are what `gpu_headroom.gpu_memory`
# used to query on its own; utilization.gpu/power.draw/power.limit/clocks.sm
# are the four fields §4.3 adds. One query string, one process spawn, both
# consumers read the same cached rows.
_GPU_QUERY = ("index,name,memory.total,memory.used,memory.free,"
             "utilization.gpu,power.draw,power.limit,clocks.sm")
_GPU_CACHE_TTL_S = 2.0          # matches gpu_headroom's old per-call cache
_gpu_cache: dict = {"ts": 0.0, "rows": None}
_gpu_cache_lock = threading.Lock()


def gpu_rows(*, fresh: bool = False) -> list[dict] | None:
    """Every GPU's live numbers, one `nvidia-smi` call, cached briefly.

    `None` means "cannot verify" — never zero, never silently omitted, the
    same rule `gpu_headroom.gpu_memory`'s docstring already states. This is
    now the ONE place in the tree that shells out to nvidia-smi for live
    numbers; `gpu_headroom.gpu_memory()` reads these rows instead of
    querying a second time.
    """
    now = time.time()
    with _gpu_cache_lock:
        if not fresh and _gpu_cache["rows"] is not None and \
                (now - _gpu_cache["ts"]) < _GPU_CACHE_TTL_S:
            return _gpu_cache["rows"]
    out = _run(["nvidia-smi", "--query-gpu=" + _GPU_QUERY,
               "--format=csv,noheader,nounits"])
    rows = []
    for line in (out or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            idx = int(parts[0])
            total = int(_opt_float(parts[2]) or 0)
            used = int(_opt_float(parts[3]) or 0)
            free = int(_opt_float(parts[4]) or 0)
        except (ValueError, TypeError, IndexError):
            continue
        rows.append({
            "index": idx, "name": parts[1],
            "total_mib": total, "used_mib": used, "free_mib": free,
            # Optional on older drivers / some virtualised cards -- "[N/A]"
            # fails to parse and reads as None, never as 0 (0% util and 0W
            # both mean something very different from "could not read this").
            "util_pct": _opt_int(parts[5]) if len(parts) > 5 else None,
            "power_w": _opt_float(parts[6]) if len(parts) > 6 else None,
            "power_limit_w": _opt_float(parts[7]) if len(parts) > 7 else None,
            "sm_mhz": _opt_int(parts[8]) if len(parts) > 8 else None,
        })
    result = rows or None
    with _gpu_cache_lock:
        _gpu_cache.update({"ts": now, "rows": result})
    return result


def reset_gpu_cache_for_tests():
    with _gpu_cache_lock:
        _gpu_cache.update({"ts": 0.0, "rows": None})


# ── RAM, disk (system volume), foreign VRAM, WDDM shared usage ─────────────

def _ram_available_mib() -> int | None:
    try:
        import psutil
        return round(psutil.virtual_memory().available / 1048576)
    except Exception:
        return None


def _disk_system_free_mib() -> int | None:
    """Free space on the volume holding `%SystemRoot%` — HR5.

    Deliberately independent of `OLLAMA_MODELS` / `runtime_dir()`: the
    2026-09-04 incident that filled C: to 0 bytes and crashed the live app
    was `friday_test_home_*` leaks, not a model, and none of R8
    (`residency_policy.check_disk_headroom`, the model-store volume), the
    Ollama pull preflight (`routes/skills.py`, 15 GB floor, model-store
    volume), or the vault-tier 2 GiB floor watches the system volume at all.
    On non-Windows there is no separate system volume in the same sense, so
    this watches the root of the filesystem instead.
    """
    try:
        root = os.environ.get("SystemRoot") or os.environ.get("windir")
        anchor = Path(root).anchor if root else os.sep
        if not anchor:
            anchor = os.sep
        return round(shutil.disk_usage(anchor).free / 1048576)
    except Exception:
        return None


def disk_system_total_mib() -> int | None:
    """Total size of the volume `_disk_system_free_mib` watches — public,
    unlike its sibling, because it exists only so a caller can draw a bar
    (§8.3's third bar: system disk against the existing `DISK_FLOOR_MIB`).
    `sample()`'s own shape is not extended with this: nothing in §4.3's
    contract needs a total, only the free figure a floor is checked
    against, and adding an unused field to every recorded sample would be
    the wrong module owning a surface concern (`routes/intelligence.py` is
    where §8.3 lives). Same anchor logic as `_disk_system_free_mib`, so the
    two numbers describe the same volume."""
    try:
        root = os.environ.get("SystemRoot") or os.environ.get("windir")
        anchor = Path(root).anchor if root else os.sep
        if not anchor:
            anchor = os.sep
        return round(shutil.disk_usage(anchor).total / 1048576)
    except Exception:
        return None


def _foreign_vram_mib(gpu_row: dict, ours_resident_mib: int) -> int | None:
    """VRAM on this card held by tenants that are not us, or `None`.

    Same arithmetic as `hardware_profile._foreign_occupancy_mib` — `used`
    minus what we know we have resident — computed from the row `gpu_rows()`
    already fetched rather than a second `nvidia-smi` call. Promoted here
    from "fallback only when the WDDM reading is rejected" (its role in
    `hardware_profile.refresh_display_reserve`) to a first-class field of
    every sample, without touching that existing fallback path at all.
    """
    used = gpu_row.get("used_mib")
    total = gpu_row.get("total_mib")
    if not isinstance(used, int) or used < 0:
        return None
    if isinstance(total, int) and total > 0 and used > total:
        return None
    return max(0, used - max(0, int(ours_resident_mib or 0)))


_WDDM_CACHE: dict = {"ts": 0.0, "val": None}
_WDDM_TTL_S = 20.0                          # matches live_display_mib's cache
_wddm_lock = threading.Lock()


def _wddm_shared_mib() -> int | None:
    """System RAM being used as GPU memory — the WDDM paging tell (§2.8).

    Same PowerShell/`Get-Counter` technique `hardware_profile.
    live_display_mib()` already uses, reading a DIFFERENT counter:
    `\\GPU Adapter Memory(*)\\Shared Usage` (memory paged out of the card
    into system RAM) rather than `\\GPU Process Memory(*)\\Dedicated Usage`
    (what the desktop compositor holds on the card). `None` when it cannot
    be read.

    **DISPLAYED ONLY.** Whether this counter is reliable enough to act on is
    U4 in the spec — explicitly unresolved. `verdict()` below reads this
    field for exactly nothing; it exists so a future surface can show it.
    """
    if os.environ.get("FRIDAY_TESTING") == "1":
        return None
    if sys.platform != "win32":
        return None
    now = time.time()
    with _wddm_lock:
        if _WDDM_CACHE["val"] is not None and \
                (now - _WDDM_CACHE["ts"]) < _WDDM_TTL_S:
            return _WDDM_CACHE["val"]
    ps = ("$t=0;(Get-Counter '\\GPU Adapter Memory(*)\\Shared Usage')."
         "CounterSamples|ForEach-Object{$t+=$_.CookedValue};[int]($t/1MB)")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15,
            creationflags=_POPEN_FLAGS)
        val = int((out.stdout or "").strip())
    except Exception:
        return None
    if val < 0:
        return None
    with _wddm_lock:
        _WDDM_CACHE.update({"ts": now, "val": val})
    return val


def reset_wddm_cache_for_tests():
    with _wddm_lock:
        _WDDM_CACHE.update({"ts": 0.0, "val": None})


# ── sample() ─────────────────────────────────────────────────────────────

def sample(*, ours_resident_mib: int = 0) -> dict:
    """One snapshot: `{ts, gpus, ram_available_mib, disk_system_free_mib,
    foreign_vram_mib, wddm_shared_mib}` (§4.3). Never raises; a reading that
    cannot be taken is `None`, which the whole layer treats as "cannot
    verify," never as "plenty" — `gpu_headroom`'s own rule, kept here.

    `ours_resident_mib`: what THIS process knows it has resident (the
    Arbiter's `_ours_resident_mib()`), so `foreign_vram_mib` does not count
    our own seats as a foreign tenant. Defaults to 0 for a standalone call
    with no Arbiter in the process.
    """
    ts = time.time()
    rows = gpu_rows() or []
    gpus = [dict(r) for r in rows]
    foreign = None
    if gpus:
        primary = max(gpus, key=lambda g: g.get("free_mib") or 0)
        foreign = _foreign_vram_mib(primary, ours_resident_mib)
    return {
        "ts": ts,
        "gpus": gpus,
        "ram_available_mib": _ram_available_mib(),
        "disk_system_free_mib": _disk_system_free_mib(),
        "foreign_vram_mib": foreign,
        "wddm_shared_mib": _wddm_shared_mib(),
    }


# ── thrash signature (§3.3, §4.3) ───────────────────────────────────────────

THRASH_WINDOW = 3            # consecutive samples, at the 5s lease cadence
UTIL_THRESHOLD_PCT = 90
POWER_RATIO_MAX = 0.4        # power_w <= 0.4 * power_limit_w
LATENCY_RATIO_MIN = 5.0      # matches provider_health.LATENCY_UNHEALTHY_MULTIPLE


def _util_power_signal(samples: list) -> bool:
    """util_pct >= 90 and power_w <= 0.4 * power_limit_w on every one of the
    last `THRASH_WINDOW` samples, on the busiest GPU each time.

    A single sample meeting this is a proxy, not proof (§3.3: "a legitimately
    memory-bound kernel at low power looks the same for a moment") — hence
    "sustained", not "seen once". Any sample missing a needed field (an older
    driver, a card that does not report power) makes the signal `False`
    rather than guessing, because a false positive here cancels real work.
    """
    if len(samples) < THRASH_WINDOW:
        return False
    for s in samples[-THRASH_WINDOW:]:
        gpus = s.get("gpus") or []
        if not gpus:
            return False
        g = max(gpus, key=lambda x: x.get("used_mib") or 0)
        util, power, limit = (g.get("util_pct"), g.get("power_w"),
                              g.get("power_limit_w"))
        if util is None or power is None or not limit:
            return False
        if not (util >= UTIL_THRESHOLD_PCT and power <= POWER_RATIO_MAX * limit):
            return False
    return True


def thrash_signature(samples: list, *, latency_ratio: float | None = None) -> dict:
    """`ok | at_risk | breached` from the two independent proxies in §3.3.

    `samples` — recent `Sample` dicts, oldest first, ideally at the 5s lease
    cadence; only the trailing `THRASH_WINDOW` are examined.
    `latency_ratio` — a served seat's measured ms/token divided by its
    catalog `probe_ms_per_token` baseline (`residency_catalog.
    baseline_probe_ms_per_token`), averaged over its last three calls. `None`
    means no seat has served recently — a missing signal, not a healthy one.
    `LATENCY_RATIO_MIN` is the same 5x `provider_health.
    LATENCY_UNHEALTHY_MULTIPLE` already uses for exactly this comparison,
    reused rather than re-guessed.

    §4.3's own line reads "either alone is at_risk; both is breached", and
    that describes a SINGLE untrusted reading of either proxy — §3.3 is
    explicit that one hot moment of low-power/high-utilisation "looks the
    same" as a legitimately memory-bound kernel for a moment, which is
    exactly why `_util_power_signal` requires the reading to be SUSTAINED
    (≥3 consecutive samples) before it counts as a signal at all: fewer than
    three matching samples is `ok`, not a lesser signal, because it has not
    yet been distinguished from that false positive (pinned by
    `test_thrash_needs_three_consecutive_samples_not_one`).  Once sustained,
    the util/power proxy has already done the corroboration §4.3's "both"
    describes, so it breaches on its own — this is the Phase 1 acceptance
    reading (§12: the REPORTED fixture is util/power alone, and asserts
    `breached`, not `at_risk`, after three samples). The throughput proxy
    has no such three-sample confirmation built into `latency_ratio` itself
    (a caller could pass a single call's ratio), so it stays the weaker,
    `at_risk`-only signal unless the sustained util/power proxy corroborates
    it, at which point the combination is still `breached`.
    """
    util_power = _util_power_signal(samples)
    latency = latency_ratio is not None and latency_ratio >= LATENCY_RATIO_MIN
    if util_power:
        status = "breached"
    elif latency:
        status = "at_risk"
    else:
        status = "ok"
    return {
        "status": status,
        "basis": "measured" if samples else "unknown",
        "util_power_signal": util_power,
        "latency_signal": latency,
        "latency_ratio": latency_ratio,
        "samples_examined": min(len(samples), THRASH_WINDOW),
        "explanation": (
            "GPU utilisation and throughput look healthy" if status == "ok" else
            "utilisation is pegged while power draw stays low — consistent "
            "with the card thrashing against RAM rather than computing "
            "(the shape measured 2026-09-04: 100% utilisation, 51 of 200W)"
            if util_power and not latency else
            "a served model's generation speed is %.1fx its measured "
            "baseline — consistent with paging" % (latency_ratio or 0)
            if latency and not util_power else
            "both the utilisation/power proxy and the throughput proxy "
            "agree the card is thrashing, not merely busy"
        ),
    }


# ── verdict() ────────────────────────────────────────────────────────────

_recent_samples: deque = deque(maxlen=THRASH_WINDOW)
_recent_lock = threading.Lock()


def reset_thrash_history_for_tests():
    with _recent_lock:
        _recent_samples.clear()


def _verdict_display(sample_: dict, profile: dict | None) -> dict:
    gpus = sample_.get("gpus") or []
    if not gpus:
        return {"status": "unknown", "basis": "unknown",
               "explanation": "no GPU detected to check the display reserve "
                              "against."}
    g = max(gpus, key=lambda x: x.get("free_mib") or 0)
    free = g.get("free_mib")
    if free is None:
        return {"status": "unknown", "basis": "unknown",
               "explanation": "GPU free memory could not be read."}
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services.headroom_contract import (
            resolve_display_reserve)
        prof = profile if profile is not None else hwp.get()
        reserve = resolve_display_reserve(prof)
    except Exception as e:
        return {"status": "unknown", "basis": "unknown",
               "explanation": "could not resolve the display reserve: %s"
                              % e}
    breached = free < reserve["mib"]
    return {
        "status": "breached" if breached else "ok",
        "basis": "measured",
        "free_mib": free, "reserve_mib": reserve["mib"],
        "reserve_basis": reserve["basis"],
        "explanation": (
            "%d MiB free against a %d MiB display reserve (%s)"
            % (free, reserve["mib"],
               "short" if breached else "room to spare")),
    }


def _verdict_disk_system(sample_: dict) -> dict:
    free = sample_.get("disk_system_free_mib")
    if free is None:
        return {"status": "unknown", "basis": "unknown",
               "explanation": "could not read free space on the system "
                              "volume."}
    try:
        from agent_friday.services.residency_policy import DISK_FLOOR_MIB
    except Exception:
        DISK_FLOOR_MIB = 10 * 1024
    breached = free < DISK_FLOOR_MIB
    return {
        "status": "breached" if breached else "ok",
        "basis": "measured",
        "free_mib": free, "floor_mib": DISK_FLOOR_MIB,
        "explanation": (
            "%d MiB free on the system volume against a %d MiB floor (%s) "
            "-- the pagefile and ~/.friday live there whatever "
            "OLLAMA_MODELS points at (HR5)"
            % (free, DISK_FLOOR_MIB,
               "short" if breached else "room to spare")),
    }


def disk_system_verdict(sample_: dict) -> dict:
    """The `disk_system` resource alone, without going through `verdict()`.

    Used by `Arbiter.grant()`'s R-DISK-SYSTEM admission check (headroom.md
    §7, §12 Phase 5). A DELIBERATELY separate entry point from `verdict()`
    rather than `verdict(sample_)["disk_system"]`: `verdict()` also
    advances the shared thrash-history window and rate-capped logging for
    all four resources on every call, and a caller that only wants one
    resource, on the hot admission path of every load, should not pay (or
    trigger side effects for) the other three every time.
    """
    return _verdict_disk_system(sample_)


# Rate-capped, per resource, the same pattern
# `hardware_profile._log_rejection` uses (2026-09-01: 1,038 identical
# rejection lines in one day from that sibling pattern before it was
# capped) -- a repeated breached verdict must not drown the log.
_LOG_INTERVAL_S = 3600.0
_verdict_log_state: dict = {}
_verdict_log_lock = threading.Lock()


def _log_verdict(resource: str, status: str, msg: str, *args):
    if status not in ("at_risk", "breached"):
        return
    now = time.time()
    with _verdict_log_lock:
        last, suppressed = _verdict_log_state.get(resource, (0.0, 0))
        if last and (now - last) < _LOG_INTERVAL_S:
            _verdict_log_state[resource] = (last, suppressed + 1)
            return
        _verdict_log_state[resource] = (now, 0)
    if suppressed:
        msg += (" (%d identical verdict%s suppressed since the last report)"
               % (suppressed, "" if suppressed == 1 else "s"))
    level = _log.error if status == "breached" else _log.warning
    level(msg, *args)


def reset_verdict_log_for_tests():
    with _verdict_log_lock:
        _verdict_log_state.clear()


def verdict(sample_: dict, contract=None, *, profile: dict | None = None,
           latency_ratio: float | None = None,
           _track_history: bool = True) -> dict:
    """Per resource: `ok | at_risk | breached`, each with a `basis`.

    `contract` is accepted for the future full Headroom Contract (D1) and is
    NOT read here — see the module docstring. `display` and `disk_system`
    are decided today; `vram_slack` and `ram_available` are D1-gated and
    report `basis: "unknown"` rather than a guessed floor (HR1).

    Advances the module's rolling thrash-history window by default (three
    consecutive `verdict()` calls, at the loop's own cadence, is how the
    signature actually fires) — the loop's own `tick()` is meant to be the
    only writer of that shared window. Pass `_track_history=False` for a
    read-only caller (a page poll, which can run far faster than the loop's
    cadence): it scores against the EXISTING window with `sample_` appended
    as a transient peek, so the answer still reflects the real rolling state
    without a duplicate-riddled window of its own.
    """
    out = {
        "display": _verdict_display(sample_, profile),
        "disk_system": _verdict_disk_system(sample_),
        "vram_slack": {
            "status": "unknown", "basis": "unknown",
            "explanation": "the VRAM-slack floor is part of the Headroom "
                           "Contract's working/away/yield levels -- D1, "
                           "Stephen's decision, not made. Not reported as "
                           "ok or breached.",
        },
        "ram_available": {
            "status": "unknown", "basis": "unknown",
            "explanation": "the RAM-available floor is the same D1 "
                           "decision.",
        },
    }
    if _track_history:
        with _recent_lock:
            _recent_samples.append(sample_)
            history = list(_recent_samples)
    else:
        with _recent_lock:
            history = list(_recent_samples)
        if not history or history[-1] is not sample_:
            history = (history + [sample_])[-THRASH_WINDOW:]
    out["thrash"] = thrash_signature(history, latency_ratio=latency_ratio)

    for resource, v in out.items():
        _log_verdict(resource, v.get("status", "unknown"),
                     "machine_monitor: %s %s -- %s",
                     resource, v.get("status"), v.get("explanation"))
    return out


# ── last sample, for anyone polling rather than sampling live ─────────────

_last_sample: dict | None = None
_last_sample_lock = threading.Lock()


def set_last_sample(s: dict) -> None:
    global _last_sample
    with _last_sample_lock:
        _last_sample = s


def last_sample() -> dict | None:
    with _last_sample_lock:
        return dict(_last_sample) if _last_sample is not None else None


def last_sample_age_s() -> float | None:
    with _last_sample_lock:
        if _last_sample is None:
            return None
        ts = _last_sample.get("ts")
    return None if ts is None else max(0.0, time.time() - ts)


def reset_last_sample_for_tests():
    global _last_sample
    with _last_sample_lock:
        _last_sample = None


# ── the loop: sample at boot, 60s at rest, 5s under a held lease ──────────
#
# No existing background loop samples the display reserve on a real timer --
# `refresh_display_reserve` runs only when something calls `compute_plan` /
# `preview` / `grant`, which is on-demand, not a tick. This is the one new
# loop this phase adds; it self-adjusts its own cadence by asking the
# Arbiter whether a lease is held, rather than the Arbiter needing to know
# the monitor exists.

def _cadence_s() -> float:
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if arb is not None and getattr(arb, "lease", None) is not None:
            return 5.0
    except Exception:
        pass
    return 60.0


def _ours_resident_mib() -> int:
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if arb is not None:
            return arb._ours_resident_mib()
    except Exception:
        pass
    return 0


def tick() -> dict:
    """One sample-and-verdict cycle, recorded as the last sample.

    Also DISPATCHES the verdict to the Arbiter (headroom.md §7, §12 Phase
    5) — a dispatch, not a decision: this module still only ever reports
    (HR7's line about `gpu_headroom` applies here too). The reading is
    handed to `residency_arbiter.Arbiter.respond_to_monitor`, the one place
    in the process that owns Friday's leases and processes and is allowed
    to act on what this function measured; `machine_monitor` itself makes
    no eviction, cancellation or termination call anywhere in this file.
    A no-op when no Arbiter governs this process (tests,
    `FRIDAY_NO_ARBITER=1`) or when nothing is leased.
    """
    s = sample(ours_resident_mib=_ours_resident_mib())
    set_last_sample(s)
    v = None
    try:
        v = verdict(s)      # advances thrash history; logs a breach, if any
    except Exception as e:
        _log.warning("machine_monitor: verdict failed on a fresh sample: %s",
                     e)
    if v is not None:
        try:
            from agent_friday.services.residency_arbiter import get_arbiter
            arb = get_arbiter()
            if arb is not None:
                arb.respond_to_monitor(s, v)
        except Exception as e:
            _log.warning("machine_monitor: intrusion-response dispatch "
                         "failed on a fresh sample: %s", e)
    return s


def loop(stop_event: threading.Event) -> None:
    """Sample at boot, then on a cadence that tightens to 5s under a lease
    and relaxes to 60s at rest (§4.3). Runs until `stop_event` is set."""
    try:
        tick()
    except Exception as e:
        _log.warning("machine_monitor: initial sample failed: %s", e)
    while not stop_event.is_set():
        if stop_event.wait(_cadence_s()):
            break
        try:
            tick()
        except Exception as e:
            _log.warning("machine_monitor: sample failed: %s", e)


_loop_stop_event: threading.Event | None = None


def start_loop() -> threading.Thread | None:
    """Start the sampling loop on a daemon thread. Inert under
    `FRIDAY_TESTING=1`, the same guard every other background loop in
    `server.py` uses -- a test run must not spawn a live nvidia-smi/
    PowerShell polling thread nobody asked for."""
    global _loop_stop_event
    if os.environ.get("FRIDAY_TESTING") == "1":
        return None
    _loop_stop_event = threading.Event()
    t = threading.Thread(target=loop, args=(_loop_stop_event,),
                         name="machine-monitor", daemon=True)
    t.start()
    return t


def stop_loop() -> None:
    if _loop_stop_event is not None:
        _loop_stop_event.set()

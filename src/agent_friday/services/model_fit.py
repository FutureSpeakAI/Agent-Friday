"""Will this file run here, how well, and why: the arithmetic behind the
Models screen's verdicts, before a model is measured.

The residency planner is the authority for anything that has been measured
on this machine; its three axes (`residency_policy.verdicts`) never say
"ready" from nothing. This module answers the question the planner cannot:
a file that is not here yet. It sizes the seat from the file's real bytes
and the model's attention layout, places it against the same budgets the
planner uses, and labels every number by its basis: `measured` when a
measurement row exists for this machine, `published` when the maker gave a
figure for this card, otherwise `about`, from a bandwidth formula that the
machine's own measurements calibrate.

No parameter-count table. A ternary packing, a k-quant and an MXFP4 file
are all sized from their bytes; nothing is guessed from a name.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agent_friday.core import runtime_dir
from agent_friday.services import residency_policy as rp

COMPUTE_MIB = {512: 400, 2048: 1200}      # llama.cpp compute buffer by -ub
KV_BYTES = {"f16": 2.0, "bf16": 2.0, "q8_0": 1.0625, "q4_0": 0.5625}
MIN_GPU_LAYER_FRACTION = 0.375              # below this, the CPU tier is faster than a split

# Decode is memory-bandwidth-bound: each token streams the weights once. The
# figures are GB/s per card family, matched by substring, and are only the
# first guess; the machine's own measurement calibrates them (see
# calibration_factor). Sources: the makers' published specifications.
GPU_BANDWIDTH_GB_S = [
    ("rtx 5090", 1792), ("rtx 5080", 960), ("rtx 5070 ti", 896), ("rtx 5070", 672), ("rtx 5060", 448),
    ("rtx 4090", 1008), ("rtx 4080", 717), ("rtx 4070 ti", 504), ("rtx 4070", 504), ("rtx 4060 ti", 288),
    ("rtx 4060", 272), ("rtx 4050", 192), ("rtx 3090", 936), ("rtx 3080", 760), ("rtx 3070", 448),
    ("rtx 3060", 360), ("rtx 3050", 224), ("l4", 300), ("a100", 1555), ("h100", 3350),
    ("rx 7900 xtx", 960), ("rx 7900 xt", 800), ("rx 7800 xt", 624), ("rx 7700 xt", 432),
    ("rx 7600", 288), ("rx 9070", 640), ("arc a770", 560), ("arc b580", 456),
]
GPU_EFFICIENCY = 0.55          # fraction of peak bandwidth a decode step achieves, uncalibrated
CPU_EFFICIENCY = 0.70          # what the community AVX2 kernels reached for Bonsai 2
APPLE_USABLE_FRACTION = 0.75


# ── sizing ──────────────────────────────────────────────────────────────────

def kv_layout_from_header(meta: dict, arch: str | None) -> dict | None:
    """The attention layout the KV cache depends on, from GGUF header keys.

    Hybrid models (Qwen 3.5/3.8 and Bonsai 2) declare `full_attention_interval`;
    only every n-th block holds a KV cache. Sliding-window families (Gemma)
    declare a window and a pattern; their windowed layers cost the window, not
    the context. Everything else is full attention on every block.
    """
    if not arch:
        return None
    g = lambda k: meta.get("%s.%s" % (arch, k))  # noqa: E731
    blocks = g("block_count")
    kv_heads = g("attention.head_count_kv") or g("attention.head_count")
    dk = g("attention.key_length")
    dv = g("attention.value_length")
    if not dk or not dv:
        emb, heads = g("embedding_length"), g("attention.head_count")
        if emb and heads:
            dk = dv = int(emb / heads)
    if not (blocks and kv_heads and dk and dv):
        return None
    out = {"blocks": int(blocks), "kv_heads": int(kv_heads), "head_dim_k": int(dk),
           "head_dim_v": int(dv), "full_attention_layers": int(blocks),
           "window": None, "window_layers": 0}
    interval = g("full_attention_interval")
    if interval and int(interval) > 1:
        out["full_attention_layers"] = int(blocks) // int(interval)
    window = g("attention.sliding_window")
    pattern = g("attention.sliding_window_pattern")
    if window and pattern and int(pattern) > 1:
        out["full_attention_layers"] = int(blocks) // int(pattern)
        out["window_layers"] = int(blocks) - out["full_attention_layers"]
        out["window"] = int(window)
    return out


def kv_mib(layout: dict | None, num_ctx: int, kv_type: str = "q8_0") -> float | None:
    if not layout:
        return None
    b = KV_BYTES.get(kv_type, 2.0)
    per_tok = layout["kv_heads"] * (layout["head_dim_k"] + layout["head_dim_v"]) * b
    full = layout["full_attention_layers"] * per_tok * num_ctx
    windowed = layout.get("window_layers", 0) * per_tok * min(num_ctx, layout.get("window") or num_ctx)
    return (full + windowed) / 1048576.0


def seat_mib(file_bytes: int, layout: dict | None, num_ctx: int, *, kv_type="q8_0",
             ub=512, mmproj_bytes: int = 0) -> dict:
    """The seat, as a dict of its parts, so "Why?" can show them."""
    weights = file_bytes / 1048576.0
    kv = kv_mib(layout, num_ctx, kv_type)
    comp = COMPUTE_MIB.get(ub, 400)
    mm = mmproj_bytes / 1048576.0 if mmproj_bytes else 0.0
    parts = {"weights_mib": round(weights), "kv_mib": round(kv) if kv is not None else None,
             "compute_mib": comp, "mmproj_mib": round(mm), "num_ctx": num_ctx, "kv_type": kv_type}
    total = weights + (kv or 0.0) + comp + mm
    parts["total_mib"] = round(total)
    parts["kv_basis"] = "header" if kv is not None else "unknown (no attention layout; KV not counted)"
    return parts


# ── budgets ─────────────────────────────────────────────────────────────────

def budgets(profile: dict) -> dict:
    gpus = rp.gpu_budgets(profile)
    ram = rp.ram_budget(profile)
    unified = ((profile.get("memory_bandwidth") or {}).get("class") == "unified")
    if unified and not gpus:
        total = int((profile.get("ram") or {}).get("total_mib") or 0)
        gpus = [{"index": 0, "name": "unified memory", "total_mib": total, "baseline_mib": 0,
                 "available_mib": int(total * APPLE_USABLE_FRACTION) - 1500, "unified": True}]
    return {"gpus": gpus, "ram": ram, "unified": unified}


def what_if(profile: dict, *, vram_total_mib: int | None = None,
            ram_total_mib: int | None = None, gpu_name: str | None = None) -> dict:
    """A copy of the profile with a pretend card or RAM figure. Local
    arithmetic only; the result carries `simulated: True`."""
    p = json.loads(json.dumps(profile))
    if vram_total_mib is not None:
        if not p.get("gpus"):
            p["gpus"] = [{"index": 0, "name": gpu_name or "pretend GPU", "vram_total_mib": 0,
                          "vram_used_mib": 0, "vram_baseline_mib": 512, "vendor": "pretend",
                          "compute_class": "unknown"}]
        p["gpus"][0]["vram_total_mib"] = int(vram_total_mib)
        if gpu_name:
            p["gpus"][0]["name"] = gpu_name
        p["gpus"][0].pop("vram_display_reserve_mib", None)
    if ram_total_mib is not None:
        p.setdefault("ram", {})["total_mib"] = int(ram_total_mib)
        p["ram"]["available_mib"] = int(ram_total_mib) // 2
    p["simulated"] = True
    return p


# ── speed ───────────────────────────────────────────────────────────────────

def calibration_path() -> Path:
    return runtime_dir() / "residency" / "calibration.json"


def calibration_factor() -> dict:
    """measured ÷ estimated across the models this machine has measured, or
    1.0 with basis `uncalibrated`."""
    try:
        d = json.loads(calibration_path().read_text(encoding="utf-8"))
        if d.get("factor"):
            return {"factor": float(d["factor"]), "basis": "calibrated",
                    "from": d.get("from"), "measured_at": d.get("measured_at")}
    except Exception:
        pass
    return {"factor": 1.0, "basis": "uncalibrated", "from": None}


def record_calibration(model_id: str, measured_tok_s: float, estimated_tok_s: float) -> dict:
    """One measured run corrects every "about" speed on this machine."""
    if not measured_tok_s or not estimated_tok_s:
        return calibration_factor()
    factor = measured_tok_s / estimated_tok_s
    try:
        prev = calibration_factor()
        if prev["basis"] == "calibrated":
            factor = 0.5 * factor + 0.5 * prev["factor"]
        calibration_path().parent.mkdir(parents=True, exist_ok=True)
        calibration_path().write_text(json.dumps({
            "factor": round(factor, 3), "from": model_id,
            "measured_tok_s": measured_tok_s, "estimated_tok_s": estimated_tok_s,
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    except Exception:
        pass
    return calibration_factor()


def gpu_bandwidth_gb_s(name: str | None) -> int | None:
    n = (name or "").lower()
    for key, bw in GPU_BANDWIDTH_GB_S:
        if key in n:
            return bw
    return None


def decode_estimate(file_bytes: int, profile: dict, *, gpu_fraction: float = 1.0,
                    gpu_name: str | None = None, published: float | None = None,
                    measured: float | None = None) -> dict:
    """Tokens per second with its basis. `gpu_fraction` is the share of the
    weights on the card (1.0 full offload, 0.0 CPU-only)."""
    if measured:
        return {"tok_s": round(measured, 1), "basis": "measured"}
    if published:
        return {"tok_s": round(published, 1), "basis": "published"}
    gb = max(0.1, file_bytes / 1e9)
    bw_cpu = float((profile.get("memory_bandwidth") or {}).get("gb_s_estimate") or 0) or 40.0
    cpu_tok_s = CPU_EFFICIENCY * bw_cpu / gb
    cal = calibration_factor()
    if gpu_fraction <= 0:
        return {"tok_s": round(cpu_tok_s * cal["factor"], 1), "basis": "about",
                "formula": "%.2f x %.0f GB/s / %.1f GB (processor)" % (CPU_EFFICIENCY, bw_cpu, gb),
                "calibration": cal}
    bw_gpu = gpu_bandwidth_gb_s(gpu_name)
    if bw_gpu is None:
        return {"tok_s": None, "basis": "unknown",
                "note": "no bandwidth figure for %s; measure it after install" % (gpu_name or "this card")}
    gpu_tok_s = GPU_EFFICIENCY * bw_gpu / gb
    if gpu_fraction >= 1.0:
        tok_s = gpu_tok_s
        formula = "%.2f x %d GB/s / %.1f GB" % (GPU_EFFICIENCY, bw_gpu, gb)
    else:
        tok_s = 1.0 / (gpu_fraction / gpu_tok_s + (1 - gpu_fraction) / cpu_tok_s)
        formula = "1 / (%.2f/%.0f + %.2f/%.1f): %d%% of the weights on the card" % (
            gpu_fraction, gpu_tok_s, 1 - gpu_fraction, cpu_tok_s, int(gpu_fraction * 100))
    return {"tok_s": round(tok_s * cal["factor"], 1), "basis": "about", "formula": formula,
            "calibration": cal}


# ── the verdict ─────────────────────────────────────────────────────────────

def fit(file_bytes: int, profile: dict, *, layout: dict | None = None, role: str = "interactive_brain",
        context_cap: int | None = None, mmproj_bytes: int = 0, kv_type: str = "q8_0",
        pinned_vram_mib: int = 0, pinned_ram_mib: int = 0, overhead_tokens: int | None = None,
        gpu_name: str | None = None, published_tok_s: float | None = None,
        measured_tok_s: float | None = None, blocks: int | None = None) -> dict:
    """Place one file beside what is already resident and say how it runs.

    Returns `{verdict, context, seat, speed, placement, why, shortfall_mib}`
    with `verdict` one of `runs_well`, `tight`, `partial`, `wont_fit`.
    Every number in `why` is the arithmetic the screen shows under "Why?".
    """
    b = budgets(profile)
    ram = b["ram"]
    if overhead_tokens is None:
        try:
            from agent_friday.services import context_budget
            overhead_tokens = context_budget.overhead_tokens()
        except Exception:
            overhead_tokens = 25000
    want = overhead_tokens + rp.ROOM_TARGET.get(role, rp.MIN_CONVERSATION_ROOM)
    floor = overhead_tokens + rp.MIN_CONVERSATION_ROOM
    cap = context_cap or rp.CONTEXT_LADDER[-1]
    rungs = [c for c in rp.CONTEXT_LADDER if c <= cap] or [rp.CONTEXT_LADDER[0]]
    ub = 2048 if (b["gpus"] and b["gpus"][0]["available_mib"] >= 9000) else 512
    why = {"overhead_tokens": overhead_tokens, "context_wanted": want, "context_floor": floor,
           "context_cap": cap, "ram_budget_mib": ram.get("available_target_mib"),
           "pinned_vram_mib": pinned_vram_mib, "pinned_ram_mib": pinned_ram_mib,
           "gpus": b["gpus"], "ub": ub}
    card = b["gpus"][0] if b["gpus"] else None
    gpu_name = gpu_name or (card or {}).get("name")
    out = {"verdict": "wont_fit", "context": None, "seat": None, "speed": None,
           "placement": None, "why": why, "shortfall_mib": None}
    ram_avail = int(ram.get("available_target_mib") or 0) - pinned_ram_mib

    # 1. Full offload on the largest card, at the largest rung that fits.
    if card:
        avail = int(card["available_mib"]) - pinned_vram_mib
        why["vram_available_mib"] = avail
        # The largest rung that RUNS WELL wins over the largest that merely
        # fits: a seat squeezed in at 64K with 350 MiB spare is a worse
        # answer than the same model at 32K with headroom, and the user can
        # still ask for more context knowing it is tight.
        # The compute buffer is a serving flag, not a property of the model:
        # the large one (-ub 2048) buys prompt speed, and a seat that only
        # fits with the small one is served that way, as the tier rules do.
        tight = None
        for c, ub_try in ((c, u) for c in sorted(rungs, reverse=True) for u in sorted({ub, 512}, reverse=True)):
            s = seat_mib(file_bytes, layout, c, kv_type=kv_type, ub=ub_try, mmproj_bytes=mmproj_bytes)
            if s["total_mib"] > avail:
                continue
            spare = avail - s["total_mib"]
            # Runs well: a real turn's worth of context (the role's target,
            # or at least 32K, which holds the overhead and a long
            # conversation) with headroom left on the card. Anything that
            # fits only at the floor rung, or with nothing to spare, is
            # tight: it works, and the user should know it is close.
            good_ctx = c >= want or c >= 32768
            verdict = "runs_well" if (good_ctx and c >= floor and spare >= 0.08 * avail) else "tight"
            placed = dict(verdict=verdict, context=c, seat=s,
                          placement={"device": "gpu:%d" % card["index"], "n_gpu_layers": "all", "spare_mib": spare,
                                     "ub": ub_try},
                          speed=decode_estimate(file_bytes, profile, gpu_fraction=1.0, gpu_name=gpu_name,
                                                published=published_tok_s, measured=measured_tok_s))
            fits_at = "%d tokens on %s with %d MiB spare" % (c, card["name"], spare)
            if verdict == "runs_well":
                out.update(placed)
                why["fits_at"] = fits_at
                if tight:
                    why["also_fits_at"] = tight[1] + " (tight)"
                return out
            if tight is None:
                tight = (placed, fits_at)
        if tight:
            out.update(tight[0])
            why["fits_at"] = tight[1]
            return out
        # 2. Partial offload: as many layers as fit beside the floor-rung cache.
        c = rungs[0]
        s = seat_mib(file_bytes, layout, c, kv_type=kv_type, ub=512, mmproj_bytes=0)
        room_for_weights = avail - (s["kv_mib"] or 0) - s["compute_mib"]
        frac = max(0.0, min(1.0, room_for_weights / s["weights_mib"])) if s["weights_mib"] else 0.0
        host_mib = s["weights_mib"] * (1 - frac)
        if frac >= MIN_GPU_LAYER_FRACTION and host_mib <= ram_avail:
            layers = int(frac * blocks) if blocks else None
            out.update(verdict="partial", context=c, seat=s,
                       placement={"device": "gpu:%d+cpu" % card["index"], "gpu_fraction": round(frac, 2),
                                  "n_gpu_layers": layers, "host_mib": round(host_mib)},
                       speed=decode_estimate(file_bytes, profile, gpu_fraction=frac, gpu_name=gpu_name))
            why["fits_at"] = "%d%% of the weights on %s, the rest in RAM" % (int(frac * 100), card["name"])
            return out
        out["shortfall_mib"] = max(0, s["total_mib"] - avail)
    # 3. CPU only.
    c = rungs[0]
    s = seat_mib(file_bytes, layout, c, kv_type="f16", ub=512, mmproj_bytes=0)
    why["ram_available_mib"] = ram_avail
    if s["total_mib"] <= ram_avail and _cpu_ok(profile):
        for cc in sorted(rungs, reverse=True):
            ss = seat_mib(file_bytes, layout, cc, kv_type="f16", ub=512)
            if ss["total_mib"] <= ram_avail:
                s, c = ss, cc
                break
        out.update(verdict="tight" if not card else "partial", context=c, seat=s,
                   placement={"device": "cpu", "n_gpu_layers": 0, "spare_mib": ram_avail - s["total_mib"]},
                   speed=decode_estimate(file_bytes, profile, gpu_fraction=0.0))
        if not card:
            out["verdict"] = "runs_well" if c >= want and out["speed"].get("tok_s", 0) and out["speed"]["tok_s"] >= 8 else "tight"
        why["fits_at"] = "%d tokens in processor RAM" % c
        return out
    out["shortfall_mib"] = out["shortfall_mib"] or max(0, s["total_mib"] - ram_avail)
    why["fits_at"] = "nowhere: needs %d MiB more" % out["shortfall_mib"]
    return out


def _cpu_ok(profile: dict) -> bool:
    flags = (profile.get("cpu") or {}).get("flags") or {}
    if flags and not flags.get("avx2") and (profile.get("os") or {}).get("family") != "darwin":
        return False
    return True


def what_would_i_need(file_bytes: int, layout: dict | None, num_ctx: int, *, mmproj_bytes=0,
                      os_family: str = "windows") -> dict:
    """VRAM, RAM and disk a file needs to run well at `num_ctx` on a card."""
    s = seat_mib(file_bytes, layout, num_ctx, kv_type="q8_0", ub=2048, mmproj_bytes=mmproj_bytes)
    reserve = {"windows": 2560, "darwin": 1024, "linux": 512}.get(os_family, 1024) + rp.VRAM_RESERVE_MIB
    return {"seat": s, "vram_total_mib": s["total_mib"] + reserve, "display_and_reserve_mib": reserve,
            "ram_total_mib": {"windows": 6144, "linux": 4096, "darwin": 4096}.get(os_family, 4096) + 1500 + 1024,
            "disk_mib": round(file_bytes / 1048576) + rp.DISK_FLOOR_MIB}

"""Six fixture profiles and the catalog they are planned against.

P1 is the measured reference instance. P2-P6 are declared hardware; their model
VRAM figures are P1's measurements carried across, which is sound for weights
and INFERRED for KV. Every plan for P2-P6 is derived from the rules in
docs/design/residency-policy.md, not chosen.

Model facts (params, quantization, capabilities) are real, read from the daemon
on 2026-08-14. Measurements are the medians of 5 warm runs recorded in
services/residency_catalog.SEED_MEASUREMENTS.
"""
from __future__ import annotations

from agent_friday.services import residency_catalog as rc


def _profile(pid, family, threads, cores, ram_mib, gpus, mem_class,
             gb_s, free_mib=200 * 1024, read_mib_s=427.0, cpu="generic"):
    return {
        "profile_version": 1,
        "profile_id": pid,
        "detected_at": "2026-08-14T00:00:00",
        "os": {"family": family, "version": "fixture", "release": "fixture"},
        "cpu": {"threads": threads, "physical_cores": cores, "model": cpu},
        "ram": {"total_mib": ram_mib, "available_mib": ram_mib // 2},
        "memory_bandwidth": {"class": mem_class, "gb_s_estimate": gb_s,
                             "method": "declared-fixture"},
        "gpus": gpus,
        "disk": {"free_mib": free_mib, "read_mib_s": read_mib_s,
                 "method": "declared-fixture"},
        "os_reserve_mib": 6144 if family == "windows" else 4096,
    }


def _gpu(index, name, vram, baseline, klass="consumer-fp8"):
    return {"index": index, "name": name, "vram_total_mib": vram,
            "vram_used_mib": baseline, "vram_baseline_mib": baseline,
            "vram_baseline_at": "2026-08-14T00:00:00",
            "compute_class": klass, "driver": "610.88", "vendor": "nvidia"}


# ── P1 — the measured reference instance ─────────────────────────────────────
P1 = _profile(
    "p1-reference", "windows", 16, 8, 32620,
    [_gpu(0, "NVIDIA GeForce RTX 4070", 12282, 1261)],
    "ddr4", 42.7, free_mib=21083, read_mib_s=427.0,
    cpu="Intel(R) Core(TM) i7-10700F CPU @ 2.90GHz")

# ── P2 — 8 GB VRAM / 16 GB RAM laptop ────────────────────────────────────────
P2 = _profile(
    "p2-laptop-8gb", "windows", 16, 8, 16384,
    [_gpu(0, "NVIDIA GeForce RTX 4060 Laptop GPU", 8188, 900)],
    "ddr5", 76.8, free_mib=120 * 1024)

# ── P3 — 24 GB GPU / 64 GB desktop ───────────────────────────────────────────
P3 = _profile(
    "p3-desktop-24gb", "linux", 32, 16, 65536,
    [_gpu(0, "NVIDIA GeForce RTX 4090", 24564, 400)],
    "ddr5", 89.6, free_mib=900 * 1024, read_mib_s=6800.0)

# ── P4 — asymmetric dual GPU, 24 GB + 12 GB ──────────────────────────────────
P4 = _profile(
    "p4-dual-asymmetric", "linux", 32, 16, 65536,
    [_gpu(0, "NVIDIA GeForce RTX 4090", 24564, 400),
     _gpu(1, "NVIDIA GeForce RTX 4070", 12282, 300)],
    "ddr5", 89.6, free_mib=900 * 1024, read_mib_s=6800.0)

# ── P5 — CPU-only mini PC, 32 GB ─────────────────────────────────────────────
P5 = _profile(
    "p5-cpu-only", "linux", 16, 8, 32768, [], "ddr4", 42.7,
    free_mib=400 * 1024, read_mib_s=1800.0)

# ── P6 — 64 GB unified memory. Backend seam UNKNOWN, not built. ──────────────
P6 = _profile(
    "p6-unified-64gb", "darwin", 12, 12, 65536,
    [], "unified", 400.0, free_mib=800 * 1024, read_mib_s=5000.0)

ALL_PROFILES = {"P1": P1, "P2": P2, "P3": P3, "P4": P4, "P5": P5, "P6": P6}


# ── Catalog ──────────────────────────────────────────────────────────────────

_MODELS = [
    # model_id,            params_b, active_b, quant,   caps
    ("gemma4:e2b",           5.1,  None,  "Q4_K_M",
     ["completion", "vision", "audio", "tools", "thinking"]),
    ("gemma4:e4b",           8.0,  None,  "Q4_K_M",
     ["completion", "vision", "audio", "tools", "thinking"]),
    ("gemma4:12b",          11.9,  None,  "Q4_K_M",
     ["completion", "vision", "audio", "tools", "thinking"]),
    ("gemma4:26b",          25.8,   4.0,  "Q4_K_M",
     ["completion", "vision", "tools", "thinking"]),
    ("qwen3-embedding:0.6b", 0.596, None, "Q8_0",
     ["tools", "thinking", "embedding"]),
]

_ARTIFACT_BYTES = {
    "gemma4:e2b": int(7.2 * 1024 ** 3),
    "gemma4:e4b": int(9.6 * 1024 ** 3),
    "gemma4:12b": int(7.6 * 1024 ** 3),
    "gemma4:26b": int(17.0 * 1024 ** 3),
    "qwen3-embedding:0.6b": int(0.639 * 1024 ** 3),
}


# ── Chains -- headroom.md §6.3, Phase 3 ──────────────────────────────────────
#
# The sequence Stephen described: "speak, transcribe, reason, render, speak
# back" -- one stt stage, the resident brain, one image render, one tts
# stage. `resident` is what plan_chain treats as ALREADY loaded (the D6
# resident pair, e4b + e2b, §6.3's own "honest resident pair" -- NOT what
# `plan()` picks from the full catalog() lineup above, which prefers the
# biggest installed model for interactive_brain/heavy_hitter and is a
# different question: "what would the ideal lineup be" versus "what is
# actually running right now that a chain has to work from").
CHAIN_STAGES = [{"role": "stt"}, {"role": "interactive_brain"},
                {"role": "image", "units": 1}, {"role": "tts"}]

_RESIDENT_GPU = {
    "interactive_brain": {"model_id": "gemma4:e4b", "device": "gpu:0",
                          "vram_mib": 3081},
    "sidekick": {"model_id": "gemma4:e2b", "device": "gpu:0",
                "vram_mib": 1811},
}
_RESIDENT_CPU = {
    "interactive_brain": {"model_id": "gemma4:e2b", "device": "cpu",
                          "vram_mib": 0},
    "sidekick": {"model_id": "gemma4:e2b", "device": "cpu", "vram_mib": 0},
}

CHAIN_RESIDENT = {
    "P1": _RESIDENT_GPU, "P2": _RESIDENT_GPU, "P3": _RESIDENT_GPU,
    "P4": _RESIDENT_GPU, "P5": _RESIDENT_CPU, "P6": {},
}

# The two real image footprints Phase 2 measured under the Arbiter's own
# `image_job` lease, 2026-09-04 -- committed into `SEED_MEASUREMENTS` at
# `P1_FINGERPRINT` (`residency_catalog.py`) for the reference instance.
# P2-P6 are DECLARED hardware: the same weights cost the same VRAM on any
# machine (only the reserve/baseline around them differs, which
# `plan_chain`'s own budget arithmetic already accounts for per profile) --
# the identical "carry the P1 measurement across" rule `catalog()` above
# already applies to every text model's `measured` row, extended here to the
# two image footprints so a chain fixture on P2-P6 is not forced to treat a
# real, measured number as `unknown` merely because the measurement ran on a
# different card.
IMAGE_FOOTPRINTS = {
    "z-image-turbo-fp8": dict(
        modality="image", device="gpu", vram_mib=10453,
        artifact_bytes=14535245332, load_s=24.52, unit="image",
        work_s_per_unit=49.6, basis="measured", measured_at="2026-09-04",
        licence={"name": "Apache License 2.0",
                "note": "Apache License 2.0 — commercial and private use, "
                        "modification and redistribution permitted",
                "url": "https://huggingface.co/Tongyi-MAI/Z-Image-Turbo"},
        quality_note="turbo: 8 steps, fast"),
    "sd3.5-medium-fp8": dict(
        modality="image", device="gpu", vram_mib=10621,
        artifact_bytes=11638004202, load_s=20.01, unit="image",
        work_s_per_unit=50.1, basis="measured", measured_at="2026-09-04",
        licence={"name": "Stability AI Community License",
                "note": "Stability AI Community License — free below $1M "
                        "annual revenue; attribution required when "
                        "redistributed", "url": None},
        quality_note="30 steps, higher fidelity, slower"),
}


def seed_image_footprints() -> None:
    """Record the two real image footprints for EVERY fixture profile's own
    fingerprint. Call this after isolating `rc.store_path` (a test's own
    `tmp_path` fixture) -- it writes through `record_footprint`, so without
    isolation it would touch whatever store is live."""
    for profile in ALL_PROFILES.values():
        fp_key = rc.profile_fingerprint(profile)
        for model_id, kwargs in IMAGE_FOOTPRINTS.items():
            rc.record_footprint(model_id, fp_key, rc.make_footprint(**kwargs))


def catalog(profile: dict) -> list:
    """CatalogEntries for the installed set, measured rows carried from P1."""
    seed = rc.SEED_MEASUREMENTS[rc.P1_FINGERPRINT]
    out = []
    for model_id, total_b, active_b, quant, caps in _MODELS:
        artifact = _ARTIFACT_BYTES[model_id]
        is_moe = bool(active_b and total_b and active_b < total_b)
        out.append({
            "model_id": model_id,
            "backend": rc.BACKEND_OLLAMA,
            "artifact_bytes": artifact,
            "quantization": quant,
            "params_total_b": total_b,
            "params_active_b": active_b,
            "is_moe": is_moe,
            "context_window": 262144,
            "modalities": caps,
            "can_generate": "completion" in caps,
            "is_embedding": "embedding" in caps and "completion" not in caps,
            "needs_think_disabled": "thinking" in caps,
            "profile_fingerprint": rc.profile_fingerprint(profile),
            "measured": [dict(m) for m in seed.get(model_id, [])],
            "baseline_ms_per_token": min(
                [m["ms_per_token"] for m in seed.get(model_id, [])
                 if m.get("ms_per_token")] or [None],
                default=None) if any(
                    m.get("ms_per_token") for m in seed.get(model_id, []))
            else None,
            "est_load_s": rc.est_load_s(artifact, profile, is_moe),
        })
    return out

"""
Agent Friday — CatalogEntry: what a model costs on THIS machine.

`services/model_catalog.py` answers "what can the user pick?" — it is the
picker's source of truth and is presentation-shaped. This module answers a
different question: "if I place this model here, what will it cost, how fast
will it run, and how long will it take to load?" Placement cannot be decided
from the picker's data, so this reads the same live sources and adds the three
things the residency policy needs and nobody recorded:

  1. **Measured VRAM at a stated num_ctx.** Artifact size is not VRAM footprint
     and the error is not small: `gemma4:e2b` is a 7.2 GB artifact that occupies
     1763 MiB. `routing/model_router._pick_local_model` ranks candidates by
     artifact size, which on the reference instance is not merely imprecise —
     it is inverted. VRAM is measured, never derived from file size.

  2. **Active vs total parameters.** The 26b is 25.8 B total / 4 B active. A
     dense model must fit or be demoted; an MoE may expert-offload (rule R6).
     That distinction is the difference between "this is fine" and "this pages".

  3. **A baseline ms/token.** The health probe's 5x rule has nothing to compare
     against without it. This is why catalog enrichment is sequenced BEFORE the
     strict latency threshold rather than beside it.

Capabilities come from the daemon and settle two live probe defects, both of
which make health report failure while inference is fine:
  * `qwen3-embedding:0.6b` reports `['tools','thinking','embedding']` — no
    `completion`. Sending it a generation probe can only ever return empty.
  * every gemma4 model reports `thinking`; a 1-token probe is consumed entirely
    by reasoning and returns empty text from a perfectly healthy model.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from agent_friday.core import runtime_dir

BACKEND_OLLAMA = "ollama"
BACKEND_LLAMA_SERVER = "llama-server"
BACKEND_COMFYUI = "comfyui"
BACKEND_CPU_SERVICE = "cpu-service"

_SHOW_CACHE: dict = {}
_SHOW_TTL_S = 300.0


def store_path() -> Path:
    return runtime_dir() / "residency" / "measurements.json"


def profile_fingerprint(profile: dict) -> str:
    """A readable, stable key for per-machine measurements.

    Deliberately NOT the opaque profile_id hash: measurements are seeded in
    source and read by humans, and a key like
    "NVIDIA GeForce RTX 4070|12282|32620" survives a schema change to the hash.
    """
    gpus = profile.get("gpus") or []
    gpu = "%s|%d" % (gpus[0]["name"], gpus[0]["vram_total_mib"]) if gpus \
        else "cpu-only"
    return "%s|%d" % (gpu, (profile.get("ram") or {}).get("total_mib", 0))


# ─────────────────────────────────────────────────────────────────────────────
#  Seed measurements — taken 2026-08-14 on the reference instance
#
#  Medians of 5 warm runs after a separate cold load; VRAM read from the
#  daemon's own /api/ps (size_vram / size), not inferred from nvidia-smi deltas.
#  None of these models had ever been measured: 12b, 26b and e2b were pulled
#  hours before, after every prior audit document was written.
# ─────────────────────────────────────────────────────────────────────────────

P1_FINGERPRINT = "NVIDIA GeForce RTX 4070|12282|32620"

SEED_MEASUREMENTS: dict = {
    P1_FINGERPRINT: {
        "gemma4:e2b": [
            {"num_ctx": 8192, "vram_mib": 1763, "total_mib": 1763,
             "pct_gpu": 100, "tok_s_median": 166.13, "tok_s_stdev": 9.76,
             "ms_per_token": 6.02, "probe_ms_per_token": 8.68,
             "cold_load_s": 20.97,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-14"},
            # 32768 is the tool-seat context (the 52-tool registry is ~8534
            # tokens, so a seat below ~12k truncates the tool definitions).
            {"num_ctx": 32768, "vram_mib": 1811, "total_mib": 1811,
             "pct_gpu": 100, "tok_s_median": 178.22,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-15"},
        ],
        "gemma4:e4b": [
            {"num_ctx": 8192, "vram_mib": 3081, "total_mib": 3081,
             "pct_gpu": 100, "tok_s_median": 99.93, "tok_s_stdev": 0.23,
             "ms_per_token": 10.01, "probe_ms_per_token": 9.99,
             "cold_load_s": 27.52,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-14"},
        ],
        "gemma4:12b": [
            {"num_ctx": 4096, "vram_mib": 7690, "total_mib": 7690,
             "pct_gpu": 100, "backend": BACKEND_OLLAMA,
             "measured_at": "2026-08-14"},
            {"num_ctx": 8192, "vram_mib": 7985, "total_mib": 7985,
             "pct_gpu": 100, "backend": BACKEND_OLLAMA,
             "measured_at": "2026-08-14"},
            {"num_ctx": 16384, "vram_mib": 8001, "total_mib": 8001,
             "pct_gpu": 100, "tok_s_median": 49.36, "tok_s_stdev": 0.12,
             "ms_per_token": 20.26, "probe_ms_per_token": 18.27,
             "cold_load_s": 20.49,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-14"},
            # The tool-seat context. Note it measures LOWER than 16384 did —
            # taken under Ollama 0.32.11 where the earlier rows were 0.32.9.
            # Recorded as measured rather than smoothed: the KV curve on this
            # family is flat enough that allocator differences dominate it.
            {"num_ctx": 32768, "vram_mib": 7718, "total_mib": 7718,
             "pct_gpu": 100, "tok_s_median": 54.12,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-15"},
            # The KV sweep that settled how much a bigger window actually
            # costs. 96 MiB buys 4x the context: this is the evidence behind
            # sizing the brain from the whole prompt rather than the tool list.
            {"num_ctx": 65536, "vram_mib": 7750, "total_mib": 7750,
             "pct_gpu": 100,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-15"},
            {"num_ctx": 131072, "vram_mib": 7814, "total_mib": 7814,
             "pct_gpu": 100,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-15"},
        ],
        # Measured under Ollama BEFORE the copy was removed for the
        # llama-server comparison. Kept: it is the banked half of a
        # backend decision made by number.
        "gemma4:26b": [
            {"num_ctx": 16384, "vram_mib": 8586, "total_mib": 17391,
             "pct_gpu": 49, "tok_s_median": 27.95, "tok_s_stdev": 0.26,
             "ms_per_token": 35.78, "cold_load_s": 55.10,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-14"},
        ],
        "qwen3-embedding:0.6b": [
            {"num_ctx": 2048, "vram_mib": 2029, "total_mib": 2029,
             "pct_gpu": 100, "cold_load_s": 3.0,
             "backend": BACKEND_OLLAMA, "measured_at": "2026-08-14"},
        ],
    }
}

# params_active is not reported by any daemon. Declared where known upstream.
# This table is now an OPTIONAL refinement, not the source of truth for
# `is_moe` -- see detect_moe(). It used to be both, and that was a defect:
# glm-4.7-flash (`glm4moelite`, 29.9B) was absent from it, so `is_moe` came out
# False and rule R6 refused a genuine MoE for the heavy_hitter seat as though
# it were dense. The refusal read like a legitimate capacity decision, which is
# the expensive kind of wrong. A hand-maintained allowlist silently mis-seats
# every new MoE that arrives; the artifact already declares what it is.
KNOWN_ACTIVE_PARAMS_B: dict = {
    "gemma4:26b": 4.0,          # 26B-A4B
    "gemma-4-26b-a4b": 4.0,
}


def detect_moe(info: dict, total_b: float | None,
               active_b: float | None) -> tuple[bool, str]:
    """(is_moe, basis) -- read the artifact's own evidence, in order of authority.

    1. `<arch>.expert_count` from GGUF metadata. Definitive: a model with
       experts is a MoE, and nothing else reports that key.
    2. The architecture string. `glm4moelite`, `qwen3moe`, `mixtral` and the
       rest are self-declaring; a name carrying `moe` is not ambiguous.
    3. The KNOWN_ACTIVE_PARAMS_B table, where active < total.

    Returning the basis alongside the verdict so a refusal can say WHY it
    believes what it believes. An unexplained "dense" verdict on a MoE is what
    made the original defect so hard to see.
    """
    mi = (info or {}).get("model_info") or {}
    arch = str(mi.get("general.architecture") or
               ((info or {}).get("details") or {}).get("family") or "").lower()

    for key, val in mi.items():
        if not str(key).endswith(".expert_count"):
            continue
        try:
            if int(val) > 1:
                return True, "gguf:expert_count=%d" % int(val)
        except (TypeError, ValueError):
            pass

    if "moe" in arch:
        return True, "architecture:%s" % arch

    if active_b and total_b and active_b < total_b:
        return True, "known_active_params"

    return False, ("architecture:%s" % arch) if arch else "no-evidence"


# ─────────────────────────────────────────────────────────────────────────────
#  Live facts from the daemon
# ─────────────────────────────────────────────────────────────────────────────

def _show(model_id: str, base_url: str | None = None) -> dict:
    """/api/show, memoised. Carries capabilities, quantization, params."""
    now = time.time()
    hit = _SHOW_CACHE.get(model_id)
    if hit and (now - hit[0]) < _SHOW_TTL_S:
        return hit[1]
    try:
        from agent_friday.routing.ollama_manager import get_manager
        mgr = get_manager(base_url or "http://localhost:11434")
        info = mgr._post("/api/show", {"model": model_id}, timeout=10) or {}
    except Exception:
        info = {}
    _SHOW_CACHE[model_id] = (now, info)
    return info


def reset_cache() -> None:
    _SHOW_CACHE.clear()


def capabilities(model_id: str) -> list:
    return list((_show(model_id) or {}).get("capabilities") or [])


def can_generate(model_id: str) -> bool:
    """Can this model produce text at all?

    An embedding model cannot. Probing one with /api/generate returns empty
    forever, which `health_check` reads as a dead backend — the live cause of
    the local provider reporting `down` on the reference instance while the
    daemon was perfectly healthy.
    """
    caps = capabilities(model_id)
    if not caps:
        # Unknown capabilities: assume generation rather than refuse. An
        # over-eager refusal here would break dispatch for every model whose
        # daemon predates the capabilities field.
        return "embed" not in model_id.lower()
    return "completion" in caps


def is_embedding(model_id: str) -> bool:
    caps = capabilities(model_id)
    if caps:
        return "embedding" in caps and "completion" not in caps
    return "embed" in model_id.lower()


def needs_think_disabled(model_id: str) -> bool:
    """Thinking models spend a small token budget entirely on reasoning.

    Measured: `gemma4:12b` with num_predict=10 returns response='' and
    done_reason='length'. With think:false the same call returns 'Hello!'.
    Any short probe against this family must disable thinking or it measures
    the wrong thing and calls a healthy model dead.
    """
    return "thinking" in capabilities(model_id)


def _parse_params_b(text: str) -> float | None:
    """'11.9B' -> 11.9, '595.78M' -> 0.59578."""
    if not text:
        return None
    m = re.match(r"\s*([\d.]+)\s*([BMK])", str(text), re.I)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2).upper()
    return {"B": val, "M": val / 1000.0, "K": val / 1e6}[unit]


# ─────────────────────────────────────────────────────────────────────────────
#  Measurement store
# ─────────────────────────────────────────────────────────────────────────────

def _load_store() -> dict:
    try:
        return json.loads(store_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_store(data: dict) -> None:
    p = store_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def measurements(model_id: str, fingerprint: str) -> list:
    """Recorded measurements, newest first. Store beats seed for the same ctx."""
    seed = (SEED_MEASUREMENTS.get(fingerprint) or {}).get(model_id) or []
    live = ((_load_store().get(fingerprint) or {}).get(model_id)) or []
    by_ctx = {m.get("num_ctx"): dict(m, source="seed") for m in seed}
    for m in live:
        by_ctx[m.get("num_ctx")] = dict(m, source="measured")
    return sorted(by_ctx.values(), key=lambda m: m.get("num_ctx") or 0)


def record_measurement(model_id: str, fingerprint: str,
                       measurement: dict) -> None:
    """Persist one measurement, replacing any prior entry at the same num_ctx."""
    data = _load_store()
    per_fp = data.setdefault(fingerprint, {})
    rows = [m for m in (per_fp.get(model_id) or [])
            if m.get("num_ctx") != measurement.get("num_ctx")]
    rows.append(dict(measurement, measured_at=measurement.get(
        "measured_at") or time.strftime("%Y-%m-%d")))
    per_fp[model_id] = sorted(rows, key=lambda m: m.get("num_ctx") or 0)
    _save_store(data)


def baseline_ms_per_token(model_id: str, fingerprint: str) -> float | None:
    """Sustained generation speed, from long runs. Used for planning."""
    vals = [m["ms_per_token"] for m in measurements(model_id, fingerprint)
            if m.get("ms_per_token")]
    return min(vals) if vals else None


def baseline_probe_ms_per_token(model_id: str, fingerprint: str) -> float | None:
    """Speed under PROBE conditions, which is a different number.

    A 10-token probe pays fixed per-request overhead that a 200-token run
    amortises away. Measured on the reference instance, a perfectly healthy
    gemma4:e2b probes at 21.86 ms/token against a 6.02 ms/token sustained
    baseline -- 3.6x, which would have consumed most of a 5x margin and made
    the paging detector fire on ordinary contention.

    The health rule compares probe against probe. Falls back to the sustained
    figure only when no probe baseline exists, which is strictly better than
    no check at all but is recorded as the weaker comparison.
    """
    vals = [m["probe_ms_per_token"]
            for m in measurements(model_id, fingerprint)
            if m.get("probe_ms_per_token")]
    return min(vals) if vals else None


def vram_at(model_id: str, fingerprint: str, num_ctx: int) -> int | None:
    """Measured VRAM at num_ctx, or the nearest measured context above it.

    Never extrapolates downward into optimism: if only a smaller context was
    measured, the largest measured value is returned, because under-estimating
    VRAM is the error that fails at load time.
    """
    rows = [m for m in measurements(model_id, fingerprint) if m.get("vram_mib")]
    if not rows:
        return None
    exact = [m for m in rows if m.get("num_ctx") == num_ctx]
    if exact:
        return exact[0]["vram_mib"]
    above = [m for m in rows if (m.get("num_ctx") or 0) >= num_ctx]
    if above:
        return min(above, key=lambda m: m["num_ctx"])["vram_mib"]
    return max(rows, key=lambda m: m["num_ctx"])["vram_mib"]


def est_load_s(artifact_bytes: int, profile: dict,
               is_moe: bool = False) -> float | None:
    """Load time from artifact size and the profile's measured disk rate.

    Fitted against four cold loads on the reference instance: dense models land
    within ~20% of artifact_MiB / rate + 3s. The 26b ran ~35% over, attributed
    to MoE expert placement across the CPU/GPU boundary, so MoE carries a
    multiplier. UNKNOWN whether that multiplier generalises — one data point.
    """
    rate = (profile.get("disk") or {}).get("read_mib_s")
    if not rate or not artifact_bytes:
        return None
    base = (artifact_bytes / 1048576) / rate + 3.0
    return round(base * (1.35 if is_moe else 1.0), 1)


# ─────────────────────────────────────────────────────────────────────────────
#  CatalogEntry
# ─────────────────────────────────────────────────────────────────────────────

def entry(model_id: str, profile: dict,
          backend: str = BACKEND_OLLAMA,
          artifact_bytes: int | None = None) -> dict:
    """Everything the policy needs to place one model on one machine."""
    fp = profile_fingerprint(profile)
    info = _show(model_id)
    details = (info or {}).get("details") or {}
    caps = list((info or {}).get("capabilities") or [])

    if artifact_bytes is None:
        artifact_bytes = 0
        try:
            from agent_friday.routing.ollama_manager import get_manager
            for m in get_manager().list_models():
                if m.get("name") == model_id:
                    artifact_bytes = int(round(
                        (m.get("size_gb") or 0) * 1024 ** 3))
                    break
        except Exception:
            pass

    total_b = _parse_params_b(details.get("parameter_size"))
    active_b = KNOWN_ACTIVE_PARAMS_B.get(model_id)
    if active_b is None:
        active_b = KNOWN_ACTIVE_PARAMS_B.get((model_id or "").lower())
    is_moe, moe_basis = detect_moe(info, total_b, active_b)

    ctx = None
    try:
        from agent_friday.services.model_catalog import context_window_for
        ctx = context_window_for(model_id)
    except Exception:
        pass

    return {
        "model_id": model_id,
        "backend": backend,
        "artifact_bytes": artifact_bytes,
        "quantization": details.get("quantization_level"),
        "params_total_b": total_b,
        "params_active_b": active_b,
        "is_moe": is_moe,
        "is_moe_basis": moe_basis,
        "context_window": ctx,
        "modalities": caps,
        "can_generate": can_generate(model_id),
        "is_embedding": is_embedding(model_id),
        "needs_think_disabled": needs_think_disabled(model_id),
        "profile_fingerprint": fp,
        "measured": measurements(model_id, fp),
        "baseline_ms_per_token": baseline_ms_per_token(model_id, fp),
        "est_load_s": est_load_s(artifact_bytes, profile, is_moe),
    }


def gguf_registry_path() -> Path:
    return runtime_dir() / "residency" / "gguf_models.json"


def gguf_models() -> dict:
    """model_id -> GGUF path, for models served by llama-server.

    Ollama's `list_models` is not the whole local inventory any more. After the
    26b moved to llama-server it stopped appearing in `ollama list` entirely,
    and a catalog built from Ollama alone silently loses the heavy seat — the
    plan then has no heavy_hitter and the arbiter cannot grant a heavy lease.
    """
    try:
        raw = json.loads(gguf_registry_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: v for k, v in raw.items() if Path(v).exists()}


def gguf_models_canonical() -> dict:
    """The gguf registry keyed by canonical id, which is what a seat looks up."""
    return {canonical_model_id(k): v for k, v in gguf_models().items()}


def register_gguf(model_id: str, path) -> None:
    p = gguf_registry_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data[model_id] = str(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def store_entry(model_id: str, rec: dict, profile: dict) -> dict:
    """A CatalogEntry built from Friday's OWN store — no daemon consulted.

    Every capability here comes from the GGUF header rather than from
    `/api/show`, which is the difference between Friday knowing what she has
    and Friday being able to ask a service what she has.
    """
    fp = profile_fingerprint(profile)
    # Same evidence order as the daemon path: the GGUF header states the
    # architecture and the expert count, so `is_moe` is read rather than
    # looked up. This path matters more than the daemon one now that Friday
    # owns her own processes -- a MoE mis-read as dense here is refused the
    # heavy_hitter seat by rule R6 with a message that sounds like capacity.
    _arch = rec.get("arch") or rec.get("architecture") or ""
    _shim = {"model_info": dict(
        {k: v for k, v in rec.items() if str(k).endswith(".expert_count")},
        **{"general.architecture": _arch})}
    _active_b = KNOWN_ACTIVE_PARAMS_B.get(model_id)
    _is_moe, _moe_basis = detect_moe(
        _shim, rec.get("params_total_b"), _active_b)
    return {
        "model_id": model_id,
        "backend": BACKEND_LLAMA_SERVER,
        "artifact_bytes": rec.get("size_bytes") or 0,
        "quantization": rec.get("quantization"),
        "params_total_b": rec.get("params_total_b"),
        "params_active_b": _active_b,
        "is_moe": _is_moe,
        "is_moe_basis": _moe_basis,
        "context_window": rec.get("context_window"),
        "modalities": (["embedding"] if rec.get("is_embedding")
                       else ["completion"] +
                       (["tools"] if rec.get("template_supports_tools")
                        else [])),
        "can_generate": bool(rec.get("can_generate")),
        "is_embedding": bool(rec.get("is_embedding")),
        # The gemma4 family reasons before answering; a short probe measures
        # the reasoning and calls a healthy model dead.
        "needs_think_disabled": "gemma" in (rec.get("architecture") or "")
        or "gemma" in model_id.lower(),
        "profile_fingerprint": fp,
        "measured": measurements(model_id, fp),
        "baseline_ms_per_token": baseline_ms_per_token(model_id, fp),
        "est_load_s": est_load_s(rec.get("size_bytes") or 0, profile,
                                 bool(KNOWN_ACTIVE_PARAMS_B.get(model_id))),
        "gguf_path": rec.get("path"),
        "source": rec.get("source"),
    }


# ---------------------------------------------------------------------------
#  Model identity: one artifact, one id
# ---------------------------------------------------------------------------
#
# The same weights can arrive twice under two names -- once when Ollama pulls
# them and once when they are copied into Friday's own store -- and then the
# picker offers two rows that are the same model and the budget charges it
# twice. Found 2026-08-18: `qwen3-embed:0.6b-q8` (store, 639,150,592 bytes) and
# `qwen3-embedding:0.6b` (daemon, 644,245,094 bytes) were one 0.6B embedder
# wearing two labels.
#
# `installed_entries` already dedupes, but only on an exact id match, which is
# why models present in BOTH stores under the SAME name (embeddinggemma,
# functiongemma) collapse correctly and this pair did not.
#
# Canonical form is the UPSTREAM name -- what `ollama list` shows and what
# someone would search for -- because that is the name the rest of the world
# uses and the one his settings already contain.
MODEL_ALIASES = {
    "qwen3-embed:0.6b-q8": "qwen3-embedding:0.6b",
    "qwen3-embed:0.6b": "qwen3-embedding:0.6b",
}


def canonical_model_id(model_id):
    """The one id an artifact is known by. Unknown ids pass through unchanged.

    Resolves the same way `residency_policy.resolve_role` does for roles, and
    for the same reason: two spellings must never become two seats.
    """
    if not model_id:
        return model_id
    return MODEL_ALIASES.get(model_id, model_id)


# Two artifacts whose sizes agree this closely are treated as suspected
# duplicates. The tolerance is ABSOLUTE, not a percentage, and that matters:
# the difference between two containers holding the same weights is framing
# overhead, which is a fixed few MB and does not scale with the model. A 2%
# relative tolerance was tried first and was wrong in the expensive direction --
# 2% of a 7 GB model is 140 MB, which flagged `gemma4:12b` (7,381,382,048) as a
# duplicate of the HauhauCS 12B finetune (7,516,192,768). Those are genuinely
# different weights and a picker that merged them would hide a model.
#
# For scale: the real duplicate pair differed by 5,094,502 bytes; that false
# positive differed by 134,810,720.
_DUP_TOLERANCE_BYTES = 32 * 1024 * 1024

# Size alone is too weak a signal, and loosening or tightening it only moves
# which pair is wrong. Two 600 MB embedders 17 MB apart -- `embeddinggemma:300m`
# and `qwen3-embedding:0.6b` -- are entirely different models, and no threshold
# that catches a 5 MB framing difference will exclude them.
#
# So a duplicate must ALSO look like the same name. Two labels for one artifact
# share a real word (`qwen3-embed` / `qwen3-embedding`); two different models of
# similar size do not. Both conditions are required, which is what keeps a
# finetune distinct from its base (shared stem, but 134 MB apart) and two
# same-size embedders distinct from each other (close in size, nothing shared).
_QUANT_TOKENS = {"q4", "q8", "q4_k_m", "q8_0", "fp8", "fp16", "bf16", "gguf",
                 "latest", "instruct", "it", "chat"}


def _id_tokens(model_id):
    """Meaningful words in a model id, lowercased.

    Registry prefixes and quantisation markers are dropped: `hf.co/...` says
    where a model came from, not what it is, and every Q4_K_M would otherwise
    look like every other.
    """
    import re as _re
    raw = str(model_id or "").lower()
    raw = raw.split("/")[-1]                      # drop hf.co/Org/
    parts = [t for t in _re.split(r"[^a-z0-9.]+", raw) if t]
    return {t for t in parts if len(t) >= 4 and t not in _QUANT_TOKENS}


def duplicate_candidates(entries):
    """Ids that look like the same artifact under different names.

    An alias table only knows the duplicates someone has already met. This is
    the standing check for the ones nobody has met yet -- and Stephen's
    inventory churns constantly, so there will be more.

    Deliberately advisory: it reports suspicion, it does not merge anything.
    Guessing that two models are the same and silently collapsing them would be
    a worse failure than showing two rows.
    """
    groups = []
    by_kind = {}
    for e in entries:
        key = bool(e.get("is_embedding"))
        by_kind.setdefault(key, []).append(e)
    for _kind, items in by_kind.items():
        items = [e for e in items if (e.get("artifact_bytes") or 0) > 0]
        for i, a in enumerate(sorted(items, key=lambda e: e["model_id"])):
            for b in sorted(items, key=lambda e: e["model_id"])[i + 1:]:
                if canonical_model_id(a["model_id"]) == \
                        canonical_model_id(b["model_id"]):
                    continue          # already collapsed by the alias table
                delta = abs(a["artifact_bytes"] - b["artifact_bytes"])
                shared = _id_tokens(a["model_id"]) & _id_tokens(b["model_id"])
                if delta <= _DUP_TOLERANCE_BYTES and shared:
                    groups.append({
                        "ids": [a["model_id"], b["model_id"]],
                        "bytes": [a["artifact_bytes"], b["artifact_bytes"]],
                        "delta_bytes": delta,
                        "shared_tokens": sorted(shared),
                        "why": ("sizes differ by only %d bytes (about what "
                                "container framing costs) and the ids share "
                                "%s; likely one model under two names"
                                % (delta, ", ".join(sorted(shared)))),
                    })
    return groups


def installed_entries(profile: dict) -> list:
    """A CatalogEntry for every locally available model.

    **Friday's own store first.** It used to be `ollama list` first, which made
    a running daemon a prerequisite for Friday knowing her own capabilities:
    stop Ollama and a machine holding 38 GB of usable weights reported that it
    had no local models, the plan had no seats, and there was nothing to
    explain why.

    The daemon is still consulted, for anything in it that has not been
    imported yet — so a model pulled with `ollama pull` five minutes ago still
    appears, and nothing that worked yesterday stops working today. It is a
    fallback now, not the source.
    """
    from agent_friday.services import model_store as ms

    out, seen = [], set()
    try:
        for model_id, rec in sorted(ms.available().items()):
            canon = canonical_model_id(model_id)
            if canon in seen:
                continue
            # Built under the CANONICAL id, not renamed afterwards: `measured`
            # rows are looked up by model id inside store_entry, so renaming
            # after the fact reads the wrong key and the seat comes back
            # unsized -- which then blocks the fit claim for a model that has
            # been measured all along.
            e = store_entry(canon, rec, profile)
            if canon != model_id:
                e["aliases"] = [model_id]
            out.append(e)
            seen.add(canon)
    except Exception:
        pass

    try:
        from agent_friday.routing.ollama_manager import get_manager
        models = get_manager().list_models()
    except Exception:
        models = []
    for m in models:
        name = canonical_model_id(m.get("name"))
        if not name or name in seen:
            continue          # the store already described it, from the file
        seen.add(name)
        out.append(entry(name, profile, BACKEND_OLLAMA,
                         artifact_bytes=int(round(
                             (m.get("size_gb") or 0) * 1024 ** 3))))
    for model_id, path in sorted(gguf_models().items()):
        model_id = canonical_model_id(model_id)
        if model_id in seen:
            continue
        try:
            size = Path(path).stat().st_size
        except Exception:
            size = 0
        e = entry(model_id, profile, BACKEND_LLAMA_SERVER, artifact_bytes=size)
        # /api/show cannot describe a model the daemon does not have, so fill
        # the facts the policy actually branches on from the seed + declared
        # active-parameter table rather than leaving them empty.
        if not e.get("modalities"):
            e["modalities"] = ["completion", "tools", "thinking"]
            e["can_generate"] = True
            e["is_embedding"] = False
            e["needs_think_disabled"] = True
        if e.get("params_active_b") and not e.get("params_total_b"):
            e["params_total_b"] = 25.8 if "26" in model_id else None
            e["is_moe"] = bool(e["params_total_b"] and
                               e["params_active_b"] < e["params_total_b"])
        e["gguf_path"] = str(path)
        out.append(e)
    return out

"""
Agent Friday — size a local model to Friday's prompts, and place it.

WHAT WENT WRONG
    On an 8 GB laptop the deep-thinker model was registered at 8,192 tokens of
    context while every Friday prompt (system prompt plus tool schemas) was
    ~20k tokens, so every local News run failed "exceeds the available context
    size". It shared the card with a 1.7B helper, both partly offloaded, at
    ~4 tok/s. And the planner refused the 1.7B outright because it had "never
    been measured". Served by hand instead -- the 27B with every layer on the
    GPU at 32k, the 1.7B on the CPU -- the same machine ran the 27B at ~25 tok/s
    and the 1.7B at ~36 tok/s.

THE RULES THIS MODULE GIVES THE PLANNER (residency_policy.plan) AND THE ARBITER
    1. A context FLOOR from the size of Friday's real prompt: the tool schemas
       plus the assembled system prompt, as services/context_budget measures
       them, plus a margin, rounded up to the next ladder rung (8k, 16k, 32k,
       ...). No seat is served below it. When the card cannot hold the model
       at that window, GPU layers go first (partial offload), then the whole
       model goes to the CPU; the window is never cut below the prompt.
    2. Several models on one small GPU: the big reasoning model gets the card;
       a small helper (<= 2B parameters by default) goes to the CPU when the
       two do not fit together.
    3. An UNMEASURED model is planned from an estimate (file size, parameter
       count, KV layout) instead of refused, and the seat says so:
       `sizing.basis == "estimated"` with a `measure_later` step.

    4. A window raised above the record's serve_num_ctx never keeps the -ngl
       sized for the old one: the layers are re-fitted to the VRAM the
       installer budgeted (`layers_for_raised_window`). Only generative chat
       models are raised (`carries_friday_prompt`); embedders keep theirs.
    5. A seat on the CPU is spawned with the GPU hidden from it.
    6. A llama-server measurement counts as the whole model's cost only when
       it was taken with every layer on the GPU (`whole_model_row`).
    Rule 2 applies only when memory is tight: on a card with room for the big
    model and its helpers together, the plan is unchanged.

    Everything here is arithmetic on its inputs. Reading settings, the prompt
    size and the shortlist happens in `sizing_inputs()`, which the Arbiter
    calls; `residency_policy.plan` stays a pure function.

SETTINGS (settings.json -> "local_sizing"), every rule switchable:
    enabled, prompt_margin_pct, min_room_tokens, small_helper_max_params_b,
    helpers_to_cpu_when_tight, plan_unmeasured_from_estimate. A person can
    still pin any number per model in ~/.friday/overrides/models.json
    (services/user_overrides.py); an override outranks this module.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

_log = logging.getLogger("friday.local_sizing")

DEFAULTS = {
    "enabled": True,
    # Room on top of the measured prompt, as a share of it, and at least this
    # many tokens: a reply and a few turns of history have to fit too.
    "prompt_margin_pct": 25,
    "min_room_tokens": 4096,
    # A "small helper" may be moved to the CPU to leave the card to the big
    # model. 1.7B decodes ~36 tok/s on a laptop CPU; a 27B does not.
    "small_helper_max_params_b": 2.0,
    "helpers_to_cpu_when_tight": True,
    # An unmeasured model is planned from its file size and parameter count
    # (and marked "estimated") instead of being refused.
    "plan_unmeasured_from_estimate": True,
    # A seat planned onto the CPU is spawned with the GPU hidden from it
    # (`--device none` plus CUDA_VISIBLE_DEVICES=-1): with only -ngl 0 a CUDA
    # build still allocates its compute buffer on the card.
    "cpu_seats_hide_gpu": True,
}

#: The same ladder residency_policy sizes windows on.
CTX_RUNGS = (8192, 16384, 32768, 65536, 131072, 262144)

#: llama.cpp reads any -ngl at or above the layer count as "every layer".
ALL_LAYERS = 99

#: llama-server's compute buffers at -ub 512, per bonsai2-tiers.json's
#: arithmetic (MEASURED there as ~400 MiB on the 27B).
COMPUTE_MIB = 400

#: Bytes per KV element relative to f16, by cache type (q8_0 is 8.5 bits).
KV_FACTOR = {"f16": 1.0, "bf16": 1.0, "f32": 2.0, "q8_0": 0.53125,
             "q5_1": 0.375, "q5_0": 0.34375, "q4_1": 0.3125, "q4_0": 0.28125,
             "iq4_nl": 0.28125}
DEFAULT_KV_TYPE = "q8_0"

#: When nothing better is known: four KiB per layer per token at f16 is
#: 8 KV heads x 128 dims x (K+V) x 2 bytes, the common GQA shape.
_KIB_PER_LAYER_F16 = 4.0

#: Fallback prompt size when the live one cannot be read: the recorded
#: tool + system prompt figures (context_budget.MEASURED_*).
def _recorded_prompt_tokens() -> int:
    from agent_friday.services.context_budget import (
        MEASURED_SYSTEM_PROMPT_TOKENS, MEASURED_TOOL_TOKENS)
    return int(MEASURED_TOOL_TOKENS + MEASURED_SYSTEM_PROMPT_TOKENS)


# ─────────────────────────────────────────────────────────────────────────────
#  Inputs (the only I/O, done by the caller's side)
# ─────────────────────────────────────────────────────────────────────────────

def settings(raw: Optional[dict] = None) -> dict:
    """The `local_sizing` block over DEFAULTS, type-checked. `raw` is a whole
    settings dict; None reads it. Never raises."""
    if raw is None:
        try:
            from agent_friday.core import _load_settings
            raw = _load_settings() or {}
        except Exception:
            raw = {}
    blk = raw.get("local_sizing") if isinstance(raw, dict) else None
    out = dict(DEFAULTS)
    if isinstance(blk, dict):
        for k, dv in DEFAULTS.items():
            v = blk.get(k)
            if isinstance(dv, bool):
                if isinstance(v, bool):
                    out[k] = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                out[k] = type(dv)(v)
    return out


def prompt_tokens(overhead: Optional[dict] = None) -> dict:
    """Friday's real prompt before any conversation: tool schemas plus the
    assembled system prompt, from context_budget (which measures both live
    and falls back to recorded figures). Injected memory is NOT part of the
    floor: it varies per turn and is the conversation room's job."""
    try:
        if overhead is None:
            from agent_friday.services import context_budget
            overhead = context_budget.overhead()
        tools = int(overhead.get("tool_tokens") or 0)
        sysp = int(overhead.get("system_prompt_tokens") or 0)
        if tools + sysp > 0:
            return {"tokens": tools + sysp, "tool_tokens": tools,
                    "system_prompt_tokens": sysp,
                    "basis": overhead.get("basis") or "measured"}
    except Exception:
        pass
    return {"tokens": _recorded_prompt_tokens(), "basis": "recorded"}


def cached_prompt_tokens() -> dict:
    """`prompt_tokens()` WITHOUT assembling the system prompt: the last figure
    context_budget memoised (any age), else the recorded constants.

    For `LlamaServerBackend.seat_cap`, which must never build the prompt:
    assembling it reaches seat_cap again (_get_friday_system_prompt ->
    _build_context_prompt -> _load_smart_context -> _smart_context_budget ->
    tool_budget._window -> _spawn_cap -> seat_cap -> context_budget.overhead
    -> system_prompt_tokens -> _get_friday_system_prompt), and the memo is
    only written after the build returns, so a cold memo recursed until
    Python's limit and was swallowed as an exception.
    """
    try:
        from agent_friday.services import context_budget
        hit = (getattr(context_budget, "_MEMO", None) or {}).get("overhead")
        if hit and isinstance(hit[1], dict):
            return prompt_tokens(hit[1])
    except Exception:
        pass
    return {"tokens": _recorded_prompt_tokens(), "basis": "recorded"}


#: Model records that never carry Friday's chat prompt, by their own labels.
_NO_PROMPT_KINDS = frozenset({"embedding", "embedder", "vision", "mmproj",
                              "projector", "stt", "tts", "image", "reranker"})


def carries_friday_prompt(rec: Optional[dict]) -> bool:
    """True for a generative chat model: the only kind whose window must hold
    Friday's prompt. An embedder, a vision tower or any record that says it
    cannot generate text keeps its declared window."""
    if not isinstance(rec, dict):
        return False
    if rec.get("is_embedding") or rec.get("can_generate") is False:
        return False
    for key in ("kind", "role", "purpose"):
        v = rec.get(key)
        if isinstance(v, str) and v.strip().lower() in _NO_PROMPT_KINDS:
            return False
    mods = rec.get("modalities")
    if isinstance(mods, (list, tuple)) and mods and "completion" not in mods:
        return False
    return True


def whole_model_row(row: dict) -> bool:
    """Whether a measured row states what holding the WHOLE model costs.

    Ollama rows do (`total_mib` is the full resident size whatever the
    offload). A llama-server row is the card's peak at whatever -ngl the run
    used, so it counts only when it says it was taken with every layer on the
    GPU (`n_gpu_layers` >= the layer count; model_bench records it from now
    on). An older row without it is unknown, and the model is planned from
    its estimate instead.
    """
    if not isinstance(row, dict):
        return False
    if str(row.get("backend") or "") != "llama-server":
        return True
    try:
        return int(row.get("n_gpu_layers")) >= ALL_LAYERS
    except (TypeError, ValueError):
        return False


def kv_layouts() -> dict:
    """model_id -> f16 KiB per token, from the shortlist's published KV
    layouts (bonsai2:27b: 16 attention layers x 4 heads x 256 dims)."""
    out = {}
    try:
        from agent_friday.services import model_shortlist as sl
        for m in sl.entries():
            kib = kv_kib_from_layout(m.get("kv_layout"))
            if kib and m.get("id"):
                out[str(m["id"])] = kib
    except Exception:
        pass
    return out


def sizing_inputs(raw_settings: Optional[dict] = None) -> dict:
    """Everything `residency_policy.plan(..., sizing=...)` needs, read once."""
    p = prompt_tokens()
    return {"cfg": settings(raw_settings), "prompt_tokens": p["tokens"],
            "prompt_basis": p["basis"], "kv_kib_per_token": kv_layouts()}


def default_inputs() -> dict:
    """Pure defaults, for a plan computed without the Arbiter (fixtures)."""
    return {"cfg": dict(DEFAULTS), "prompt_tokens": _recorded_prompt_tokens(),
            "prompt_basis": "recorded", "kv_kib_per_token": {}}


# ─────────────────────────────────────────────────────────────────────────────
#  The context floor
# ─────────────────────────────────────────────────────────────────────────────

def context_floor(prompt: Optional[int] = None, cfg: Optional[dict] = None,
                  model_window: Optional[int] = None) -> dict:
    """The smallest window a Friday seat may be served at.

    `{"prompt_tokens", "prompt_len", "need_tokens", "num_ctx",
    "limited_by_model"}`. `prompt_len` is the same prompt length as
    `prompt_tokens` under a neutral name: log lines use it, because "tokens"
    here is a prompt size, not a secret, and scanners that judge by name
    cannot tell the difference.
    `num_ctx` is the next ladder rung at or above prompt + margin. A model
    whose own window is smaller gets its window, flagged, because serving
    past it is not possible either.
    """
    cfg = cfg or DEFAULTS
    p = int(prompt if prompt is not None else _recorded_prompt_tokens())
    margin = max(p * float(cfg.get("prompt_margin_pct", 25)) / 100.0,
                 float(cfg.get("min_room_tokens", 4096)))
    need = int(math.ceil(p + margin))
    ctx = next((r for r in CTX_RUNGS if r >= need), CTX_RUNGS[-1])
    out = {"prompt_tokens": p, "prompt_len": p, "need_tokens": need,
           "num_ctx": ctx, "limited_by_model": False}
    if model_window and int(model_window) < ctx:
        out["num_ctx"] = int(model_window)
        out["limited_by_model"] = True
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Estimating what an unmeasured model costs
# ─────────────────────────────────────────────────────────────────────────────

def kv_kib_from_layout(layout) -> Optional[float]:
    """f16 KiB per token from {"full_attention_layers"|"layers", "kv_heads",
    "head_dim"}, or None."""
    if not isinstance(layout, dict):
        return None
    try:
        layers = int(layout.get("full_attention_layers") or layout.get("layers") or 0)
        heads = int(layout.get("kv_heads") or 0)
        dim = int(layout.get("head_dim") or 0)
    except (TypeError, ValueError):
        return None
    if layers <= 0 or heads <= 0 or dim <= 0:
        return None
    return layers * heads * dim * 2 * 2 / 1024.0


def params_b(entry: dict) -> Optional[float]:
    for key in ("params_total_b", "params_b"):
        v = entry.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return float(v)
    return None


def weights_mib(entry: dict) -> Optional[int]:
    """The file on disk is what the weights cost; parameter count at ~4.5
    bits when the size is unknown."""
    for key in ("size_bytes", "artifact_bytes", "bytes"):
        v = entry.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return int(math.ceil(v / 1048576.0))
    pb = params_b(entry)
    if pb:
        return int(math.ceil(pb * 1e9 * 4.5 / 8 / 1048576.0))
    return None


def layer_count(entry: dict) -> int:
    v = entry.get("block_count") or entry.get("layers")
    if isinstance(v, int) and v > 0:
        return v
    pb = params_b(entry) or 7.0
    for top, n in ((1.0, 24), (2.5, 28), (5.0, 36), (10.0, 40), (16.0, 48), (40.0, 64)):
        if pb <= top:
            return n
    return 80


def kv_kib_per_token(entry: dict, kv_table: Optional[dict] = None) -> tuple[float, str]:
    """(f16 KiB/token, where it came from)."""
    v = entry.get("kv_kib_per_token")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return float(v), "record"
    kib = kv_kib_from_layout(entry.get("kv_layout"))
    if kib:
        return kib, "layout"
    if kv_table and entry.get("model_id") in kv_table:
        return float(kv_table[entry["model_id"]]), "shortlist"
    return layer_count(entry) * _KIB_PER_LAYER_F16, "assumed"


def estimate(entry: dict, num_ctx: int, *, kv_type: str = DEFAULT_KV_TYPE,
             kv_table: Optional[dict] = None) -> dict:
    """What holding `entry` at `num_ctx` costs, ALL on one GPU, estimated.

    `{"weights_mib", "kv_mib", "compute_mib", "total_mib", "layers",
    "kv_basis", "basis": "estimated"}`; `total_mib` is None when neither the
    file size nor the parameter count is known.
    """
    w = weights_mib(entry)
    kib, kv_basis = kv_kib_per_token(entry, kv_table)
    kv = int(math.ceil(kib * KV_FACTOR.get(kv_type, 1.0) * int(num_ctx) / 1024.0))
    total = (w + kv + COMPUTE_MIB) if w is not None else None
    return {"weights_mib": w, "kv_mib": kv, "compute_mib": COMPUTE_MIB,
            "total_mib": total, "layers": layer_count(entry),
            "kv_basis": kv_basis, "num_ctx": int(num_ctx), "basis": "estimated"}


def gpu_layers_that_fit(est: dict, budget_mib: int) -> int:
    """Layers that fit `budget_mib` with their share of the KV cache, after
    the compute buffers. 0 when not even one does."""
    layers = max(1, int(est["layers"]))
    if est.get("weights_mib") is None:
        return 0
    per_layer = (est["weights_mib"] + est["kv_mib"]) / float(layers)
    room = int(budget_mib) - est["compute_mib"]
    if room <= 0 or per_layer <= 0:
        return 0
    return max(0, min(layers, int(room // per_layer)))


def layers_for_raised_window(entry: dict, declared_ngl: int, declared_ctx: int,
                             num_ctx: int, *, kv_type: str = DEFAULT_KV_TYPE,
                             kv_table: Optional[dict] = None) -> dict:
    """GPU layers for a window raised above the one the -ngl was sized for.

    The installer sized `-ngl` for its own window (T4a: 8,192 tokens of KV on
    an 8 GB card). Serving 32k with the same layer count adds the extra KV on
    top of a card that was already budgeted full, and the seat runs out of
    memory. The VRAM the installer budgeted is kept, not exceeded: what
    `declared_ngl` layers cost at `declared_ctx` (by the same estimate) is
    the budget, and this returns the layers that fit it at `num_ctx`.

    `{"n_gpu_layers", "budget_mib", "estimate", "reason"}`; `n_gpu_layers`
    None when the model's size is unknown (no estimate is possible).
    """
    old = estimate(entry, declared_ctx, kv_type=kv_type, kv_table=kv_table)
    new = estimate(entry, num_ctx, kv_type=kv_type, kv_table=kv_table)
    if old.get("weights_mib") is None:
        return {"n_gpu_layers": None, "budget_mib": None, "estimate": new,
                "reason": "neither the file size nor the parameter count is known"}
    layers = max(1, int(old["layers"]))
    ngl = max(0, int(declared_ngl))
    if ngl == 0:
        return {"n_gpu_layers": 0, "budget_mib": 0, "estimate": new,
                "reason": "declared on the CPU; stays there"}
    on_gpu = min(ngl, layers)
    budget = old["compute_mib"] + int(math.ceil(
        (old["weights_mib"] + old["kv_mib"]) * on_gpu / float(layers)))
    n = gpu_layers_that_fit(new, budget)
    if n >= layers:
        n = ALL_LAYERS if ngl >= layers else min(ngl, layers)
    return {"n_gpu_layers": int(n), "budget_mib": int(budget), "estimate": new,
            "reason": "-ngl %d was sized for %d tokens (~%d MiB); at %d tokens the "
                      "same VRAM holds %d of %d layers"
                      % (ngl, declared_ctx, budget, num_ctx, min(n, layers), layers)}


def is_small_helper(entry: dict, cfg: Optional[dict] = None) -> bool:
    cfg = cfg or DEFAULTS
    pb = params_b(entry)
    if pb is None:
        w = weights_mib(entry)
        # Unknown parameters: a file under ~1.5 GB is a small model by any reading.
        return w is not None and w <= 1536
    return pb <= float(cfg.get("small_helper_max_params_b", 2.0))


# ─────────────────────────────────────────────────────────────────────────────
#  Placement
# ─────────────────────────────────────────────────────────────────────────────

def size_seat(entry: dict, budget_mib: Optional[int], num_ctx: int, *,
              measured_mib: Optional[int] = None, helper: bool = False,
              cfg: Optional[dict] = None, kv_type: str = DEFAULT_KV_TYPE,
              kv_table: Optional[dict] = None) -> dict:
    """Where one model goes, given what is LEFT of the card.

    Returns `{"device": "gpu"|"cpu"|None, "n_gpu_layers", "vram_mib",
    "num_ctx", "basis", "estimate", "reason"}`. `device` None means the model
    cannot be placed here at all (and `reason` says why with numbers). The
    window is `num_ctx` in every outcome: it is the floor, and this function
    never trades it away.

      * fits whole            -> gpu, every layer
      * small helper, tight   -> cpu, 0 layers (rule 2; `helper=True` and the
                                 helpers_to_cpu_when_tight setting)
      * otherwise             -> gpu with the layers that fit (partial offload)
      * not one layer fits    -> cpu
    """
    cfg = cfg or DEFAULTS
    est = estimate(entry, num_ctx, kv_type=kv_type, kv_table=kv_table)
    basis = "measured" if measured_mib is not None else "estimated"
    need = measured_mib if measured_mib is not None else est["total_mib"]
    out = {"num_ctx": int(num_ctx), "basis": basis, "estimate": est,
           "need_mib": need}
    if need is None:
        out.update(device=None, n_gpu_layers=None, vram_mib=None,
                   reason="neither the file size nor the parameter count is known, "
                          "so not even an estimate is possible")
        return out
    if budget_mib is None:
        out.update(device="cpu", n_gpu_layers=0, vram_mib=0,
                   reason="no GPU on this machine")
        return out
    if need <= budget_mib:
        out.update(device="gpu", n_gpu_layers=ALL_LAYERS, vram_mib=int(need),
                   reason="fits whole: %d MiB of %d MiB" % (need, budget_mib))
        return out
    if helper and is_small_helper(entry, cfg) and cfg.get("helpers_to_cpu_when_tight", True):
        out.update(device="cpu", n_gpu_layers=0, vram_mib=0,
                   reason="needs %d MiB and %d MiB is left beside the larger model, so this "
                          "small helper runs on the CPU and the card stays with the big model"
                          % (need, max(0, budget_mib)))
        return out
    if measured_mib is not None and est["total_mib"]:
        # Scale the per-layer arithmetic to what was actually measured.
        scale = measured_mib / float(est["total_mib"])
        est = dict(est, weights_mib=int(est["weights_mib"] * scale),
                   kv_mib=int(est["kv_mib"] * scale))
    n = gpu_layers_that_fit(est, budget_mib)
    if n >= 1:
        vram = min(int(budget_mib), est["compute_mib"] + int(
            math.ceil((est["weights_mib"] + est["kv_mib"]) * n / float(est["layers"]))))
        out.update(device="gpu", n_gpu_layers=n, vram_mib=vram,
                   reason="needs %d MiB whole and %d MiB is free: %d of %d layers on the GPU, "
                          "the rest on the CPU, at the full %d-token window"
                          % (need, budget_mib, n, est["layers"], num_ctx))
        return out
    out.update(device="cpu", n_gpu_layers=0, vram_mib=0,
               reason="needs %d MiB and only %d MiB is free: not one layer fits, so it "
                      "runs on the CPU at the full %d-token window"
                      % (need, max(0, budget_mib), num_ctx))
    return out


def measure_later(model_id: str) -> str:
    return ("planned from an estimate; run `friday measure %s` (or Settings -> Models -> "
            "Measure) to replace it with this machine's numbers" % model_id)


_SAID: set = set()


def lift_declared_ctx(model_id: str, declared: int, *, prompt: Optional[int] = None,
                      cfg: Optional[dict] = None, model_window: Optional[int] = None,
                      overridden: bool = False) -> int:
    """A model record's declared serving window, never below the floor.

    The record's number is the installer's (or the publisher's) and was chosen
    without Friday's prompt in view; on a T4a laptop it was 8,192 against a
    20k prompt. A person's override (`overridden`) is theirs and is kept, with
    a warning in friday.log when it is below the floor.
    """
    cfg = cfg or DEFAULTS
    if not cfg.get("enabled", True):
        return int(declared)
    floor = context_floor(prompt, cfg, model_window)
    if int(declared) >= floor["num_ctx"]:
        return int(declared)
    # seat_cap is asked on every routing decision; say it once per change.
    key = (model_id, int(declared), floor["num_ctx"], bool(overridden))
    if key in _SAID:
        return int(declared) if overridden else int(floor["num_ctx"])
    _SAID.add(key)
    if overridden:
        # The floor's numbers come from the size of the assembled prompt, so
        # they stay out of the log line (CodeQL py/clear-text-logging); the
        # residency status (/api/residency, the seat's "sizing" block) shows them.
        # The model is named on /api/residency too, so the line carries fixed words only.
        _log.warning("local sizing: a local model is overridden below Friday's prompt floor; "
                     "keeping the override (model and numbers: /api/residency)")
        return int(declared)
    _log.info("local sizing: a local model declares less context than Friday's prompt needs; "
              "serving at the prompt floor instead (component=arbiter; model and numbers: "
              "/api/residency)")
    return int(floor["num_ctx"])

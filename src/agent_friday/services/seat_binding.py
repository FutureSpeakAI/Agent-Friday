"""
Agent Friday — seat binding: the residency plan drives `capability_routing`.

Friday grew two vocabularies for the same idea and reconciled them BY HAND:

  * `capability_routing` — reasoning / subagent / creative_* / voice / asr /
    tts / embedding / local. What dispatch actually reads.
  * residency roles — interactive_brain / heavy_hitter / sidekick /
    sidekick_heavy / embedder / stt / tts / image. What the policy computes.

Hand-reconciliation is exactly how `reasoning` came to point at a model that
had been deleted from the disk, and `local` at a `gemma3:4b` that was never
installed (Q5). This module makes the plan authoritative so that class of
defect cannot recur.

One rule keeps it honest:

**`embedding` is never bound.** The plan's embedder seat is
`qwen3-embedding:0.6b` at 1024 dimensions; the live store is
`all-MiniLM-L6-v2` at 384. Decision D5 gates that switch on a re-index path
that does not exist, and A5's dimension guard exists because the mismatch
fails SILENTLY and presents as permanent memory loss. Overwriting this key
from a plan would destroy the Chroma collections.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("friday.seat_binding")

# residency role -> capability_routing key
SEAT_TO_CAPABILITY = {
    "interactive_brain": "reasoning",
    "heavy_hitter": "heavy_hitter",
    "sidekick": "local",
    "sidekick_heavy": "subagent",
    "image": "creative_image",
}

# Flat legacy mirrors. These are NOT optional bookkeeping: core's
# `_sync_capability_routing` derives capability_routing FROM these keys, so a
# capability written without its mirror is silently reverted.
#
# Caught live 2026-08-15. Binding the image seat without updating
# `creative_model` produced a corrupted hybrid — provider `local-comfyui` with
# model `gemini-nano-banana-2`, a Google model on the on-device provider:
#
#     "creative_image": {"provider": "local-comfyui",
#                        "model": "gemini-nano-banana-2"}
#
# Every capability this module binds needs its mirror here, or the two
# surfaces fight and the loser is whichever one dispatch happens to read.
CAPABILITY_TO_FLAT = {
    "reasoning": "orchestrator_model",
    "subagent": "subagent_model",
    "creative_image": "creative_model",
}

# Never bound from a plan. See the module docstring — D5.
NEVER_BIND = {"embedder", "stt", "tts"}

def _provider_for(seat: dict) -> str:
    backend = (seat or {}).get("backend") or ""
    if backend == "llama-server":
        return "llama-cpp-local"
    if backend == "comfyui":
        return "local-comfyui"
    return "ollama-local"


def propose(plan: dict, settings: dict) -> dict:
    """Compute the capability_routing changes the plan implies. Pure.

    Returns {"changes": {cap: {provider, model, from}}, "refusals": [...],
             "skipped": [...]} — nothing is written.
    """
    cr = dict((settings or {}).get("capability_routing") or {})
    changes, refusals, skipped = {}, [], []

    for role, cap in sorted(SEAT_TO_CAPABILITY.items()):
        if role in NEVER_BIND:
            continue
        seat = ((plan or {}).get("seats") or {}).get(role)
        if not seat or not seat.get("model_id"):
            refusal = [r for r in (plan or {}).get("refusals", [])
                       if r.get("role") == role]
            if refusal:
                skipped.append({"role": role, "capability": cap,
                                "why": refusal[0].get("explanation")})
            continue

        model = seat["model_id"]
        provider = _provider_for(seat)
        current = cr.get(cap) or {}

        # 2026-08-15: NO GATE. A seat used to require both battery axes
        # green before it could be bound, which left heavy_hitter, local and
        # subagent permanently unbound on this machine — the plan computed
        # them correctly and then refused to apply them. Removed on Stephen's
        # decision: any installed model is bindable to any seat, and binding
        # it makes it actually serve.

        if (current.get("provider") == provider
                and current.get("model") == model):
            continue
        changes[cap] = {"provider": provider, "model": model,
                        "from": dict(current) or None}

    return {"changes": changes, "refusals": refusals, "skipped": skipped}


def apply(plan: dict, settings: dict) -> dict:
    """Bind the plan into `settings` in place. Returns the proposal applied.

    Mirrors are reconciled for EVERY bound capability, not only the ones that
    changed. A capability can be correct while its flat key has drifted — edit
    `model_routing.local_model` directly and `capability_routing.local` still
    says something else — and `_sync_capability_routing` lets the flat key win,
    so the drift silently becomes the answer. Repairing only the delta left
    that divergence in place.
    """
    prop = propose(plan, settings)
    _reconcile_mirrors(plan, settings)
    if not prop["changes"]:
        return prop
    cr = settings.setdefault("capability_routing", {})
    for cap, val in prop["changes"].items():
        cr[cap] = {"provider": val["provider"], "model": val["model"]}
        flat = CAPABILITY_TO_FLAT.get(cap)
        if flat:
            # provider_health.py:306 specifically guards against a foreign id
            # reaching the Anthropic probe; keeping the mirror in step is what
            # stops that happening.
            settings[flat] = val["model"]
        if cap == "local":
            settings.setdefault("model_routing", {})["local_model"] = \
                val["model"]
    _log.info("seat binding applied: %s",
              ", ".join("%s→%s" % (c, v["model"])
                        for c, v in sorted(prop["changes"].items())))
    return prop


def describe(prop: dict) -> str:
    """One human-readable block, for logs and the startup banner."""
    lines = []
    for cap, v in sorted((prop.get("changes") or {}).items()):
        was = (v.get("from") or {}).get("model") or "—"
        lines.append("  bound   %-15s %s  (was %s)" % (cap, v["model"], was))
    for r in prop.get("refusals") or []:
        lines.append("  REFUSED %-15s %s" % (r["capability"], r["explanation"]))
    for s in prop.get("skipped") or []:
        lines.append("  skipped %-15s %s" % (s["capability"],
                                             (s.get("why") or "")[:100]))
    return "\n".join(lines) or "  (no changes)"


def _reconcile_mirrors(plan: dict, settings: dict) -> None:
    """Force every flat mirror to agree with the capability the plan bound."""
    seats = (plan or {}).get("seats") or {}
    cr = settings.setdefault("capability_routing", {})
    for role, cap in SEAT_TO_CAPABILITY.items():
        if role in NEVER_BIND:
            continue
        seat = seats.get(role)
        if not seat or not seat.get("model_id"):
            continue
        model = seat["model_id"]
        if (cr.get(cap) or {}).get("model") != model:
            continue          # propose() will set it; nothing to reconcile yet
        flat = CAPABILITY_TO_FLAT.get(cap)
        if flat and settings.get(flat) != model:
            settings[flat] = model
        if cap == "local":
            mr = settings.setdefault("model_routing", {})
            if mr.get("local_model") != model:
                mr["local_model"] = model

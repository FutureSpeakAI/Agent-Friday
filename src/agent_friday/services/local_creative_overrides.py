"""
Agent Friday — per-machine local creative model overrides.

Some on-device generation models cannot ship in provider_registry.py, which is
identical on every install: FLUX.1 [dev]'s licence forbids commercial use of
the MODEL itself (its OUTPUTS are unrestricted — the model is not), and that
restriction is Stephen's alone to accept for his own machine. Baking it into
the shipped catalog would mean every install inherits a commitment it never
agreed to.

This module is the one place a model can be registered for THIS MACHINE ONLY:
`register_local_creative_model` writes a small JSON file under
`runtime_dir()`, and `merge_local_creative_overlay` folds its contents into
the in-memory `local-comfyui` provider descriptor at catalog-build time
(model_catalog.build_catalog). A fresh install has no such file, so the merge
is a pure no-op there — nothing here ever touches provider_registry.py's
shipped data, on disk or in memory.

The dispatch side (local_image.py's MODELS entry for flux1-dev-fp8) is fine to
ship in source regardless: it is inert without BOTH this overlay naming the
model AND the weight files actually being on disk (is_installed() checks the
latter). Shipping dormant code is not shipping the capability.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from agent_friday.core import runtime_dir

OVERLAY_PROVIDER = "local-comfyui"


def overlay_path() -> Path:
    return runtime_dir() / "creative_models_local.json"


def load_overlay() -> dict:
    """{"local-comfyui": {"models": [...], "model_meta": {...}}}, or {} when
    the file is absent or unreadable — absence is the NORMAL case, not an
    error, so this never logs or raises for it."""
    p = overlay_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def merge_local_creative_overlay(provider: dict) -> dict:
    """Return `provider` unchanged, or a shallow-merged COPY carrying this
    machine's locally-registered models, when `provider` is the
    local-comfyui descriptor and an overlay file exists.

    Never mutates the dict it is given: the caller holds the ProviderRegistry
    singleton's own object, and a permanent mutation would (a) leak a
    personal-use-only model into anything that later persists that descriptor
    back to disk via ProviderRegistry.update_provider, and (b) accumulate
    duplicate model ids across repeated build_catalog() calls in one process.
    A fresh copy, remerged from the overlay file every call, has neither
    problem — it costs one small JSON read per catalog build, which is
    already the same budget model_discovery spends on its own disk cache.
    """
    if not isinstance(provider, dict) or provider.get("name") != OVERLAY_PROVIDER:
        return provider
    overlay = load_overlay().get(OVERLAY_PROVIDER)
    if not overlay:
        return provider
    merged = dict(provider)
    base_ids = list(merged.get("models") or [])
    extra_ids = [m for m in (overlay.get("models") or []) if m not in base_ids]
    if extra_ids:
        merged["models"] = base_ids + extra_ids
    extra_meta = overlay.get("model_meta") or {}
    if extra_meta:
        meta = dict(merged.get("model_meta") or {})
        meta.update(extra_meta)
        merged["model_meta"] = meta
    return merged


def register_local_creative_model(model_id: str, meta: dict) -> Path:
    """Write/update this machine's overlay file. Additive: existing entries
    for OTHER models are preserved; re-registering `model_id` replaces only
    its own entry.

    Atomic write — same pattern as model_store.py's `_save()`: write to a
    `.tmp` sibling, then `os.replace()`. This must never be hand-edited via a
    shell (PowerShell's `ConvertTo-Json`/`Set-Content` mangle encoding and
    aren't atomic); this function is the only writer.
    """
    p = overlay_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_overlay()
    prov = data.setdefault(OVERLAY_PROVIDER, {})
    ids = prov.setdefault("models", [])
    if model_id not in ids:
        ids.append(model_id)
    meta_map = prov.setdefault("model_meta", {})
    meta_map[model_id] = dict(meta)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return p


if __name__ == "__main__":
    # One-time, per-machine registration — run once via the venv python:
    #   venv\Scripts\python -m agent_friday.services.local_creative_overrides
    # NOT a general CLI command; it always registers this one model with
    # these fixed, licence-accurate fields. Re-running is harmless (the write
    # above is idempotent per model_id).
    from agent_friday.services.provider_registry import ROLE_CREATIVE

    path = register_local_creative_model("flux1-dev-fp8", {
        "label": "FLUX.1 Dev (personal use)",
        "short": "FLUX.1 Dev",
        "roles": [ROLE_CREATIVE],
        "modalities": ["image"],
        "note": "highest quality local image option, personal-use only. "
                "Measured ~95s per 1024x1024 image, ~11.6GB VRAM peak on a "
                "4070 12GB — only ~700MB headroom left on this card, so "
                "treat it as usable but fragile: a larger resolution or "
                "concurrent GPU use could push it over.",
        "licence": "FLUX.1 [dev] Non-Commercial License v1.1.1",
        "licence_note": "Outputs may be used for any purpose including sale. "
                        "The MODEL itself may not be used commercially — this "
                        "is registered for Stephen's personal use only and "
                        "must never be added to provider_registry.py or any "
                        "shipped default catalog.",
    })
    print("registered flux1-dev-fp8 in", path)

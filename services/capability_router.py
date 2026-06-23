"""
Agent Friday — Capability Router

The single resolver that maps a CAPABILITY to a concrete provider+model and reports
whether it is currently usable. Reads ``settings.capability_routing`` (the canonical
per-capability map, kept congruent with the legacy flat ``*_model`` keys by
``core._sync_capability_routing``) and the declarative ProviderRegistry.

Capabilities: reasoning, subagent, creative_image, creative_video, voice, embedding, local.

Used by:
  * the UI lock/unlock badges      — GET /api/capabilities
  * graceful degradation           — "Connect <provider> to unlock <feature>"
  * /api/health/full               — capability-resolution block

There are NO hardcoded provider/model lists here — everything resolves from the
registry + settings, so adding a provider surfaces in routing automatically.
"""
from __future__ import annotations

CAPABILITIES = ("reasoning", "subagent", "creative_image", "creative_video",
                "voice", "asr", "tts", "embedding", "local")

_CAP_LABEL = {
    "reasoning": "Reasoning & chat",
    "subagent": "Background tasks",
    "creative_image": "Image generation",
    "creative_video": "Video generation",
    "voice": "Live voice (cloud)",
    "asr": "Speech-to-text",
    "tts": "Text-to-speech",
    "embedding": "Memory & search",
    "local": "On-device inference",
}


def _settings():
    import core
    return core._load_settings()


def _registry():
    from services.provider_registry import get_provider_registry
    return get_provider_registry()


def _provider_label(name):
    if not name:
        return None
    p = _registry().get_provider(name)
    return (p or {}).get("label") or name


def resolve(capability, settings=None):
    """Resolve one capability.

    Returns: {capability, label, provider, provider_label, model, available,
    unlock_hint}. ``available`` reflects whether the assigned provider has a usable
    key / is reachable (registry.is_provider_available). Embedding and local
    inference run on-device and are always considered available (they degrade
    gracefully rather than erroring).
    """
    settings = settings if settings is not None else _settings()
    cr = settings.get("capability_routing") or {}
    entry = cr.get(capability) or {}
    provider = entry.get("provider")
    model = entry.get("model")

    if capability == "embedding" or provider == "local":
        available = True
    elif provider:
        try:
            available = _registry().is_provider_available(provider)
        except Exception:
            available = False
    else:
        available = False

    unlock_hint = None
    if not available:
        # Local voice degrades to an install hint, not a "connect a key" one.
        prov = _registry().get_provider(provider) if provider else None
        if prov and prov.get("type") in ("local-voice", "nemo-local"):
            extra = ("voice-local-lite" if prov.get("type") == "local-voice"
                     else "voice-local-gpu")
            unlock_hint = (f"Install `.[{extra}]` to unlock "
                           f"{_CAP_LABEL.get(capability, capability)}")
        else:
            who = _provider_label(provider) or "a provider"
            unlock_hint = f"Connect {who} to unlock {_CAP_LABEL.get(capability, capability)}"

    return {
        "capability": capability,
        "label": _CAP_LABEL.get(capability, capability),
        "provider": provider,
        "provider_label": _provider_label(provider),
        "model": model,
        "available": bool(available),
        "unlock_hint": unlock_hint,
    }


def route_table(settings=None):
    """All capabilities resolved — the shape the UI renders lock/unlock badges from."""
    settings = settings if settings is not None else _settings()
    return [resolve(c, settings) for c in CAPABILITIES]


def is_available(capability, settings=None):
    return resolve(capability, settings)["available"]


def unlock_note(capability, settings=None):
    """The 'Connect X to unlock Y' string for an unavailable capability, else None.

    Engines call this to return a friendly note instead of raising when the
    provider for a capability isn't configured (graceful degradation)."""
    return resolve(capability, settings).get("unlock_hint")

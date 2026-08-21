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

# creative_music was absent from this tuple entirely, so /api/capabilities has
# never reported music at all — neither as present nor as missing. An omitted
# capability reads as "not a thing Friday does"; the honest report is that it
# IS a thing she is asked for and currently cannot do.
CAPABILITIES = ("reasoning", "subagent", "creative_image", "creative_video",
                "creative_music",
                "voice", "asr", "tts", "embedding", "local")

_CAP_LABEL = {
    "reasoning": "Reasoning & chat",
    "subagent": "Background tasks",
    "creative_image": "Image generation",
    "creative_video": "Video generation",
    "creative_music": "Music generation",
    "voice": "Live voice (cloud)",
    "asr": "Speech-to-text",
    "tts": "Text-to-speech",
    "embedding": "Memory & search",
    "local": "On-device inference",
}


def _settings():
    import agent_friday.core as core
    return core._load_settings()


def _registry():
    from agent_friday.services.provider_registry import get_provider_registry
    return get_provider_registry()


def _provider_label(name):
    if not name:
        return None
    p = _registry().get_provider(name)
    return (p or {}).get("label") or name


#: Routes that resolve to a configured provider but a backend that has never
#: produced output. Each entry must carry WHY, and must be deleted the moment
#: the route is proven — this list is a disclosure, not a permanent opinion.
_STUB_ROUTES = {
    ("creative_music", "google-gemini"): (
        "Music is not available: the installed google-genai has no batch music "
        "surface (only Lyria RealTime streaming), so this route writes a "
        "written preview rather than audio. Higgsfield lists an audio model "
        "('sonilo_music') but nothing has generated audio here yet."),
}


def _stub_route_reason(capability, provider, model=None):
    """Why a resolved route cannot actually deliver, or None if it can.

    Deliberately a declared list rather than a guess: claiming a capability
    works because a key exists is the failure this guards against, and
    inventing a probe that does not exist would be the same error inverted.
    """
    return _STUB_ROUTES.get((capability, provider))


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

    # A route can resolve to a provider whose key is present and whose backend
    # still cannot do the job. Availability here is derived from key presence,
    # which answers "is this provider configured", not "does this work" — so a
    # configured key in front of a stub reports AVAILABLE and the capability
    # list lies. Known non-producing routes are named here, with the reason,
    # until something probes for real output instead of for credentials.
    stub_reason = _stub_route_reason(capability, provider, model)
    if stub_reason:
        available = False

    unlock_hint = None
    if stub_reason:
        unlock_hint = stub_reason
    elif not available:
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

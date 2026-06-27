"""
Agent Friday — Demo Mode

When NO AI provider is configured, Friday still runs: it serves clearly-labelled
canned responses so a new user can explore the full UI and understand what each
capability would do, then "connect a provider to go live".

``is_demo()`` is the single gate:
  * settings.demo_mode is True   -> always demo (explicit override)
  * settings.demo_mode is False  -> never demo (explicit override)
  * settings.demo_mode is None   -> AUTO: demo only when no provider is available
"""
from __future__ import annotations

DEMO_BANNER = "DEMO MODE — connect a provider to go live"


def _settings(settings=None):
    if settings is not None:
        return settings
    import agent_friday.core as core
    return core._load_settings()


def _any_provider_available() -> bool:
    """True if at least one enabled provider has a usable key (or a local Ollama
    daemon with at least one model installed)."""
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        reg = get_provider_registry()
        for p in reg.get_enabled_providers():
            name = p.get("name", "")
            # Voice-only providers (local-voice / nemo-local) are not a reasoning
            # brain — they must not lift demo mode on their own.
            if p.get("type") in ("local-voice", "nemo-local"):
                continue
            if not reg.is_provider_available(name):
                continue
            if p.get("type") == "ollama":
                # Local only counts as "live" when a model is actually installed.
                try:
                    from agent_friday.services.model_catalog import _live_ollama_models
                    if not _live_ollama_models(p.get("base_url")):
                        continue
                except Exception:
                    continue
            return True
    except Exception:
        pass
    return False


def is_demo(settings=None) -> bool:
    s = _settings(settings)
    mode = s.get("demo_mode")
    if mode is True:
        return True
    if mode is False:
        return False
    return not _any_provider_available()


_CANNED = {
    "chat": (
        "[DEMO] I'm Agent Friday running in demo mode — no AI provider is connected "
        "yet, so this is a canned reply. With a provider connected I'd answer your "
        "message using your chosen reasoning model and your private vault context, "
        "and I could run tools (calendar, news, code, web) to actually get it done.\n\n"
        "Open Settings → AI Providers (or re-run setup) and add one API key to go live."
    ),
    "briefing": (
        "[DEMO] Your morning briefing would appear here — top headlines on your "
        "priority topics, today's calendar, unread messages worth your attention, and "
        "anything Friday flagged overnight. Connect a provider to generate it live."
    ),
    "image": (
        "[DEMO] Image generation is part of the Creative capability (Gemini Nano Banana "
        "/ Veo). Connect Google Gemini to generate real images and video from a prompt."
    ),
    "voice": (
        "[DEMO] Voice is built in. The default is fully LOCAL (on-device): install "
        "`.[voice-local-lite]` and Friday talks with you offline — faster-whisper "
        "for listening, Piper for speaking, no cloud. Prefer the cloud? Connect "
        "Google Gemini for Gemini Live. Connect a reasoning provider to give the "
        "voice a brain."
    ),
    "generic": (
        "[DEMO] This is a placeholder response. Connect an AI provider in Settings to "
        "see Friday do this for real."
    ),
}


def demo_response(kind="generic", prompt=None) -> str:
    """A clearly-labelled canned response explaining what Friday WOULD do with a
    real provider connected."""
    base = _CANNED.get(kind, _CANNED["generic"])
    if prompt and kind == "chat":
        snippet = str(prompt).strip().replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:80] + "…"
        base = f'[DEMO] You said: "{snippet}"\n\n' + base
    return base


def demo_status(settings=None) -> dict:
    """The block the UI banner reads."""
    return {"demo_mode": is_demo(settings), "banner": DEMO_BANNER}

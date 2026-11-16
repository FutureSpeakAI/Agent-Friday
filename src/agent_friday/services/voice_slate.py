"""The voice slate: every voice engine the PC offers and every voice on each, in one list that the desktop
settings and every remote device (phone, tablet, TV) read, plus the check for a mid-conversation switch.

Same idea as services/model_slate (the model one): one source, never rebuilt per request, nothing guessed.

An ENGINE is a value of the ``voice_engine`` setting: ``local`` (Whisper + Piper on the CPU), ``local-gpu``,
``gemini`` (Gemini Live), ``openai`` (OpenAI Realtime) or ``cloud:<provider>`` (local listening, a cloud
voice for the reply text only: ElevenLabs). Per engine: provider, local or cloud, key present, whether it can
run now and why not in words, a latency CLASS (``fast``/``medium``/``slow``, a guide from how the engine
works, labelled ``latency_basis``; not a benchmark), and exactly what leaves the house. A VOICE is one voice
of an engine; where this PC cannot list an engine's voices without a network call (an ElevenLabs account's
own voices) only the voice in use is listed and ``voices_complete`` says so.

``plan_switch`` answers "can this conversation move to that engine and voice, and what would leave the
house?" without changing anything. A cloud engine is never switched here: the owner's one question ("just
this call, or always?", services/local_voice_readiness) is the only way voice moves to the cloud, so a cloud
pick answers ``needs_choice`` with that fact, and Local only answers ``refused``. A voice that the live
engines bind at connect time takes effect on the next connection, and the plan says so
(``takes_effect``) instead of promising a mid-sentence change.
"""
from __future__ import annotations

from typing import Optional

UNKNOWN_ENGINE = "unknown_engine"
UNKNOWN_VOICE = "unknown_voice"
UNAVAILABLE = "engine_unavailable"
LOCAL_ONLY_MODE = "local_only_mode"
NEEDS_CHOICE = "needs_choice"

#: OpenAI Realtime's voices (the API's documented voice names). ``marin`` is this app's default.
OPENAI_VOICES = ("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse")

LEAVES_NOTHING = "Nothing. Your microphone audio and Friday's replies stay on this PC."


class VoiceNotServable(Exception):
    """A voice pick that cannot run, with a stable ``code`` and a reason in words."""

    def __init__(self, engine, code, reason, voice=None):
        super().__init__(reason)
        self.engine, self.voice, self.code, self.reason = engine, voice, code, reason


# -- the sources (each one swappable in tests) --------------------------------------------------------

def _catalog() -> dict:
    from agent_friday.services import warm_cache
    from agent_friday.services.model_catalog import build_catalog
    warm_cache.register("model_catalog", build_catalog, ttl_s=900.0)
    got = warm_cache.get("model_catalog", compute_if_cold=True) or {}
    return got.get("value") or {}


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _cloud_providers() -> list:
    try:
        from agent_friday.services import cloud_voice
        return cloud_voice.available_providers()
    except Exception:
        return []


def _cloud_egress(name: str) -> str:
    try:
        from agent_friday.services import cloud_voice
        return cloud_voice.disclosure(name)["egress"]
    except Exception:
        return "The text Friday is about to speak, after it passes the privacy gate. Not your microphone audio."


def _piper_voices() -> list:
    try:
        from agent_friday.services import setup_steps
        return list(setup_steps.installed_voices())
    except Exception:
        return []


def _gemini_voices() -> dict:
    try:
        from agent_friday.services.agent_identity import GEMINI_VOICE_GENDER
        return dict(GEMINI_VOICE_GENDER)
    except Exception:
        return {}


def _kokoro_voices() -> list:
    try:
        from agent_friday.services.agent_identity import CLOUD_TO_KOKORO, DEFAULT_KOKORO_VOICE
        return sorted(set(CLOUD_TO_KOKORO.values()) | {DEFAULT_KOKORO_VOICE})
    except Exception:
        return []


def _openai_state() -> dict:
    try:
        from agent_friday.services import openai_realtime as o
        return {"key_present": o.key_state() == "connected", "model": o.MODEL, "default": o.VOICE,
                "price": o.per_minute_line()}
    except Exception:
        return {"key_present": False, "model": None, "default": "marin", "price": None}


def _local_only(settings: dict) -> bool:
    try:
        return str(((settings.get("model_routing") or {}).get("mode")) or "").strip().lower() == "local_only"
    except Exception:
        return False


# -- the slate ----------------------------------------------------------------------------------------

def _voice(vid, label=None, **extra) -> dict:
    return dict({"id": vid, "label": label or vid}, **extra)


def _engine_rows(cat: dict, settings: dict) -> list:
    by_id = {e.get("id"): e for e in cat.get("voice_engines") or [] if isinstance(e, dict)}
    tts = {e.get("id"): e for e in cat.get("tts_engines") or [] if isinstance(e, dict)}
    locked = _local_only(settings)
    oa = _openai_state()
    rows = []

    def add(eid, label, provider, local, key_present, available, hint, latency, why, leaves, voices,
            complete=True, kind="speech_to_speech", **extra):
        rows.append(dict({
            "id": eid, "label": label, "provider": provider, "local": local, "cloud": not local,
            "kind": kind, "key_present": key_present,
            "available": bool(available), "can_serve": bool(available) and not (locked and not local),
            "blocked": None if (available and not (locked and not local)) else
            {"code": LOCAL_ONLY_MODE if (available and locked and not local) else UNAVAILABLE,
             "reason": ("Local only is on, so a cloud voice is off." if (available and locked and not local)
                        else (hint or "This voice engine is not ready on the PC."))},
            "latency_class": latency, "latency_basis": "guide: " + why, "what_leaves": leaves,
            "voices": voices, "voices_complete": complete}, **extra))

    e = by_id.get("local") or {}
    piper = [_voice(v, v, synthesizer="piper") for v in _piper_voices()]
    kok_ok = bool((tts.get("kokoro") or {}).get("available"))
    kokoro = [_voice(v, v, synthesizer="kokoro") for v in _kokoro_voices()] if kok_ok else []
    add("local", e.get("label") or "Local CPU (Whisper + Piper)", "local", True, True, e.get("available"),
        e.get("hint"), "medium", "runs on the CPU, a sentence takes a moment", LEAVES_NOTHING,
        piper + kokoro, complete=True, kind="local_pipeline")
    g = by_id.get("local-gpu") or {}
    add("local-gpu", g.get("label") or "Local GPU (NeMo)", "local", True, True, g.get("available"),
        g.get("hint"), "fast", "runs on the GPU", LEAVES_NOTHING, kokoro, kind="local_pipeline")
    gm = by_id.get("gemini") or {}
    gv = _gemini_voices()
    add("gemini", gm.get("label") or "Gemini Live (cloud)", "google", False, bool(gm.get("available")),
        gm.get("available"), gm.get("hint"), "fast", "speech to speech, no text hop",
        "Your microphone audio and the conversation go to Google (Gemini Live) while it is on.",
        [_voice(v, v, gender=gv[v]) for v in sorted(gv)])
    ok = bool(oa["key_present"])
    oe = by_id.get("openai") or {}
    add("openai", oe.get("label") or "OpenAI Realtime (cloud)", "openai", False, ok,
        oe.get("available", ok), oe.get("hint"), "fast", "speech to speech, no text hop",
        "Your microphone audio and the conversation go to OpenAI (Realtime) while it is on.",
        [_voice(v, v, default=(v == oa["default"])) for v in OPENAI_VOICES], model=oa["model"],
        price=oa["price"])
    for p in _cloud_providers():
        name = p.get("name")
        ready = bool(p.get("selectable"))
        try:
            from agent_friday.services import cloud_voice
            vid = cloud_voice.selected_voice(name, settings)
        except Exception:
            vid = None
        add("cloud:" + str(name), p.get("label") or name, name, False, ready, ready, p.get("reason"),
            "medium", "local listening, then the reply text is synthesised in the cloud",
            _cloud_egress(name), [_voice(vid, "Voice in use", in_use=True)] if vid else [], complete=False,
            kind="synthesis_only", models=[m.get("id") for m in p.get("models") or []],
            durable_output=bool(p.get("durable_output")))
    return rows


def slate() -> dict:
    """Every engine and voice, with the selected engine, the voice in use and the routing mode."""
    settings = _settings()
    cat = _catalog()
    sel = str(settings.get("voice_engine") or "local").strip().lower()
    sel = "local" if sel == "auto" else ("gemini" if sel == "gemini-live" else sel)
    rows = _engine_rows(cat, settings)
    for r in rows:
        r["selected"] = r["id"] == sel
    return {"engines": rows, "selected": sel, "routing_mode": (settings.get("model_routing") or {}).get("mode"),
            "voice_in_use": settings.get("tts_voice") or settings.get("local_voice_tts_voice")}


def find(engine: str, rows: Optional[list] = None) -> Optional[dict]:
    key = str(engine or "").strip().lower()
    key = "gemini" if key == "gemini-live" else key
    for r in (rows if rows is not None else slate()["engines"]):
        if r["id"] == key:
            return r
    return None


# -- the switch check ---------------------------------------------------------------------------------

def plan_switch(engine: str, voice: Optional[str] = None, conversation_id: Optional[str] = None) -> dict:
    """What a move to ``engine`` (and ``voice``) would do, without doing it.

    Returns ``{"outcome": "ok"|"needs_choice", ...}`` or raises ``VoiceNotServable`` (loud, coded). ``ok``
    means the pick is allowed for that conversation; ``needs_choice`` means it is a cloud engine and only the
    owner's own answer on the PC can move voice there. Both carry ``what_leaves`` so the device can show it
    before anything is sent."""
    rows = slate()["engines"]
    row = find(engine, rows)
    if row is None:
        raise VoiceNotServable(engine, UNKNOWN_ENGINE,
                               "No voice engine called %r. Pick one from the voice list." % (engine,))
    if row["blocked"]:
        raise VoiceNotServable(row["id"], row["blocked"]["code"], row["blocked"]["reason"])
    chosen = None
    if voice:
        chosen = next((v for v in row["voices"] if v["id"] == voice), None)
        if chosen is None and row["voices_complete"]:
            raise VoiceNotServable(row["id"], UNKNOWN_VOICE,
                                   "%s has no voice called %r." % (row["label"], voice), voice)
    live = row["id"] in ("gemini", "openai")
    out = {"engine": row["id"], "voice": voice or None, "conversation_id": conversation_id,
           "local": row["local"], "latency_class": row["latency_class"], "what_leaves": row["what_leaves"],
           "takes_effect": "next_connection" if live else "next_reply",
           "voice_verified": chosen is not None or not voice}
    if row["cloud"] and row["kind"] != "synthesis_only":
        out.update(outcome=NEEDS_CHOICE,
                   message="Cloud voice needs your answer on the PC: just this call, or always. Nothing has changed.")
    else:
        out.update(outcome="ok")
    return out

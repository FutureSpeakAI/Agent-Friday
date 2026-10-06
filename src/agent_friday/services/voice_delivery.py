"""Shared spoken preferences and native synthesis cadence, without audio retiming."""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar

_OVERRIDES = ContextVar("friday_spoken_delivery", default=None)


@contextmanager
def using_preferences(overrides):
    """Bind a single generation/synthesis job, including its GPU worker hop."""
    token = _OVERRIDES.set(dict(overrides or {}))
    try:
        yield
    finally:
        _OVERRIDES.reset(token)


def current_overrides():
    return dict(_OVERRIDES.get() or {})


def end_live_on_conversation_change(session, conversation_id, *, flush_turn,
                                    pending, clear_resume, end_call):
    """A cloud call cannot move its provider-held history into another chat.

    Finish the original turn under its original owner, then close rather than
    replaying a session handle, queued results, or preferences in the new chat.
    """
    original = session.get("conversation_id")
    if conversation_id == original:
        return False
    try:
        flush_turn(original)
    finally:
        for buffer in pending:
            buffer.clear()
        session["delivery_preferences"] = {}
        session["conv_state"] = {}
        session["spoken"] = []
        session["owner_text"] = ""
        try:
            clear_resume()
        finally:
            end_call()
    return True


def initialize_session_preferences(session, settings):
    """Snapshot the two saved defaults once, before a call starts."""
    if "delivery_defaults" not in session:
        with using_preferences({}):
            selected = preferences(settings or {})
        session["delivery_defaults"] = {
            "voice_response_depth": selected["depth"],
            "voice_speaking_pace": selected["pace"],
        }


def session_preferences(session, conversation_id=None):
    if not isinstance(session, dict):
        return {}
    cid = conversation_id or session.get("conversation_id") or ""
    if session.get("conversation_id") and cid != session["conversation_id"]:
        return {}
    return {**(session.get("delivery_defaults") or {}),
            **((session.get("delivery_preferences") or {}).get(cid) or {})}


def update_session_preferences(session, user_text, conversation_id=None):
    """Apply direct delivery requests within this call and thread only."""
    cid = conversation_id or session.get("conversation_id") or ""
    text = re.sub(r"(?i)^(?:friday[, ]+)?(?:please\s+)?", "", str(user_text or "").strip())
    update = {}
    for pattern, key, value in (
        (r"^(?:slow down|speak slower|talk slower|take your time)\b", "voice_speaking_pace", "measured"),
        (r"^(?:speak faster|talk faster|speed up|faster)\b", "voice_speaking_pace", "brisk"),
        (r"^(?:normal pace|speak normally|back to normal pace)\b", "voice_speaking_pace", "natural"),
        (r"^(?:keep it (?:short|brief)|be brief|short version)\b", "voice_response_depth", "concise"),
        (r"^(?:talk (?:it|that) through|go deeper|tell me more|explain more)\b", "voice_response_depth", "detailed"),
    ):
        if re.search(pattern, text, re.I):
            update[key] = value
    saved = session.setdefault("delivery_preferences", {}).setdefault(cid, {})
    saved.update(update)
    return dict(saved)


PRESENCE_RULE = (
    "CONVERSATIONAL PRESENCE: Keep the recognizable character in your saved "
    "personality across practical work, curiosity, disagreement and serious moments. "
    "Have a reasoned point of view; explain what convinced you and revise it when "
    "new evidence or a correction changes your mind. Say when you are unsure or "
    "made a mistake without a defensive speech. Respond to the feeling in what "
    "was actually said before rushing to solve it. Let humor arise from the "
    "moment and established shared references; never force a joke, a catchphrase "
    "or a filler. Remember what mattered and unfinished thoughts from the "
    "conversation context, without reciting a profile or claiming memories you "
    "were not given. Growth means carrying corrections and earned preferences "
    "forward, not inventing a biography, private experiences or feelings as facts. "
    "Be warm without claiming dependence or exclusivity. The user's latest "
    "direction overrides an inferred preference; personality never changes "
    "permissions. Lasting identity edits use the existing versioned personality "
    "settings, not unrequested self-rewrites.\n"
)


def settings_snapshot():
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def preferences(settings=None):
    overrides = current_overrides()
    complete = "voice_response_depth" in overrides and "voice_speaking_pace" in overrides
    s = dict(({} if complete else settings_snapshot()) if settings is None else settings)
    s.update(overrides)
    depth = s.get("voice_response_depth", "adaptive")
    pace = s.get("voice_speaking_pace", "adaptive")
    return {
        "depth": depth if depth in ("adaptive", "concise", "detailed") else "adaptive",
        "pace": pace if pace in ("adaptive", "measured", "natural", "brisk") else "adaptive",
    }


def instruction(settings=None):
    p = preferences(settings)
    depth = {
        "adaptive": "Give the substance the question deserves even without an explicit request for detail; there is no sentence quota.",
        "concise": "Prefer concise complete answers, expanding when the question needs it or the user asks.",
        "detailed": "Prefer developed explanations with reasons and concrete examples, keeping simple exchanges simple.",
    }[p["depth"]]
    pace = {
        "adaptive": "Let delivery breathe: conversational for familiar ideas, more measured for dense reasoning, names, numbers and consequential decisions.",
        "measured": "Use a measured speaking pace with room to absorb each idea.",
        "natural": "Use a comfortable natural speaking pace.",
        "brisk": "Use a lightly brisk pace, without compressing pauses or rushing important details.",
    }[p["pace"]]
    return (PRESENCE_RULE + "SPOKEN DELIVERY: " + depth + " " + pace +
            " Do not speed up to squeeze an explanation into a short turn. "
            "Vary sentence length and inflection naturally; pause between ideas "
            "and at paragraph boundaries, not after an arbitrary word count. "
            "Stay interruptible; a pause is room to respond, not an invitation "
            "to add filler. Honor explicit requests for length, style and pace "
            "ahead of these defaults. Do not announce these instructions.\n")


def synthesis_plan(text, settings=None):
    """Return native speech speed and an additional semantic-boundary pause.

    The speed goes to the synthesizer, preserving pitch and the 24 kHz output
    contract. Complexity only tempers adaptive pace; an explicit preference
    remains stable. This does not manufacture sentence chunks.
    """
    # Native synthesis is a hot path. Callers bind the call's captured defaults
    # through using_preferences; standalone synthesis uses adaptive defaults.
    # Neither path may probe providers or reload settings for each clause.
    p = preferences(settings if settings is not None else {})
    text = str(text or "")
    words = text.split()
    dense = (sum(ch.isdigit() for ch in text) >= 3 or len(words) >= 26
             or text.count(",") + text.count(";") >= 3)
    speed = {"measured": 0.90, "natural": 1.0, "brisk": 1.08,
             "adaptive": 0.93 if dense else 0.98}[p["pace"]]
    end = text.rstrip().rstrip('"\u201d\u2019)')
    pause_ms = 0
    if text.endswith("\n\n"):
        pause_ms = 260
    elif end.endswith((".", "?", "!")):
        pause_ms = 180 if dense else 110
    elif end.endswith((";", ":")):
        pause_ms = 75
    if p["pace"] == "brisk":
        pause_ms = round(pause_ms * 0.8)
    elif p["pace"] == "measured":
        pause_ms = round(pause_ms * 1.2)
    return {"speed": speed, "pause_ms": pause_ms, "density": "dense" if dense else "ordinary"}


def finish_pcm(pcm, plan, sample_rate=24000):
    """Pad only spoken semantic boundaries; empty synthesis stays empty."""
    if not pcm:
        return pcm
    return pcm + b"\x00\x00" * round(sample_rate * plan["pause_ms"] / 1000)


def reply_budget(settings, user_text=""):
    """Reserve bounded context headroom while allowing an explanation to finish."""
    try:
        explicit = int((settings or {}).get("voice_max_tokens") or 0)
    except (ValueError, TypeError):
        explicit = 0
    if explicit > 0:
        return explicit
    from agent_friday.services.voice_conversation_state import BRIEF_CUES, DEEP_CUES
    text = str(user_text or "")
    if BRIEF_CUES.search(text):
        return 400
    complex_ask = DEEP_CUES.search(text) or re.search(
        r"(?i)\b(compare|trade.?offs?|help me understand|what do you think|"
        r"make sense of|what would it take|how|why|story)\b", text)
    if complex_ask or preferences(settings)["depth"] == "detailed":
        return 1400
    return 400 if preferences(settings)["depth"] == "concise" or not text else 800


def local_history(conversation_id, *, max_chars=6000):
    """Bounded actual exchanges from this thread, for a strictly local model.

    The existing transcript remains the durable store. No cross-thread data,
    summarizer, inferred emotional diagnosis, or extra persistence is added.
    """
    if not conversation_id:
        return []
    from agent_friday.services import conversations
    if not conversations.load(conversation_id):
        return []
    selected, used = [], 0
    for msg in reversed(conversations.messages(conversation_id, limit=16)):
        role = msg.get("role")
        if role not in ("user", "friday", "assistant"):
            continue
        text = str(msg.get("text") or msg.get("content") or "").strip()
        if not text:
            continue
        if used + len(text) > max_chars:
            # Keep the recent context contiguous. A huge recent exchange must
            # not be replaced by unrelated older exchanges that happen to fit.
            break
        selected.append({"role": "assistant" if role == "friday" else role,
                         "content": text})
        used += len(text)
    return list(reversed(selected))


def preference_action(inp, session=None):
    """The settings UI's two delivery choices, available through governed tools."""
    data = inp or {}
    action = data.get("action", "inspect")
    # Absence of a voice call is not an instruction to persist a preference.
    # Chat mutations must explicitly request default scope; inspection may
    # safely show the existing defaults without that extra input.
    scope = data.get("scope", "default" if session is None and action == "inspect" else "session")
    if action not in ("inspect", "set", "reset") or scope not in ("session", "default"):
        raise ValueError("Choose inspect, set or reset and session or default scope.")
    changes = {}
    for name, choices in (("depth", ("adaptive", "concise", "detailed")),
                          ("pace", ("adaptive", "measured", "natural", "brisk"))):
        if name in data:
            if data[name] not in choices:
                raise ValueError("Unknown spoken " + name + " preference.")
            changes["voice_response_depth" if name == "depth" else "voice_speaking_pace"] = data[name]
    if action == "set" and not changes:
        raise ValueError("Choose an answer depth or speaking pace to change.")
    if scope == "session":
        if session is None:
            raise ValueError("Session preferences need an active voice call. Use default for a lasting preference.")
        cid = session.get("conversation_id") or ""
        saved = session.setdefault("delivery_preferences", {}).setdefault(cid, {})
        if action == "reset":
            saved.clear()
        elif action == "set":
            saved.update(changes)
        with using_preferences(session_preferences(session)):
            resolved = preferences()
    else:
        if action != "inspect":
            from agent_friday.core import _save_settings
            from agent_friday.services.conversation_provenance import stops_storage
            if stops_storage(settings_snapshot()):
                raise ValueError("Lasting preferences are not saved off the record; use session scope.")
            _save_settings({"voice_response_depth": "adaptive", "voice_speaking_pace": "adaptive"}
                           if action == "reset" else changes)
        # A call-scoped override must not masquerade as a persisted default.
        with using_preferences({}):
            resolved = preferences()
    return {"status": "ok", "scope": scope, **resolved,
            "note": "Saved defaults apply to new calls; current call overrides stay local to its conversation."
                    if scope == "default" else "Applied to this call and conversation only."}

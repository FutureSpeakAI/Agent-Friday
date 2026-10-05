"""The hand cursor and big mode, by voice and chat: the big_mode and hand_cursor tools.

With hand tracking on, the desktop is driven by a reticle that snaps to targets
(static/hand_cursor.js; HIG hand-cursor.md). Two tools give the same control to a
voice: "big mode" and "big mode off" set the large-target layout and remember the
choice; "next card", "select" and "back" move the reticle, press what it holds, and
close the topmost panel.

Both are declared once in the text registry and shared into voice
(voice_engine._VOICE_SHARED_TOOLS). A big_mode change is persisted first, through
the same settings key the panel writes, then pushed to the open desktop page
(action bus, type "hand_cursor"); the page answers with what it did. The cursor
actions are push-only: without an open page there is nothing to move.

Safety: "select" never fires a guarded action (send, delete, spend, publish,
approve). The page answers CURSOR_GUARDED and the user pinches and holds, or says
the explicit phrase the card asks for. Approval gates are untouched.

Replies are written for the ear: sentences, no key names.
"""
from __future__ import annotations

BIG_MODES = ("on", "off", "auto")
CURSOR_OPS = ("next", "previous", "select", "back")

#: What the page says, and how it is said.
_SAID = {
    "BIG_MODE_ON": "Big mode is on.",
    "BIG_MODE_OFF": "Big mode is off.",
    "CURSOR_MOVED": "On {label}.",
    "CURSOR_SELECTED": "Selected {label}.",
    "CURSOR_NO_TARGET": "There is nothing to select here.",
    "CURSOR_GUARDED": "That would {label}. Pinch and hold it, or tell me yes to the card.",
    "CURSOR_BACK": "Back.",
    "CURSOR_UNAVAILABLE": "The hand cursor is not running on this page.",
    "CURSOR_BAD_OP": "I did not understand that cursor action.",
}


def persist_big_mode(mode: str) -> None:
    from agent_friday.core import _save_settings
    _save_settings({"big_mode": mode})


def current_big_mode() -> str:
    from agent_friday.core import DEFAULT_SETTINGS, _load_settings
    try:
        v = (_load_settings() or {}).get("big_mode")
    except Exception:
        v = None
    return v if v in BIG_MODES else DEFAULT_SETTINGS.get("big_mode", "auto")


def push(action: dict) -> dict:
    """Send one hand_cursor action to the open desktop page."""
    try:
        from agent_friday.services import desktop_bus
        return desktop_bus.send([action], timeout=4.0)
    except Exception as e:  # the page is a nicety for big_mode; never a crash
        return {"delivered": False, "reason": str(e)}


def _result(sent: dict) -> dict:
    """The page's answer for a hand_cursor action, if it rode back on the ack."""
    ack = sent.get("ack") if isinstance(sent, dict) else None
    if isinstance(ack, dict):
        for v in ack.values():
            if isinstance(v, dict) and v.get("code"):
                return v
        if ack.get("code"):
            return ack
    return {}


def _say(res: dict, fallback: str) -> str:
    code = res.get("code")
    text = _SAID.get(code)
    if not text:
        return fallback
    return text.format(label=str(res.get("label") or "that").strip())


def handle_big_mode(inp: dict) -> str:
    inp = inp if isinstance(inp, dict) else {}
    mode = str(inp.get("mode") or "").strip().lower()
    if mode in ("", "status"):
        cur = current_big_mode()
        return {"on": "Big mode is always on.", "off": "Big mode is off.",
                "auto": "Big mode comes on with hand tracking."}[cur]
    if mode not in BIG_MODES:
        return "big_mode error: mode must be one of " + ", ".join(BIG_MODES)
    from agent_friday.services import setting_proposals as _sp
    held = _sp.hold("big_mode", inp, old=current_big_mode(), new=mode, consequence={
        "on": "Buttons and cards stay large, with fewer items on a screen.",
        "off": "Buttons and cards stay at their normal size, even with hand tracking.",
        "auto": "Buttons and cards grow when hand tracking is on and shrink when it is off."}[mode])
    if held:
        return held
    persist_big_mode(mode)
    sent = push({"type": "hand_cursor", "op": "big_mode", "mode": mode})
    said = {"on": "Big mode is on.", "off": "Big mode is off.", "auto": "Big mode will follow hand tracking."}[mode]
    if not sent.get("delivered"):
        return said + " It takes effect when the Friday window is open."
    return _say(_result(sent), said)


def handle_hand_cursor(inp: dict) -> str:
    inp = inp if isinstance(inp, dict) else {}
    op = str(inp.get("action") or "").strip().lower()
    if op not in CURSOR_OPS:
        return "hand_cursor error: action must be one of " + ", ".join(CURSOR_OPS)
    sent = push({"type": "hand_cursor", "op": op})
    if not sent.get("delivered"):
        return "I can't move the cursor without the Friday window open."
    return _say(_result(sent), {"next": "Moved on.", "previous": "Moved back.", "select": "Selected.", "back": "Back."}[op])


def _tool_big_mode(inp):
    return handle_big_mode(inp if isinstance(inp, dict) else {})


def _tool_hand_cursor(inp):
    return handle_hand_cursor(inp if isinstance(inp, dict) else {})


TOOLS = [
    {"name": "big_mode",
     "description": (
         "Big mode on the user's Friday desktop: a large-target layout (big buttons and "
         "cards, fewer items per screen) for driving Friday by hand or from across the "
         "room. mode=on keeps it on, off keeps it off, auto (the default) turns it on with "
         "hand tracking and off with it; no mode says how it is set. The choice is "
         "remembered. Say the result in one plain sentence."),
     "input_schema": {"type": "object", "properties": {
         "mode": {"type": "string", "enum": list(BIG_MODES) + ["status"]}}}},
    {"name": "hand_cursor",
     "description": (
         "Drive the hand cursor on the user's Friday desktop by voice: next moves the "
         "reticle to the next target in reading order (previous goes back), select presses "
         "the target it holds, back closes the topmost panel. select never fires an action "
         "that sends, deletes, spends or publishes; the user pinches and holds it, or "
         "answers the approval card. Say what happened in a few words."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(CURSOR_OPS)}},
         "required": ["action"]}},
]
RINGS = {"big_mode": 1, "hand_cursor": 1}
HANDLERS = {"big_mode": _tool_big_mode, "hand_cursor": _tool_hand_cursor}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)

"""The hologram window, by voice and chat: the hologram_window tool.

With face tracking on, the screen is a window onto the avatar: it holds its
place behind the glass and the head moves the view, so moving aside shows it
from the side and leaning in brings it closer while the view widens. The dials that shape that live in Settings (tracking) and are the
owner's to tune by ear, so one tool, declared once in the text registry and
shared into voice (voice_engine._VOICE_SHARED_TOOLS), sets them: "make the
depth stronger", "let me lean in further", "I sit eighty centimetres away",
"calibrate where I'm sitting", "reset the window".

A change is persisted first, through the same settings path the panel uses,
and then pushed to the open desktop page so it takes effect on the next frame
(the page's action bus, type "tracking"). Calibration is the one thing only
the page can do, since only the page knows how wide the face is right now, so
that op is pushed and the page answers with what it measured.

Replies are written for the ear: sentences, numbers as words, no key names.
"""
from __future__ import annotations

import json

ACTIONS = ("status", "set", "calibrate", "reset")

#: The dials a voice may set, with their slider ranges. Anything outside the
#: range is clamped, never refused: "make it huge" should land on the maximum.
DIALS = {
    "depth_strength": (0.0, 2.5),
    "zoom_in_max": (1.0, 2.5),
    "zoom_out_max": (1.0, 2.5),
    "parallax_strength": (0.0, 2.5),
    "head_smoothing": (0.0, 1.0),
    "head_response": (0.0, 1.0),
    "holo_cues": (0.0, 1.0),
    "viewing_distance_cm": (30.0, 120.0),
    "screen_width_cm": (0.0, 120.0),
}

#: How each dial is spoken.
SPOKEN = {
    "depth_strength": "the depth effect",
    "zoom_in_max": "how far you can lean in",
    "zoom_out_max": "how far you can lean back",
    "parallax_strength": "the parallax",
    "head_smoothing": "head smoothing",
    "head_response": "head response",
    "holo_cues": "the holographic cues",
    "viewing_distance_cm": "the viewing distance",
    "screen_width_cm": "the screen width",
}

#: Dials the panel keeps across a reset, and so does a spoken one: the dock's
#: depth and the debug overlay belong to other tabs, and the calibrated
#: distance, the viewing distance and the screen width are about the seat,
#: the camera and the screen rather than the feel.
KEPT_ON_RESET = ("dock_depth", "debug_overlay", "neutral_face_width",
                 "viewing_distance_cm", "screen_width_cm")

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve "
         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def _say_int(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " and " + _say_int(n % 100))
    return str(n)


def say_number(v) -> str:
    """0.5 -> 'zero point five', 1.8 -> 'one point eight', 2 -> 'two'."""
    v = round(float(v), 2)
    whole = int(abs(v))
    frac = int(round((abs(v) - whole) * 100))
    if frac % 10 == 0:
        frac //= 10
        digits = _ONES[frac] if frac else ""
    else:
        digits = " ".join(_ONES[int(c)] for c in "%02d" % frac)
    out = _say_int(whole)
    if digits:
        out += " point " + digits
    return ("minus " if v < 0 else "") + out


def _defaults() -> dict:
    from agent_friday.core import DEFAULT_SETTINGS
    return dict(DEFAULT_SETTINGS["tracking"])


def current() -> dict:
    """The tracking dials as stored, over the defaults."""
    from agent_friday.core import _load_settings
    cur = _defaults()
    try:
        stored = (_load_settings() or {}).get("tracking")
    except Exception:
        stored = None
    if isinstance(stored, dict):
        cur.update(stored)
    return cur


def persist(tracking: dict) -> None:
    """Store the whole tracking object: the settings merge replaces it whole."""
    from agent_friday.core import _save_settings
    _save_settings({"tracking": tracking})


def push(action: dict) -> dict:
    """Send one tracking action to the open desktop page."""
    try:
        from agent_friday.services import desktop_bus
        return desktop_bus.send([action], timeout=4.0)
    except Exception as e:  # the page is a nicety for `set`; never a crash
        return {"delivered": False, "reason": str(e)}


def _clamp(key: str, value) -> float | None:
    lo, hi = DIALS[key]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:                                     # NaN
        return None
    return max(lo, min(hi, v))


def _status_text(t: dict) -> str:
    depth = float(t.get("depth_strength", 1.0))
    if depth <= 0:
        lead = "Leaning in and out is off; the view follows you only from side to side. "
    else:
        lead = ("Leaning in can bring you up to %s times nearer the glass and leaning back "
                "up to %s times farther, with the depth effect at %s. "
                % (say_number(t.get("zoom_in_max", 1.8)), say_number(t.get("zoom_out_max", 1.5)),
                   say_number(depth)))
    width = float(t.get("screen_width_cm") or 0)
    lead += ("It is scaled for %s centimetres from a screen %s. "
             % (say_number(t.get("viewing_distance_cm", 60)),
                "%s centimetres wide" % say_number(width) if width > 0 else "it measures itself"))
    cal = float(t.get("neutral_face_width") or 0)
    tail = ("It is calibrated to where you sit." if cal > 0.02
            else "It is not calibrated yet; say calibrate while you sit normally.")
    return lead + "Parallax is at %s, smoothing %s, response %s. " % (
        say_number(t.get("parallax_strength", 1.0)), say_number(t.get("head_smoothing", 0.35)),
        say_number(t.get("head_response", 0.5))) + tail


def _not_on_screen(sent: dict) -> str:
    return ("Saved; it takes effect when the Friday window is open (%s)."
            % sent.get("reason", "no page is open"))


def handle(inp: dict) -> str:
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "status").strip().lower()
    if action not in ACTIONS:
        return "hologram_window error: action must be one of " + ", ".join(ACTIONS)

    if action == "status":
        return _status_text(current())

    if action == "calibrate":
        sent = push({"type": "tracking", "op": "calibrate"})
        if not sent.get("delivered"):
            return ("I can't calibrate without the Friday window open: %s."
                    % sent.get("reason", "no page is open"))
        got = ((sent.get("ack") or {}).get("result") or {}).get("calibrated")
        if not got:
            return ("I can't see a face right now. Turn the hologram on, look at the "
                    "camera from where you normally sit, and ask me to calibrate again.")
        return "Calibrated to where you're sitting now; that is your normal distance."

    tracking = current()
    if action == "reset":
        fresh = _defaults()
        for k in KEPT_ON_RESET:
            if k in tracking:
                fresh[k] = tracking[k]
        persist(fresh)
        sent = push({"type": "tracking", "tracking": fresh})
        text = "The window is back to its defaults. " + _status_text(fresh)
        return text if sent.get("delivered") else text + " " + _not_on_screen(sent)

    # set
    relative = bool(inp.get("relative"))
    changed = []
    for key in DIALS:
        if key not in inp or inp[key] is None:
            continue
        raw = inp[key]
        if relative:
            try:
                raw = float(tracking.get(key, 0)) + float(raw)
            except (TypeError, ValueError):
                return "hologram_window error: %s must be a number" % key
        v = _clamp(key, raw)
        if v is None:
            return "hologram_window error: %s must be a number" % key
        tracking[key] = round(v, 3)
        changed.append(key)
    if not changed:
        return ("hologram_window error: set needs at least one of "
                + ", ".join(DIALS) + " (numbers; relative=true adds to the current value)")
    persist(tracking)
    sent = push({"type": "tracking", "tracking": tracking})
    said = ", ".join("%s is now %s" % (SPOKEN[k], say_number(tracking[k])) for k in changed)
    said = said[0].upper() + said[1:] + "."
    return said if sent.get("delivered") else said + " " + _not_on_screen(sent)


def _tool_hologram_window(inp):
    return handle(inp if isinstance(inp, dict) else {})


TOOLS = [
    {"name": "hologram_window",
     "description": (
         "The hologram window on the user's Friday desktop: with face tracking on, "
         "the 3D avatar holds its place behind the screen and the user's head moves "
         "the view: moving aside shows it from the side, leaning in brings it closer "
         "while the view widens. action=status says how it is set; set changes any "
         "of the dials given (depth_strength 0-2.5, 1 means the view follows their "
         "real distance; zoom_in_max and zoom_out_max 1-2.5, how many times nearer "
         "or farther the eye may go; parallax_strength 0-2.5, 1 is true to life; "
         "viewing_distance_cm 30-120 and screen_width_cm 0-120, 0 meaning measured "
         "from the display; head_smoothing and head_response 0-1; holo_cues 0-1), "
         "relative=true adds to the current value ('a bit stronger' is about +0.25); "
         "calibrate takes where they sit right now as the normal distance, which "
         "needs the hologram on and their face in the camera; reset returns the feel "
         "to defaults and keeps the calibration, viewing distance and screen width. "
         "Say the result in one plain sentence."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)},
         "depth_strength": {"type": "number"},
         "zoom_in_max": {"type": "number"},
         "zoom_out_max": {"type": "number"},
         "parallax_strength": {"type": "number"},
         "head_smoothing": {"type": "number"},
         "head_response": {"type": "number"},
         "holo_cues": {"type": "number"},
         "viewing_distance_cm": {"type": "number"},
         "screen_width_cm": {"type": "number"},
         "relative": {"type": "boolean"}},
         "required": ["action"]}},
]
RINGS = {"hologram_window": 1}
HANDLERS = {"hologram_window": _tool_hologram_window}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)

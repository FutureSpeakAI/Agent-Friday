"""Call mode, by voice and chat: the call_mode tool.

One tool, declared once in the text registry and shared into voice
(voice_engine._VOICE_SHARED_TOOLS): "are you standing back?", "stand back,
I'm on a call", "the call is over", "always stand back on calls", "ask me
first", "never do that". It reads and sets the call_mode setting through the
same settings path the panel uses and throws the same switch the detector
does (services/call_watch). Replies are written for the ear.
"""
from __future__ import annotations

ACTIONS = ("status", "set_mode", "start", "end")
MODE_SAID = {"automatic": "stand back on its own when a call starts",
             "ask": "ask you first when a call starts",
             "off": "never stand back on its own"}


def _watch():
    from agent_friday.services import call_watch
    return call_watch.watch()


def _status_text(snap: dict) -> str:
    mode = snap.get("mode") or "automatic"
    lead = ("I'm standing back for your call%s: the camera, the mic and the GPU are "
            "yours, and the scene is still. " % (" in " + snap["app"] if snap.get("app") else "")
            if snap.get("active") else "I'm not standing back right now. ")
    return lead + "Call mode is set to %s." % MODE_SAID.get(mode, mode)


def handle(inp: dict) -> str:
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "status").strip().lower()
    if action not in ACTIONS:
        return "call_mode error: action must be one of " + ", ".join(ACTIONS)
    w = _watch()
    if action == "status":
        return _status_text(w.snapshot())
    if action == "set_mode":
        from agent_friday.services.call_watch import MODES
        mode = str(inp.get("mode") or "").strip().lower()
        if mode not in MODES:
            return "call_mode error: mode must be one of " + ", ".join(MODES)
        from agent_friday.core import _load_settings, _save_settings
        from agent_friday.services import setting_proposals as _sp
        held = _sp.hold("call_mode", inp, old=(_load_settings() or {}).get("call_mode") or "automatic", new=mode,
                        consequence="From then on I would %s." % MODE_SAID[mode])
        if held:
            return held
        _save_settings({"call_mode": mode})
        return "Done. From now on I'll %s." % MODE_SAID[mode]
    if action == "start":
        snap = w.start(str(inp.get("app") or ""))
        return ("Standing back now: the camera, the mic and the GPU are yours and the "
                "scene is still. Say the call is over when you're done, or press the chip.")
    snap = w.end()
    return "Welcome back. The camera, the scene and the brain are coming back now."


def _tool_call_mode(inp):
    return handle(inp if isinstance(inp, dict) else {})


TOOLS = [
    {"name": "call_mode",
     "description": (
         "Friday standing back for a call on this computer: the webcam and mic are "
         "released, the 3D scene holds still, the local brain seat is parked so its "
         "VRAM and RAM are free, and a chip on screen says so. action=status says "
         "whether Friday is standing back and how call mode is set; start stands "
         "back now (optional app, e.g. 'Zoom'); end brings everything back; set_mode "
         "changes the setting: automatic (stand back on its own when a call starts, "
         "recommended), ask (ask first, once per call), or off. Say the result in one "
         "plain sentence."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)},
         "mode": {"type": "string", "enum": ["automatic", "ask", "off"]},
         "app": {"type": "string"}},
         "required": ["action"]}},
]
RINGS = {"call_mode": 1}
HANDLERS = {"call_mode": _tool_call_mode}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)

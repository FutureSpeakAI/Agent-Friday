"""`local_voice_readiness`: "can you run voice locally now?" and "measure local voice".

The spoken and typed side of Settings > Voice > Local voice. One tool, two
actions, both in words a voice can say:

  * status (default): whether local voice runs on this PC, from the stored
    capability snapshot. Not measured is said as "not measured", never as
    "can't"; a refusal says why.
  * measure: queue the measurement as an idle background job
    (services/local_voice_readiness). It never runs during a call, and it
    never changes the voice engine: switching is the owner's own choice.

Registered like services/queue_tools: handler and ring always, and the schema
loaded on demand (`load_tools` finds it from the owner's own words), so it costs
nothing on a turn that does not ask for it. The voice front does not declare it
(its contract is full) and the Laya router only runs tools in that contract, so
voice reaches it the way it reaches `local_work`: through ask_friday.
"""
from __future__ import annotations

import json

NAME = "local_voice_readiness"

TOOLS = [
    {"name": NAME,
     "description": (
         "Can voice run locally on this PC, and measure it. action=status (default) says "
         "whether local voice is measured and runs well ('can you run voice locally now?'); "
         "action=measure queues the measurement for when the owner is away, never during a "
         "call ('measure local voice'). Say the `spoken` sentence as it is. It never switches "
         "the voice engine."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["status", "measure"]}}}},
]

#: Ring 1: it can queue an idle job, and nothing else.
RINGS = {NAME: 1}


def _minutes(seconds: float) -> str:
    m = max(1, round(float(seconds) / 60.0))
    return "%d minute%s" % (m, "" if m == 1 else "s")


def _status_sentence() -> str:
    from agent_friday.services import local_voice_readiness as lvr
    from agent_friday.services import local_scheduler as ls
    state = lvr.voice_state()
    job = lvr.job_status()
    waiting = (" A measurement is queued; it starts after %s away."
               % _minutes(ls.idle_window_s())) if job.get("wanted") else ""
    if state == lvr.CAPABLE:
        said = lvr._numbers_sentence(lvr.measured_numbers())
        return ("Yes. Local voice runs well on this PC%s." % ((": " + said) if said else "")
                + " It is private and costs nothing. Switching is your call, in Settings, Voice.")
    if state == lvr.UNKNOWN:
        return ("I have not measured local voice on this PC yet, so I cannot say. Ask me to "
                "measure local voice and I will do it after you have been away for %s. It never "
                "runs during a call.%s" % (_minutes(ls.idle_window_s()), waiting))
    why = lvr.voice_why()
    return "Not yet%s.%s" % ((": " + why) if why else "", waiting)


def _tool_local_voice_readiness(inp=None):
    inp = inp if isinstance(inp, dict) else {}
    action = str(inp.get("action") or "status").strip().lower()
    from agent_friday.services import local_voice_readiness as lvr
    if action == "measure":
        from agent_friday.services import local_scheduler as ls
        lvr.request_measure("voice")
        spoken = ("Okay. I will measure local voice after you have been away for %s. It will "
                  "not run during a call, and it changes nothing about how you talk to me. "
                  "Ask again afterwards." % _minutes(ls.idle_window_s()))
        return json.dumps({"spoken": spoken, "kind": "measure_queued"})
    return json.dumps({"spoken": _status_sentence(), "kind": "status",
                       "state": lvr.voice_state()})


HANDLERS = {NAME: _tool_local_voice_readiness}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)

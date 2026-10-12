"""`local_voice_readiness`: "can you run voice locally now?", "measure local voice", and the cloud voice choice.

The spoken and typed side of Settings > Voice > Local voice. One tool, four
actions, all in words a voice can say:

  * status (default): whether local voice runs on this PC, from the stored
    capability snapshot. Not measured is said as "not measured", never as
    "can't"; a refusal says why.
  * measure: queue the measurement as an idle background job
    (services/local_voice_readiness). It never runs during a call.
  * cloud: "use cloud voice". It ASKS: "just this call, or always?" Nothing
    switches. Local-only mode refuses, with its reason.
  * answer: the owner's answer to a question that is waiting: "just this call",
    "always" (cloud voice), "switch back", "keep cloud" (the one switch-back
    offer). It runs only on the owner's own words in this turn: the words must
    give that answer, a model's choice alone changes nothing, and a turn that has
    read untrusted content changes nothing.

Registered like services/queue_tools: handler and ring always, and the schema
loaded on demand (`load_tools` finds it from the owner's own words), so it costs
nothing on a turn that does not ask for it. The voice front does not declare it
(its contract is full) and the Laya router only runs tools in that contract, so
voice reaches it the way it reaches `local_work`: through ask_friday, which hands
this tool the caller's last spoken words (`voice_owner_words`).
"""
from __future__ import annotations

import json

NAME = "local_voice_readiness"

TOOLS = [
    {"name": NAME,
     "description": (
         "Can voice run locally on this PC, measure it, and the cloud voice choice. action=status "
         "(default) says whether local voice is measured and runs well ('can you run voice locally "
         "now?'); action=measure queues the measurement for when the owner is away ('measure local "
         "voice'); action=cloud asks 'just this call, or always?' ('use cloud voice'); action=answer "
         "takes the owner's answer to that or to the switch-back offer (choice=call, always, "
         "switch_back, keep_cloud), only from their own words. Say the `spoken` sentence as it is. "
         "Never choose for them."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["status", "measure", "cloud", "answer"]},
         "choice": {"type": "string", "enum": ["call", "always", "switch_back", "keep_cloud", "cancel"]}}}},
]

#: Ring 1: it can queue an idle job and carry out an answer the owner just spoke.
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


def _owner_words() -> str:
    """The owner's own words for this turn, or "" when there are none to trust.

    A typed turn and a voice tool call carry `owner_text`; a question relayed
    through ask_friday carries the caller's last words as `voice_owner_words`.
    A turn that has read untrusted content (a page, a mail, a tool result) has no
    owner words for this purpose, whatever text it holds."""
    try:
        from agent_friday.services import agent, slice_head
        if slice_head.is_tainted(agent._taint_mod.CURRENT_KEY.get()):
            return ""
        words = str(agent._CURRENT_OWNER_TEXT.get() or "")
        if not words:
            ctx = agent._CURRENT_TOOL_CONTEXT.get() or {}
            if str(ctx.get("surface") or "").startswith("voice"):
                words = str(ctx.get("voice_owner_words") or "")
        return words
    except Exception:
        return ""


def _pending_kind(lvr) -> str:
    """Which question is waiting: `scope` (just this call / always), `switch_back`, or ''."""
    if lvr.cloud_question_card() is not None:
        return "scope"
    if lvr.switch_back_card() is not None:
        return "switch_back"
    return ""


def _ask_again(lvr, kind: str) -> str:
    if kind == "scope":
        return lvr.spoken_question()
    card = lvr.switch_back_card() or {}
    return ((card.get("body") or "Local voice is ready again. Switch back?")
            + " Say switch back, or keep cloud.")


def _answer(inp: dict) -> dict:
    from agent_friday.services import local_voice_readiness as lvr
    kind = _pending_kind(lvr)
    if not kind:
        return {"spoken": "There is no cloud voice question waiting.", "kind": "no_question"}
    words = _owner_words()
    heard = lvr.classify_answer(words)
    claimed = str(inp.get("choice") or "").strip().lower()
    if kind == "scope" and lvr.asked_in_these_words(words):
        heard = None                 # the same breath that asked is not the answer
    if heard is None or (claimed and claimed != heard):
        # Not the owner's words, or words that give no single answer: nothing changes, and it is asked again.
        return {"spoken": "I did not catch which. " + _ask_again(lvr, kind), "kind": "asked_again",
                "pending": kind}
    if kind == "scope" and heard in (lvr.SCOPE_CALL, lvr.SCOPE_ALWAYS, "cancel"):
        res = lvr.answer_cloud_switch(heard)
    elif kind == "switch_back" and heard in ("switch_back", "keep_cloud"):
        res = lvr.decide_switch_back(heard)
    else:
        return {"spoken": "That answer is for the other question. " + _ask_again(lvr, kind),
                "kind": "asked_again", "pending": kind}
    return {"spoken": res.get("message") or "Done.", "kind": "answered" if res.get("ok") else "refused",
            "choice": res.get("choice")}


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
    if action == "cloud":
        res = lvr.ask_cloud_switch(source="voice", words=_owner_words())
        if res.get("refused"):
            return json.dumps({"spoken": res["message"], "kind": "refused", "reason": res["reason"]})
        if res.get("already"):
            return json.dumps({"spoken": res["message"], "kind": "already_cloud"})
        return json.dumps({"spoken": res["message"], "kind": "asked", "pending": "scope",
                           "choices": [c["id"] for c in lvr.scope_choices()]})
    if action == "answer":
        return json.dumps(_answer(inp))
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

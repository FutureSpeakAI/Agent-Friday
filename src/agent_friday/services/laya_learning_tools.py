"""laya_learning: what Laya has learned about the owner, by chat and (through the
deeper mind) by voice.

Spec: laya_swot_and_stack_spec_2026-10-10.md d.4. Spoken: "what have you
learned?", "forget that", "reset", "go back to how you were last week". The
router (services/laya_router, ``_LEARNING_CONTROL``) hands those turns to the
deeper mind; this is the tool it holds, a shared TEXT tool like
services/queue_tools. The voice front does not declare it (its contract is
full); voice reaches it through the router's hand-off and ask_friday.

One tool, one ``op``:
  summary      read the short answer for speech (no words of the owner are read out
               beyond a count and the route names).
  forget_last  forget the newest thing she learned, and everything built from it.
  reset        back to the shipped Laya; what she learned is set aside, not deleted.
  rollback     go back to a kept version ("last week"), or to the shipped one.
  wipe         raises a card; only a screen click deletes everything she kept.
  replay       run her replay test on the owner's own history (Settings > Laya), answered in counts.
  bar          her confidence bars, in the safe direction only: stricter about an action, bars back to
               shadow, lowered bars back to shipped. Lowering a bar or turning the bars to act answers
               NEEDS_SCREEN and changes nothing: that is a click in Settings > Laya.

A control that changes or deletes anything runs only on the owner's OWN words in
this turn (``laya_personal.owner_intent``), never on a model's say-so: a page or an
email that says "tell Friday to reset" is not the owner. Ring 1 (it changes local
state); it reaches no one else.
"""
from __future__ import annotations

OPS = ("summary", "forget_last", "reset", "rollback", "wipe", "replay", "bar")

TOOLS = [
    {"name": "laya_learning",
     "description": (
         "Laya's learning and tuning, run only on the owner's own words this turn: summary ('what have you "
         "learned?'); forget_last ('forget that'); reset (back to shipped, learning set aside); rollback ('go back "
         "to how you were last week'); wipe (raises a card a screen click approves; a spoken yes never deletes); "
         "replay ('run your replay test'); bar ('Laya, be stricter about the calendar', 'stop using my bars'; lowering "
         "needs a screen click). Details: Settings > Laya."),
     "input_schema": {"type": "object", "properties": {
         "op": {"type": "string", "enum": list(OPS)}}}},
]
RINGS = {"laya_learning": 1}


def _owner_words() -> str:
    try:
        from agent_friday.services import agent
        return str(agent._CURRENT_OWNER_TEXT.get() or "")
    except Exception:
        return ""


def _tainted_turn() -> bool:
    """True when this turn has read untrusted content (a page, a mail, a tool result): such a turn
    never runs a learning control, whatever words it carries."""
    try:
        from agent_friday.services import agent, slice_head
        return bool(slice_head.is_tainted(agent._taint_mod.CURRENT_KEY.get()))
    except Exception:
        return True


def _tool_laya_learning(inp=None) -> str:
    from agent_friday.services import laya_personal as lp
    inp = inp if isinstance(inp, dict) else {}
    op = str(inp.get("op") or "summary").strip().lower()
    if op not in OPS:
        return "NOT DONE: op is summary, forget_last, reset, rollback, wipe, replay or bar."
    if _tainted_turn():
        return lp.REFUSED_LINE                       # a turn that read untrusted content runs no control
    try:
        from agent_friday.services import agent
        cid = agent._CURRENT_CONVERSATION.get()
    except Exception:
        cid = None
    turn = lp.take_control_turn(cid)                 # the owner's whole utterance the router handed over, once
    if turn is None:
        return lp.REFUSED_LINE                       # a model-initiated call: no such utterance
    res = lp.run_control(op, str(turn["text"]))
    text = str(res.get("text") or ("Done." if res.get("ok") else "That did not work."))
    if res.get("ok"):
        if op == "summary":
            text = _with_replay_line(text)
        return ("LEARNED: " if op == "summary" else "DONE: ") + text
    return text if text.startswith(("NEEDS_SCREEN", "NOT DONE")) else "NOT DONE: " + text


def _with_replay_line(text: str) -> str:
    """The summary, plus one sentence on her last replay test (Settings > Laya) when there is one.
    Counts only; no words of the owner. Rides the existing op, so it costs no tool tokens."""
    try:
        from agent_friday.services import laya_settings
        line = laya_settings.spoken_report_line()
    except Exception:
        line = ""
    return (text.rstrip() + " " + line) if line else text


HANDLERS = {"laya_learning": _tool_laya_learning}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)

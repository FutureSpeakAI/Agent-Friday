"""Text chat: system one chooses the tool, the model answers (DA-0).

The same ``laya_router.route`` that routes the local voice front runs before a
typed chat turn when the owner chose it (setting ``chat_tool_routing`` =
"laya"; unchosen means the model calls its own tools, exactly as before).

  LOCAL MODEL  tool    the router's tool runs through ``agent._execute_tool``
                       (the choke point every model call takes) and its result
                       reaches the model fenced as untrusted data, so the model
                       answers from it. The model keeps its catalogue.
               ask     the router's one question is the whole turn.
               defer / no_tool
                       the model's own turn, native tool calling untouched.
  CLOUD MODEL  only a ``tool`` the router is highly sure of (fast path); the
               result is scrubbed like any cloud tool result, and the model
               still holds its full catalogue and may call more. Nothing else
               changes for a cloud turn: no ask, no downgrade.

The router decides; this module never builds a command as free text, and it
has no way to run a tool except the governed runner. The receipt (layer,
confidence, runner-up, whether it ran) is written into the turn's TurnPlan and
so appears in the turn receipt. Off the record the router writes no log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

_log = logging.getLogger("friday.chat_tool_routing")

SETTING = "chat_tool_routing"
MODES = ("laya", "model")

#: A cloud model keeps its own judgement: the router may only save it a round
#: when it is this sure (the sealed tier-0 patterns report 0.95).
CLOUD_MIN_CONFIDENCE = 0.9

#: Router layers that read the owner's screen or item index.
_SCREEN_LAYERS = frozenset({"point_act", "resolver", "safe_write"})


def mode(settings: Optional[dict]) -> str:
    """"laya" only when chosen; anything else, including unchosen, is "model"."""
    v = str((settings or {}).get(SETTING) or "").strip().lower()
    return "laya" if v == "laya" else "model"


def enabled(settings: Optional[dict]) -> bool:
    return mode(settings) == "laya"


@dataclass
class TurnRoute:
    route: object                 # laya_router.Route
    action: str                   # "run" | "ask" | "model"
    cloud: bool = False


def decide(message: str, data: Optional[dict], *, settings: Optional[dict],
           held: Sequence[str], local: bool, off_record: bool = False,
           trusted: Optional[bool] = None,
           route_fn: Optional[Callable] = None,
           conversation_id: Optional[str] = None) -> Optional[TurnRoute]:
    """Route one typed turn, or None when this path is not in play.

    None means "take today's path" and the router is never called: the setting
    is not "laya", or the turn is not the owner's own short line typed in the
    app on this PC (reflex_chat.trusted_surface), or the router failed.
    """
    try:
        if not enabled(settings):
            return None
        if trusted is None:
            from agent_friday.services import reflex_chat
            trusted = reflex_chat.trusted_surface(data, message)
        if not trusted:
            return None
        from agent_friday.services import laya_router as _lr
        own_route = route_fn is None or route_fn is _lr.route
        if route_fn is None:
            if not _lr.ready():
                _lr.warm()                  # the encoder loads off the turn's path
            from agent_friday.services import laya as _laya
            route_fn = _laya.route_turn          # the contract: one deadline, a receipt, sealed rules on a hang
        r = route_fn(message, tools=list(held), surface="chat", log=not off_record,
                     trusted=bool(local), quiet=False, conversation_id=conversation_id,   # no screen rungs for a cloud seat
                     **({"safe_writes": False} if own_route else {}))
        # Safe writes ride the main composer (reflex_chat) only: never here, whatever route_fn returned.
        if local:
            action = {"tool": "run", "ask": "ask"}.get(r.decision, "model")
        else:
            # A cloud model never gets a point-and-act or resolver result: it names items on the owner's screen
            # ("opened <title>. Other matches: ..."). Those layers answer on a local seat only, as voice does.
            action = ("run" if r.decision == "tool" and r.layer not in _SCREEN_LAYERS
                      and float(r.confidence or 0.0) >= CLOUD_MIN_CONFIDENCE else "model")
        if getattr(r, "layer", "") == "safe_write":
            return None                     # never carried here, whatever a route_fn returned
        if action == "ask" and not r.question:
            action = "model"
        return TurnRoute(r, action, cloud=not local)
    except Exception as e:  # noqa: BLE001
        _log.warning("chat tool routing failed, taking the model's turn: %s", e)
        return None


def ask_result(tr: TurnRoute) -> dict:
    """The router's one question as an instant reply: a template line (a rule
    asked, never a model) with its Laya receipt."""
    from agent_friday.services import laya_templates
    ask = laya_templates.rule_question(tr.route.question, "laya_router")
    return {"reply": ask.text, "tool_trace": [], "actions": [], "laya": ask.receipt()}


def run(tr: TurnRoute, *, session_ctx: Optional[dict], pii_lookup=None,
        run_tool: Optional[Callable[[str, dict], str]] = None) -> Optional[dict]:
    """Execute a ``run`` route through the governed runner.

    Returns the tool_trace entry {"name", "input", "result"}, or None when
    nothing ran (a runner that raised is the model's turn, not a failed one).
    The session context is the turn's own, with ``chosen_by`` naming the route
    layer, so the governance receipt says who proposed the call.
    """
    r = tr.route
    if tr.action != "run" or r.decision != "tool" or not r.tool:
        return None
    ctx = dict(session_ctx or {})
    # The owner mark (gate: ctx["owner_utterance"]) is the voice router's alone. A typed line routed here
    # never carries it, so "quiet until morning" typed in chat is a card, exactly as a model's call is.
    ctx.pop("owner_utterance", None)
    ctx["chosen_by"] = "laya_router:" + str(r.layer or "rule")
    if run_tool is None:
        def run_tool(name, args):
            from agent_friday.services import agent
            return agent._execute_tool(name, args, pii_lookup=pii_lookup, session_ctx=ctx)
    try:
        from agent_friday.services import laya_router as _lr
        result = _lr.execute(r, run_tool)
    except Exception as e:  # noqa: BLE001
        _log.warning("routed tool %s raised, taking the model's turn: %s", r.tool, e)
        return None
    return {"name": r.tool, "input": dict(r.args),
            "result": str(result if result is not None else "")}


def result_block(tr: TurnRoute, entry: dict) -> str:
    """The result as the model reads it: the voice path's fenced block."""
    from agent_friday.services import voice_front
    return voice_front.result_block(tr.route.label, entry.get("result") or "")


def attach_block(messages: list, block: str) -> bool:
    """Append ``block`` to the newest user turn. False when there is none."""
    if not messages or not block:
        return False
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return False
    cur = last.get("content")
    if isinstance(cur, str):
        last["content"] = cur + block
    elif isinstance(cur, list):
        last["content"] = list(cur) + [{"type": "text", "text": block.lstrip("\n")}]
    else:
        return False
    return True


def receipt(tr: TurnRoute, *, ran: Optional[str], outcome: str) -> dict:
    """The route's receipt in the shape the voice path writes (laya_router.
    surface_receipt), plus where it was and what came of it."""
    from agent_friday.services import laya_router as _lr
    return dict(_lr.surface_receipt(tr.route, ran=ran), surface="chat", mode="laya",
                seat="cloud" if tr.cloud else "local", outcome=outcome)


def record(turn_id: Optional[str], tr: TurnRoute, *, ran: Optional[str], outcome: str) -> None:
    """Put the route's receipt in this turn's TurnPlan (and so its receipt)."""
    try:
        from agent_friday.services import turn_plan as _tp
        if not turn_id:
            return
        _tp.attach_route(turn_id, receipt(tr, ran=ran, outcome=outcome),
                         "laya_router: %s (%s)" % (tr.route.decision, tr.route.layer))
    except Exception as e:  # noqa: BLE001
        _log.debug("chat route receipt not recorded: %s", e)

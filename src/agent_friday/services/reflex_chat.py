"""Chat turns answered without a model round: the reflex path (spec 9.5f, U-7).

Two things are done here, and nothing else:

  1. APPROVAL REPLIES (default on). A pending approval question is answered by
     a plain "yes" in chat. Today the deterministic claim path
     (`agent.prepare_confirmation_ctx` + `resume_confirmed_action`) already
     runs the stored action with no model to regenerate it, but it runs after
     the turn's whole preparation (recall, routing, prompt). This runs it first.
     The outcome is the same one, by the same code.

  2. "OPEN THE X" (opt-in, `reflex_open_enabled`). `laya_resolver.resolve`
     turns a request for one item into a sealed command; the same opt-in
     covers POINT-AND-ACT ("open the second one", "select these"), which
     names rows of the list on screen (`services/point_act`) and shows
     them; a change ("archive these") is never run here, it is the model's
     turn. The command is run by
     `agent._execute_tool`, so `action_gate.authorize` sees it exactly as it
     sees a model's call. The resolver decides; this module never builds a
     command as free text.

THE RULES (a turn is a reflex only when every one holds)

  TRUSTED SURFACE  The owner typed it in the app, on this PC. A request that
                   came through a tunnel or proxy, a channel (SMS, Telegram,
                   Discord), a live call, the Local API, or any turn carrying
                   an image, a screenshot or an attachment is never a reflex.
                   Local voice has its own route and is out of scope here; a
                   paired phone reaches Friday through the tunnel and so is
                   not local (pinned: effectively app + local voice).
  SHORT, PLAIN     One short line. Pasted or fetched content is not a command.
  CONDITIONAL      "yes, but ..." never approves. It is a change request: it
                   goes to the normal turn with the condition in the words the
                   model reads, and the model raises a revised card.
  GOVERNED         Every command runs through `agent._execute_tool`. A refusal
                   or a card is reported as that, never as success.
  HONEST           The reply says it was instant and used no model; the turn
                   receipt says `chosen_by` = "rule" or "resolver".

NO CONTENT       Counters and plans hold labels and names, never the words of a
                 turn, an item's title or a tool argument. Off the record
                 nothing is written.

Ring <= 1 templates (mark read, timers, setting toggles) are NOT in this wave.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter
from typing import Any, Dict, Optional

from agent_friday.services import laya_templates as _laya_templates

_lt = _laya_templates      # every reply below is a template line with a receipt

_log = logging.getLogger("friday.reflex_chat")

APPROVAL_SETTING = "reflex_approval_replies"     # default on: same outcome, no model round
OPEN_SETTING = "reflex_open_enabled"             # opt-in; None until the owner chooses

#: A reflex is one short line the owner typed. Longer text is content.
MAX_CHARS = 200

#: Where a request says it came from, when the client says so. Any of these
#: means the turn is not the owner's own typing in the app.
UNTRUSTED_SOURCES = frozenset({
    "sms", "telegram", "discord", "channel", "channels", "live_call", "call",
    "phone", "local_api", "api", "webhook", "email", "mail", "remote", "tunnel",
})
_SOURCE_KEYS = ("source", "surface", "origin", "channel", "via")
#: A body field that carries something other than the owner's line.
_CONTENT_KEYS = ("image", "screenshot", "images", "attachments", "files", "attachment")

_BRAIN_REASONS_NORMAL = frozenset({"no_open_verb", "not_command", "not_an_item",
                                   "no_candidates"})

_lock = threading.Lock()
_counts: "Counter[str]" = Counter()


# ---------------------------------------------------------------------------
#  TRUST
# ---------------------------------------------------------------------------

def _request_is_local() -> bool:
    """True when the current Flask request came from this machine, unproxied."""
    try:
        from agent_friday import core
        return bool(core._is_local_request())
    except Exception:
        return False


def trusted_surface(data: Optional[dict], message: str, *, local: Optional[bool] = None) -> bool:
    """Is this turn the owner's own short line, typed in the app on this PC?"""
    data = data or {}
    if not (_request_is_local() if local is None else local):
        return False
    for key in _SOURCE_KEYS:
        if str(data.get(key) or "").strip().lower() in UNTRUSTED_SOURCES:
            return False
    if any(data.get(k) for k in _CONTENT_KEYS):
        return False
    text = str(message or "")
    if not text.strip() or len(text) > MAX_CHARS or "\n" in text.strip():
        return False
    return True


# ---------------------------------------------------------------------------
#  COUNTERS AND THE PLAN (labels only)
# ---------------------------------------------------------------------------

def _off_record() -> bool:
    try:
        from agent_friday.services import off_record as _off
        return bool(_off.active())
    except Exception:
        return False


def count(outcome: str) -> None:
    """Count one reflex outcome. Labels only; nothing while off the record."""
    if _off_record():
        return
    try:
        with _lock:
            _counts[outcome] += 1
            from agent_friday.core import FRIDAY_DIR
            path = FRIDAY_DIR / "runtime" / "reflex" / "reflex_chat.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": round(time.time(), 3), "outcome": outcome},
                                   separators=(",", ":")) + "\n")
    except Exception as e:
        _log.debug("reflex outcome not recorded: %s", e)


def stats() -> Dict[str, int]:
    """Outcomes since this server started (for Settings)."""
    with _lock:
        return dict(_counts)


def reset_stats() -> None:
    with _lock:
        _counts.clear()


def _record_plan(turn_id: Optional[str], *, shape: str, reason: str,
                 reflex: Optional[dict], proposal: Optional[str] = "reflex") -> None:
    """This turn's TurnPlan: who decided, no words of the turn. Never raises."""
    try:
        from agent_friday.services import turn_plan as _tp
        if not turn_id:
            return
        _tp.begin_turn(turn_id)
        if not _tp.current_turn_id():
            return                                   # off the record: no plan
        _tp.record(_tp.TurnPlan(
            plan_version=_tp.PLAN_VERSION, turn_id=str(turn_id), shape=shape,
            route_proposal=proposal, route_reason=reason, reflex=reflex,
            ts=time.time()))
    except Exception as e:
        _log.debug("reflex plan not recorded: %s", e)


# ---------------------------------------------------------------------------
#  SESSION CONTEXT
# ---------------------------------------------------------------------------

def _base_ctx(conversation_id: str, chosen_by: str, authenticated: bool) -> dict:
    ctx = {
        "authenticated": bool(authenticated),
        "provider": "",
        "model": "",
        # Read by the governance receipt's `chosen_by` (action_gate._chosen_by).
        "chosen_by": chosen_by,
        "turn_shape": None,
        "conversation_id": conversation_id or "",
    }
    try:
        from agent_friday.services import codebases as _cbs
        rec = _cbs.for_conversation(conversation_id)
        if rec:
            ctx["codebase"] = rec["id"]
            ctx["key_profile"] = rec.get("key_profile") or "mine"
    except Exception:
        pass
    return ctx


def _confirmation_key(session_id, conversation_id) -> str:
    return f"conversation:{conversation_id}" if conversation_id else session_id


def _pending(session_id, conversation_id) -> dict:
    from agent_friday.services import agent
    key = _confirmation_key(session_id, conversation_id)
    if not key:
        return {}
    with agent._PENDING_LOCK:
        return dict(agent._PENDING_CONFIRMATIONS.get(key) or {})


# ---------------------------------------------------------------------------
#  1. APPROVAL REPLIES
# ---------------------------------------------------------------------------

def approval_enabled(settings: Optional[dict]) -> bool:
    return (settings or {}).get(APPROVAL_SETTING) is not False


def classify_reply(message: str, session_id, conversation_id) -> str:
    """"approve" | "change" | "none" for a message answering a pending card.

    approve  a whole, unconditional yes, and a question is waiting for it
    change   opens with a yes but is not one ("yes, but use the other folder"):
             never an approval; the normal turn gets the owner's words
    none     nothing pending, or not a yes
    """
    from agent_friday.services import agent
    pending = _pending(session_id, conversation_id)
    if not any(e.get("awaiting_reply") for e in pending.values()):
        return "none"
    if agent._is_affirmative(message):
        return "approve"
    if agent._is_negative(message) or agent._is_ambiguous(message):
        return "none"
    if agent._AFFIRM_RE.match(message or ""):
        return "change"
    return "none"


def approval_turn(message: str, data: Optional[dict], *, session_id, conversation_id,
                  turn_id: Optional[str], settings: Optional[dict],
                  authenticated: bool = True,
                  local: Optional[bool] = None) -> Optional[dict]:
    """The session context that runs the stored action for a confident yes, else None.

    None means "take today's path": nothing pending, a conditional or ambiguous
    reply, an untrusted surface, or the setting is off. The caller replays the
    returned context with `agent.resume_confirmed_action`, the same call the
    normal path makes, so the outcome is identical.
    """
    try:
        if not approval_enabled(settings):
            return None
        kind = classify_reply(message, session_id, conversation_id)
        if kind == "none":
            return None
        if not trusted_surface(data, message, local=local):
            count("approval_untrusted_surface")
            return None
        if kind == "change":
            count("approval_change_request")
            _record_plan(turn_id, shape="approval_reply", proposal=None,
                         reason="rule: yes with a condition is a change request",
                         reflex={"by": "rule", "kind": "approval_reply",
                                 "outcome": "change_request"})
            return None
        from agent_friday.services import agent
        ctx = _base_ctx(conversation_id, "rule", authenticated)
        ctx = agent.prepare_confirmation_ctx(session_id, message, ctx)
        if not ctx.get("confirm_granted_fp"):
            return None
        count("approval_reply")
        _record_plan(turn_id, shape="approval_reply",
                     reason="rule: a whole yes to the question waiting",
                     reflex={"by": "rule", "kind": "approval_reply", "outcome": "approved"})
        return ctx
    except Exception as e:
        _log.warning("approval reflex failed, taking the normal path: %s", e)
        return None


# ---------------------------------------------------------------------------
#  2. "OPEN THE X"
# ---------------------------------------------------------------------------

def open_enabled(settings: Optional[dict]) -> bool:
    return (settings or {}).get(OPEN_SETTING) is True


_QUESTION_RE = re.compile(r'wait for their reply:\s*"(.*)"\s*$', re.S)


def _held_question(result: str) -> str:
    """The yes/no question inside a [CONFIRMATION REQUIRED] result, else ''."""
    m = _QUESTION_RE.search(result or "")
    return m.group(1).strip() if m else ""


def _first_line(text: str, n: int = 200) -> str:
    return ((text or "").strip().splitlines() or [""])[0][:n]


def open_turn(message: str, data: Optional[dict], *, session_id, conversation_id,
              turn_id: Optional[str], settings: Optional[dict],
              authenticated: bool = True, local: Optional[bool] = None,
              resolve=None) -> Optional[dict]:
    """Resolve and run one "open the X" request, or None to take the normal turn.

    Returns {"reply", "tool_trace", "actions", "outcome"} when the turn was
    answered here. The opt-in setting off means the resolver is never called.
    """
    try:
        if not open_enabled(settings):
            return None
        pointed = _point_act_turn(message, data, session_id=session_id, conversation_id=conversation_id,
                                  turn_id=turn_id, authenticated=authenticated, local=local)
        if pointed is not None:
            return pointed
        from agent_friday.services import laya_resolver
        # Only a request that opens with an opening verb is looked at; every
        # other turn is the ordinary turn, uncounted.
        if not laya_resolver._OPEN_VERB.search(message or ""):
            return None
        if not trusted_surface(data, message, local=local):
            return None
        if resolve is None:
            resolve = laya_resolver.resolve
        res = resolve(message)
        status = getattr(res, "status", "brain")
        reason = str(getattr(res, "reason", "") or "")
        if status == "ask":
            count("open_ask")
            _record_plan(turn_id, shape="reflex_open", reason="resolver: asked which one",
                         reflex={"by": "resolver", "kind": "open", "outcome": "ask"})
            ask = _laya_templates.rule_question(str(res.question or res.text), "resolver")
            return {"reply": ask.text, "tool_trace": [], "actions": [],
                    "outcome": "ask", "laya": ask.receipt()}
        if status != "command" or not getattr(res, "command", None):
            # brain / not_found: the normal turn. A resolver that did not
            # answer (Laya absent, slow, errored) is counted as degraded.
            degraded = status == "brain" and reason not in _BRAIN_REASONS_NORMAL
            count("open_degraded" if degraded else "open_to_model")
            return None
        return _run_command(res, message, session_id=session_id,
                            conversation_id=conversation_id, turn_id=turn_id,
                            authenticated=authenticated)
    except Exception as e:
        _log.warning("open reflex failed, taking the normal path: %s", e)
        count("open_error")
        return None


def _run_command(res, message, *, session_id, conversation_id, turn_id, authenticated):
    from agent_friday.services import agent
    cmd = res.command
    tool, tool_input = cmd["tool"], dict(cmd.get("input") or {})
    ctx = _base_ctx(conversation_id, "resolver", authenticated)
    ctx = agent.prepare_confirmation_ctx(session_id, message, ctx)
    # The same call a model's tool use makes: confirmation, governance, vault,
    # sandbox, receipts. This module has no other way to run a command.
    result = agent._execute_tool(tool, tool_input, session_ctx=ctx)
    result = str(result if result is not None else "")
    status = agent._tool_call_status(result)
    entry = {"name": tool, "input": tool_input, "result": result}
    title = ""
    try:
        title = str(res.chosen.title)
    except Exception:
        pass
    shown = title or "that"
    landed = status == "ok" and (tool != "navigate_to" or result.startswith("NAV_OK"))
    if landed:
        outcome = "done"
        reply = _lt.line("open_done", shown=shown)
    elif status == "deny" and result.startswith("[CONFIRMATION REQUIRED]"):
        outcome = "asked"
        reply = (_laya_templates.rule_question(_held_question(result), "action_gate")
                 if _held_question(result) else _lt.line("open_asked", shown=shown))
    elif status == "pending":
        outcome = "card"
        reply = _lt.line("open_card", shown=shown)
    elif status in ("deny", "error"):
        outcome = "refused"
        reply = _lt.line("open_refused", shown=shown, detail=_first_line(result))
    else:
        outcome = "not_confirmed"
        reply = _lt.line("open_unconfirmed", shown=shown, detail=_first_line(result))
    count("open_" + outcome)
    _record_plan(turn_id, shape="reflex_open", reason="resolver: one sealed command",
                 reflex={"by": "resolver", "kind": "open", "tool": tool, "outcome": outcome})
    return {"reply": reply.text, "tool_trace": [entry], "actions": [], "outcome": outcome,
            "laya": reply.receipt()}


# ---------------------------------------------------------------------------
#  2b. POINT-AND-ACT: the rows the owner pointed at
# ---------------------------------------------------------------------------

_POINT_OK = ("NAV_OK", "SELECT_OK", "SELECT_PARTIAL")


def _point_act_turn(message, data, *, session_id, conversation_id, turn_id, authenticated, local):
    """Open or tick the on-screen rows a short typed line names, or ask which; None
    for everything else (a change to those rows is the normal turn). Trusted
    surfaces only, and only when a page reports a fresh list."""
    from agent_friday.services import point_act
    if not point_act.looks_like(message):
        return None
    if not trusted_surface(data, message, local=local):
        return None
    from agent_friday.services import laya
    # Through the contract: one deadline, a receipt, and a slow reading model is "no rows named" (the
    # ordinary turn), never a wait.
    r = laya.point_act_turn(message, quiet=False, caller="chat.point")
    if r is None:
        return None
    if r.decision == "ask":
        count("point_ask")
        _record_plan(turn_id, shape="reflex_point", reason="point-and-act: asked which rows",
                     reflex={"by": "rule", "kind": "point", "outcome": "ask"})
        ask = _laya_templates.rule_question(r.question, "point_act")
        return {"reply": ask.text, "tool_trace": [], "actions": [], "outcome": "ask",
                "laya": ask.receipt()}
    if r.decision != "tool" or not r.tool:
        count("point_to_model")
        return None
    from agent_friday.services import agent
    ctx = _base_ctx(conversation_id, "rule", authenticated)
    ctx = agent.prepare_confirmation_ctx(session_id, message, ctx)
    result = str(agent._execute_tool(r.tool, dict(r.args), session_ctx=ctx) or "")
    status = agent._tool_call_status(result)
    entry = {"name": r.tool, "input": dict(r.args), "result": result}
    if result.startswith(_POINT_OK):
        outcome = "done"
        # What the page confirmed, in the tool's own words.
        said = result.split(" ", 1)[1].strip() if " " in result else "done"
        reply = _lt.line("point_done", said=(said if r.tool == "screen_select" else "Done: opened it"))
    elif status == "deny" and result.startswith("[CONFIRMATION REQUIRED]"):
        outcome = "asked"
        reply = (_laya_templates.rule_question(_held_question(result), "action_gate")
                 if _held_question(result) else _lt.line("point_asked"))
    elif status == "pending":
        outcome = "card"
        reply = _lt.line("point_card")
    elif status in ("deny", "error"):
        outcome = "refused"
        reply = _lt.line("point_refused", detail=_first_line(result))
    else:
        outcome = "not_confirmed"
        reply = _lt.line("point_unconfirmed", detail=_first_line(result))
    count("point_" + outcome)
    _record_plan(turn_id, shape="reflex_point", reason="point-and-act: rows named on screen",
                 reflex={"by": "rule", "kind": "point", "tool": r.tool, "outcome": outcome})
    return {"reply": reply.text, "tool_trace": [entry], "actions": [], "outcome": outcome,
            "laya": reply.receipt()}

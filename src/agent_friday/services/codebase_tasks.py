"""One card per task for commands and the agent in a codebase (chat-hub.md M3b;
salon spec §4.5 "Trust this codebase").

Running a command in a codebase, or handing it to Claude's agent, reaches past
the browser frame: a process on this PC. It is outward, and it goes through
the existing gate, batched per task rather than carded per command:

- the first `codebase_run` or `codebase_agent` of a task raises ONE card,
  "Run commands in <codebase> for this task", and nothing runs;
- approving it mints a grant scoped to that codebase (`codebase:<id>`) for a
  bounded time and number of uses, runs the first action, and posts the result
  into the chat; every later action of the task consumes one use and runs
  without a card;
- denying it runs nothing and mints nothing; a later ask raises a new card.

The grant is the owner's decision, minted here in the card's decision hook,
never by a tool. `action_gate.consume_grant` is how the two tools spend it.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

_log = logging.getLogger(__name__)

KIND = "codebase_task"
GRANT_TOOLS = ("codebase_run", "codebase_agent")
GRANT_SECONDS = 30 * 60
GRANT_USES = 40

_registered = False


def scope(cid: str) -> str:
    return "codebase:%s" % cid


def register() -> None:
    """Attach the decision hook. Idempotent."""
    global _registered
    if _registered:
        return
    from agent_friday.services import approvals as _ap
    _ap.register_decision_hook(KIND, _on_decision)
    _registered = True


def _describe(tool: str, args: dict) -> str:
    if tool == "codebase_agent":
        return "Claude's agent: %s" % (str(args.get("task") or "").strip()[:160] or "the task")
    return "`%s`" % str(args.get("command") or "").strip()[:200]


def pending_card(cid: str, conversation_id: str) -> Optional[dict]:
    """The task's card while it is still waiting, so a second ask in the same
    task returns the same card rather than raising another."""
    from agent_friday.services import approvals as _ap
    for a in _ap.list_approvals(status="pending"):
        p = a.get("payload") or {}
        if a.get("kind") == KIND and p.get("codebase_id") == cid and (p.get("conversation_id") or "") == (conversation_id or ""):
            return a
    return None


def request(rec: dict, tool: str, args: dict, *, conversation_id: str = "", requested_by: str = "friday") -> dict:
    """Raise the task's one card (or return the one already waiting)."""
    register()
    from agent_friday.services import approvals as _ap
    existing = pending_card(rec["id"], conversation_id)
    if existing is not None:
        return existing
    title = 'Run commands in "%s" for this task' % (rec.get("title") or rec["id"])
    first = _describe(tool, args)
    payload = {"codebase_id": rec["id"], "conversation_id": conversation_id or "", "tool": tool,
               "args": dict(args or {}), "title": rec.get("title") or rec["id"], "first": first}
    return _ap.create_approval(
        kind=KIND, subject_type="codebase", subject_id="%s@%s@%s" % (rec["id"], conversation_id or "-", uuid.uuid4().hex[:8]),
        title=title,
        description=("One approval covers this task: commands and Claude's agent in this codebase's own folder, "
                     "for %d minutes or %d runs, whichever comes first. Nothing runs until you approve; first up: %s"
                     % (GRANT_SECONDS // 60, GRANT_USES, first)),
        action_description="run commands in the codebase for this task", payload=payload,
        requested_by=requested_by, force_gate=True)


def _perform(payload: dict) -> str:
    """The task's first action, on the fresh grant. Returns what to tell the chat."""
    from agent_friday.governance import action_gate as _gate
    from agent_friday.services import codebases as _cb
    cid = payload.get("codebase_id") or ""
    tool = payload.get("tool") or "codebase_run"
    args = payload.get("args") or {}
    if _gate.consume_grant(tool, scope(cid)) is None:
        return "The approval was recorded, but no grant could be used; nothing ran."
    if tool == "codebase_agent":
        from agent_friday.services import claude_engine as _ce
        rec = _cb.load(cid) or {}
        out = _ce.run_task(cid, str(args.get("task") or ""), key_profile=rec.get("key_profile") or "mine")
        return str(out.get("say") or out.get("status") or "done")
    out = _cb.run(cid, str(args.get("command") or ""))
    if out.get("status") != "ok":
        return "Not run: %s" % (out.get("say") or out.get("error") or out.get("status"))
    tail = (out.get("output") or "").strip()
    tail = tail[-1200:] if len(tail) > 1200 else tail
    return "Ran `%s` in %s: exit %s.%s" % (str(args.get("command") or "")[:120], payload.get("title") or cid, out.get("exit"),
                                           ("\n```\n%s\n```" % tail) if tail else "")


def _post_back(conversation_id: str, text: str, approval_id: str = "") -> None:
    if not conversation_id:
        return
    try:
        from agent_friday.services import conversations as _convs
        _convs.append(conversation_id, {"role": "friday", "text": text, "ts": time.time(),
                                        "meta": {"kind": "approval_result", "approval_id": approval_id}})
        from agent_friday.services import desktop_bus as _bus
        _bus.broadcast({"type": "approval_result", "conversation_id": conversation_id,
                        "approval_id": approval_id}, kind="chat")
    except Exception as e:
        _log.warning("could not post the task result into %s: %s", conversation_id, e)


def _on_decision(record: dict) -> None:
    if not isinstance(record, dict) or record.get("kind") != KIND:
        return
    payload = record.get("payload") or {}
    conv = payload.get("conversation_id") or ""
    title = payload.get("title") or payload.get("codebase_id") or "the codebase"
    if record.get("status") != "approved":
        if record.get("status") in ("denied", "expired"):
            _post_back(conv, "Not run: the card to run commands in %s was %s. Nothing ran and nothing was granted."
                       % (title, record.get("status")), record.get("approval_id") or "")
        return
    from agent_friday.governance import action_gate as _gate
    try:
        _gate.create_grant(tools=list(GRANT_TOOLS), scope=scope(payload.get("codebase_id") or ""),
                           expires_in_seconds=GRANT_SECONDS, max_uses=GRANT_USES, created_by="owner",
                           note=record.get("title") or "")
    except Exception as e:
        _post_back(conv, "The approval was recorded, but the grant could not be written (%s); nothing ran." % e,
                   record.get("approval_id") or "")
        return
    try:
        text = _perform(payload)
    except Exception as e:
        text = "The task's first action failed: %s" % e
    _post_back(conv, text, record.get("approval_id") or "")

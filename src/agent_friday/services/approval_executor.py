# -*- coding: utf-8 -*-
"""Run the action an approved card describes, exactly once.

WHY THIS EXISTS
---------------
`governance/action_gate.authorize` has two gated verdicts, and until 2026-09-25
only one of them ever executed:

  * ``confirm`` is BLOCKING. Outward, interactive, nothing from outside content:
    the chat gate asks yes/no inside the turn, the user answers, the same call
    proceeds. Nothing is deferred, so nothing is left to run later.
  * ``card`` is DEFERRED. The tool call is refused with
    "[APPROVAL CARD RAISED]", the card is stored, and the design assumed the
    model would make the identical call again so `_taint_card` could find the
    approved card and let it through.

Nothing ever made that second call. The refusal text tells the model in as many
words "Do NOT call it again this turn", and once the turn ends there is no loop
left to re-issue anything. So an owner could approve a card and watch nothing
happen: five `create_calendar_event` cards and two `write_file` cards, all
``status: approved, consumed: false``, an itinerary that never reached a
calendar, and two bug reports that were never written.

This module is the missing half. It makes the second call itself, on approval,
from whatever surface approved it and however long after the turn has ended.

EXACTLY ONCE
------------
Three independent reasons a double-run cannot happen, because this runs
irreversible things:

  1. `approvals.decide` only acts on a card whose status is ``pending``, under
     the store lock, so concurrent approvals fire the hook once.
  2. `approvals.claim_for_execution` is a compare-and-set under that same lock.
     Whatever else calls the hook -- a second registration, a replayed decision,
     a cross-tab popup -- only one caller is told to proceed.
  3. The governance checkpoint marks the card ``consumed`` when it lets the call
     through, and refuses a consumed card.

WHAT THIS IS NOT
----------------
It is not a second decision-maker. It runs only what the owner already approved,
it verifies the tool and arguments against the card's own payload before running
anything, and it writes the cLaws-checked signed receipt that every outward
action gets (`action_gate.record_external`). A card it cannot match, or cannot
write a receipt for, is not run.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

#: The card kind the taint gate raises for a flagged tool call.
KIND = "tainted_action"

_registered = False


def register() -> None:
    """Attach the executor to approval decisions. Idempotent."""
    global _registered
    if _registered:
        return
    from agent_friday.services import approvals as _ap
    _ap.register_decision_hook(KIND, _on_decision)
    _ap.register_decision_hook("tool_confirm", _on_decision)
    _registered = True
    _log.info("approval executor registered for %r cards", KIND)


def _run_tool(name: str, args: Dict[str, Any], *,
              session_ctx: Optional[dict] = None) -> Any:
    """Dispatch through the one choke point every tool call goes through.

    `services.agent._execute_tool` is the only way to invoke a handler, and that
    is deliberate -- the audit log, the PII scrub and the cost attribution are
    hooks on it. The executor does not get a side door; it gets a session_ctx
    carrying the approved card's id, which the governance hook checks against the
    card's own payload.
    """
    from agent_friday.services.agent import _execute_tool
    return _execute_tool(name, args, session_ctx=session_ctx)


def _post_back(record: dict, text: str) -> None:
    """Put the outcome in the conversation that asked for it.

    An approval decided in the System workspace has no turn to return into, so
    without this the owner approves a card and the chat that raised it never
    mentions it again -- which is how the reported failure stayed invisible for
    four turns.
    """
    cid = ((record.get("payload") or {}).get("conversation_id") or "").strip()
    if not cid:
        return
    try:
        from agent_friday.services import notice_registry as _nr
        _nr.transcript_add("E75", cid, {"role": "friday", "text": text,
                                        "ts": time.time(),
                                        "meta": {"kind": "approval_result",
                                                 "approval_id": record.get("approval_id")}})
    except Exception as e:                      # never let reporting break the run
        _log.warning("could not post the approval result into %s: %s", cid, e)
        return
    # The store is the record; this is what makes it VISIBLE. The chat fetches a
    # conversation when it opens or switches and does not poll, so without a push
    # the owner would have to reload to learn that the thing he approved had
    # happened -- and not knowing is the whole complaint.
    try:
        from agent_friday.services import notice_registry as _nr
        _nr.bus_broadcast("E74", {"type": "approval_result", "conversation_id": cid,
                                  "approval_id": record.get("approval_id")}, kind="chat")
    except Exception as e:
        _log.info("no chat page to tell about %s: %s", cid, e)


def _describe(tool: str, args: Dict[str, Any]) -> str:
    try:
        return "%s %s" % (tool, json.dumps(args, default=str)[:300])
    except Exception:
        return tool


def _on_decision(record: dict) -> None:
    """Run an approved card's action once, and report what happened."""
    if not isinstance(record, dict):
        return
    if record.get("kind") == "tool_confirm":
        from agent_friday.services.agent import clear_confirmation_card
        clear_confirmation_card(record.get("approval_id"))
    payload = record.get("payload") or {}
    if record.get("status") == "denied" and isinstance(payload.get("public_query"), dict):
        # Don't send on a search card (privacy/public_query): nothing runs, and
        # the conversation that asked is told so in plain words. No query text.
        _post_back(record, "You chose Don't send on the search card. Nothing "
                           "was sent to %s."
                   % (payload["public_query"].get("engine") or "the search engine"))
    if record.get("status") != "approved":
        return                                  # denied, expired, blocked: nothing runs

    tool = payload.get("tool")
    args = payload.get("input")
    if not tool or not isinstance(args, dict):
        # Another kind of card shares this hook's kind, or an older card has no
        # payload to act on. Saying so is better than silently doing nothing,
        # because "nothing happened" is the bug this module exists to fix.
        _log.info("approval %s has no tool payload; nothing to execute",
                  record.get("approval_id"))
        return

    aid = record.get("approval_id")
    from agent_friday.services import approvals as _ap
    from agent_friday.privacy import public_query as _pq
    search_card = _pq.is_public_query_card(record)
    if search_card and not _pq.approved_texts(record):
        # A search card whose input is not exactly what it showed: not run.
        if _ap.claim_for_execution(aid):
            _ap.mark_used(aid, "approval_executor",
                          detail={"ok": False, "error": "card text does not match its input"})
            _post_back(record, "The search card you approved did not match what it "
                               "would send, so nothing was sent.")
        return
    if not _ap.claim_for_execution(aid):
        return                                  # someone else already has it

    # The two steps every outward action gets, for an executor acting outside a
    # tool call: the cLaws are intact, and a signed receipt exists. If either
    # fails the action does NOT run.
    from agent_friday.governance import action_gate as _gate
    try:
        _gate.record_external(tool, surface="approval_card", approval_id=aid,
                              target=str(args.get("path") or args.get("title") or ""))
    except Exception as e:
        _ap.mark_used(aid, "approval_executor",
                      detail={"ok": False, "error": "held: %s" % e})
        _post_back(record, "I could not run the approved action: %s. Nothing "
                           "was done." % e)
        _log.error("approval %s held at the checkpoint: %s", aid, e)
        return

    ctx = {"authenticated": True, "approved_card": aid,
           "surface": "approval_card", "confirm_bypass": True,
           "conversation_id": payload.get("conversation_id") or ""}
    started = time.time()
    try:
        # An approved search card (privacy/public_query): exactly the strings
        # the card showed are floor-only at the search gates while it runs.
        # Any other card: a no-op.
        with _pq.approved_scope(record):
            result = _run_tool(tool, args, session_ctx=ctx)
        from agent_friday.services.completion_receipts import receipt_ok
        ok = receipt_ok({"name": tool, "result": result})
        if tool == "navigate" and str(result).startswith("NAV_OK:"):
            # There is no chat HTTP response to carry the UI action from a
            # card decision. Use the desktop's acknowledged command channel.
            from agent_friday.services.desktop_targets import open_on_desktop
            workspace = str(result).split(":", 1)[1].split()[0]
            shown = open_on_desktop("workspace", workspace=workspace)
            result, ok = shown["text"], shown.get("status") == "opened"
    except Exception as e:
        result, ok = "%s: %s" % (type(e).__name__, e), False

    took = int((time.time() - started) * 1000)
    _ap.mark_used(aid, "approval_executor",
                  detail={"ok": ok, "took_ms": took,
                          "result": str(result)[:800]})

    if search_card:
        # A search card's report names the card, not the query, and its result
        # is PII-scrubbed: the transcript replays to cloud seats.
        _post_back(record, _scrubbed(
            ("Approved search (card %s) result:\n\n%s" if ok else
             "You approved search card %s, but it did not go through: %s")
            % (aid, str(result)[:600 if ok else 400])))
        if not ok:
            _log.warning("approved search card %s failed", aid)
    elif ok:
        _post_back(record, "Approved action result: %s\n\n%s"
                   % (_describe(tool, args), str(result)[:600]))
    else:
        _post_back(record, "You approved %s, but it did not go through: %s"
                   % (_describe(tool, args), str(result)[:400]))
        _log.warning("approved action %s failed: %s", tool, result)


def _scrubbed(text: str) -> str:
    """`text` with PII replaced by tags (core._scrub_pii); when the scrub
    cannot run, a fixed line is posted instead of the text."""
    try:
        from agent_friday.core import _scrub_pii
        return _scrub_pii(text)[0]
    except Exception:
        return ("An approved search ran; its result could not be checked for "
                "private details, so it is not shown here.")

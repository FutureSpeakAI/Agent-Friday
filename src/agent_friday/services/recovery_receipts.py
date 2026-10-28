"""services/recovery_receipts.py -- one signed recovery receipt per interrupted thing (BD-16).

North Star 32.11 (crash recovery): on restart Friday identifies interrupted work, rebuilds the
last durable checkpoint, checks whether an action may already have happened, never replays a
non-idempotent one, "creates or backfills a recovery receipt", resumes only when policy
permits, and reports. NS-32.10-1: a partial multi-step action records its last verified
boundary and pauses and explains. BD-16's acceptance: "each chaos kill leaves zero duplicate
effects and one recovery receipt".

The boot paths already find the interrupted work and refuse to repeat it (task_journal
.reconcile_on_boot, task_resume, card_recovery; R1 #303 proves the refusals). What was missing
is the record: each interrupted task and each interrupted approved action now gets exactly one
signed receipt in the action ledger, written where it is found, saying how Friday stopped,
the last verified boundary, what was in flight, whether it may already have happened, and
what Friday will do next. Those finders report each interruption once, so one receipt each.
A receipt that cannot be written is logged; it never stops Friday from starting.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.recovery_receipts")

#: What Friday does next with an interrupted thing (the receipt's ``next``).
RESUMES = "resumes_from_its_checkpoint"
ASKS = "asks_the_owner_before_resuming"
NOT_REPEATED = "not_repeated_owner_told"
STAYS_STOPPED = "stays_stopped_owner_told"


def _write(entry: Dict[str, Any]) -> bool:
    try:
        from agent_friday.governance import action_gate
        base = {"tool": "recovery", "class": action_gate.INTERNAL, "decision": "recovered",
                "surface": "boot", "chosen_by": "friday:recovery"}
        base.update({k: v for k, v in entry.items() if v is not None})
        action_gate._receipt(base)
        return True
    except Exception as e:  # noqa: BLE001 - a missing receipt never stops a start
        _log.error("recovery receipt not written for %s: %s", entry.get("subject"), type(e).__name__)
        return False


def task_interrupted(task_id: str, state: Dict[str, Any], previous_exit: Optional[Dict[str, Any]],
                     last_boundary: Optional[str]) -> bool:
    """The receipt for one interrupted task, written when the journal marks it interrupted."""
    verdict: Dict[str, Any] = {}
    try:
        from agent_friday.services import task_resume
        verdict = task_resume.resumability(task_id) or {}
    except Exception:
        verdict = {}
    if verdict.get("may_have_acted"):
        nxt = NOT_REPEATED
    elif verdict.get("needs_confirmation"):
        nxt = ASKS
    elif verdict.get("resumable"):
        nxt = RESUMES
    else:
        nxt = STAYS_STOPPED
    pending = verdict.get("pending_tool") or {}
    if not last_boundary:
        # The last verified boundary: the resume checkpoint, when the journal names none.
        step = int(verdict.get("iteration") or 0)
        last_boundary = ("checkpoint after step %d" % step) if step else "started; no checkpoint yet"
    return _write({"subject": "task:%s" % task_id, "what": str(state.get("name") or "")[:120],
                   "exit": (previous_exit or {}).get("kind"),
                   "last_boundary": str(last_boundary or "")[:120] or None,
                   "in_flight": (pending.get("name") if isinstance(pending, dict) else None),
                   "may_have_acted": bool(verdict.get("may_have_acted")),
                   "repeated": False, "next": nxt})


def card_interrupted(card: Dict[str, Any]) -> bool:
    """The receipt for one approved action stopped mid-run (its outcome is unknown, and the
    card can never authorise it again)."""
    payload = card.get("payload") or {}
    return _write({"subject": "card:%s" % card.get("approval_id"),
                   "what": str(card.get("title") or card.get("kind") or "")[:120],
                   "in_flight": str(payload.get("tool") or card.get("kind") or "")[:60] or None,
                   "last_boundary": "approved and started" if card.get("executing_at") else "approved",
                   "may_have_acted": True, "repeated": False, "outcome": "unknown",
                   "next": NOT_REPEATED})

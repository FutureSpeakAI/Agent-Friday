"""The task states the North Star names (NS-17.7-1), read from what Friday records.

Friday's task records keep their `status` strings as they were (the journal, the seat queue,
the tray and many tests read them). This module is the one place that says which of the
North Star's states a task is in, from its status and the facts recorded beside it:

    queued                      created, not started
    planning                    started, before its first reasoning step
    running                     working
    waiting_for_approval        an approval card it raised is open (A1: a task never counts as
                                done while one is)
    waiting_for_input           it asked the owner something and waits for the answer
    waiting_for_external_event  it waits on something outside it: the local model seat, an idle PC
    paused                      the owner paused it
    cancel_requested            the owner asked it to stop; it stops at its next step
    cancelled                   stopped (by the owner, or replaced by a new run of itself)
    failed                      ended with an error or a timeout
    completed_unverified        finished, but without evidence the work was done
    completed_verified          finished with evidence
    outcome_unknown             an action it waited on was approved and began, and Friday stopped
                                before it said how it ended: it may or may not have happened.
                                Never done, never failed; check before retrying
                                (agent.settle_waiting_task)
    recovered_after_crash       Friday stopped while it was running (closed or crashed: the
                                record's `exit` says which); its record was recovered at the next
                                start (task_journal.reconcile_on_boot)

`derive` never raises and never guesses a finished state for unfinished work.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

STATES = ("queued", "planning", "running", "waiting_for_approval", "waiting_for_input",
          "waiting_for_external_event", "paused", "cancel_requested", "cancelled", "failed",
          "completed_unverified", "completed_verified", "recovered_after_crash", "outcome_unknown")
TERMINAL_STATES = frozenset({"cancelled", "failed", "completed_unverified", "completed_verified",
                             "recovered_after_crash", "outcome_unknown"})

_WAITING = {"approval": "waiting_for_approval", "input": "waiting_for_input",
            "external": "waiting_for_external_event"}


def derive(rec: Optional[Dict[str, Any]], *, stop_requested: bool = False) -> str:
    """The North Star state of one task record (a TASKS entry or its state.json)."""
    rec = rec or {}
    status = str(rec.get("status") or "").strip().lower()
    if status in ("complete", "completed", "done"):
        # Verified only with evidence on the record: some paths write "complete" without a check.
        verified = rec.get("verified") is True or bool((rec.get("verification") or {}).get("verified"))
        return "completed_verified" if verified else "completed_unverified"
    if status == "completed_unverified":
        return "completed_unverified"
    if status == "outcome_unknown":
        return "outcome_unknown"        # before the journal's terminal fallback: never a finish
    if status.startswith("failed") or status in ("timeout", "error", "killed"):
        return "failed"
    if status in ("cancelled", "canceled", "superseded", "stopped", "skipped", "deleted",
                  "declined"):
        return "cancelled"              # declined: the owner said no on the task's card
    if status == "interrupted":
        return "recovered_after_crash"
    if status == "needs_reconnect":
        # Terminal for the journal; the task waits on the owner to reconnect (NS-32.7-1).
        return "waiting_for_input"
    if status in ("waiting_approval", "waiting_for_approval"):
        # Terminal for the journal (the worker is done; a restart must not run it again), but the
        # task is not finished: it waits for the owner's card (agent.settle_waiting_task).
        return "waiting_for_approval"
    try:
        from agent_friday.services import task_journal as _tj
        if _tj.is_terminal(status):
            return "completed_unverified"      # finished, in a way this list has no word for
    except Exception:
        pass
    # Not finished.
    if stop_requested or rec.get("stop_requested"):
        return "cancel_requested"
    if rec.get("user_paused") or rec.get("paused_by_you"):
        return "paused"
    if status in ("waiting_approval", "waiting_for_approval", "waiting"):
        return "waiting_for_approval"
    waiting = rec.get("waiting_on")
    if isinstance(waiting, dict) and waiting.get("kind") in _WAITING:
        return _WAITING[waiting["kind"]]
    if status == "queued-for-seat" or rec.get("paused_reason"):
        return "waiting_for_external_event"
    if status in ("queued", "pending", ""):
        return "queued"
    if rec.get("phase") == "planning":
        return "planning"
    return "running"

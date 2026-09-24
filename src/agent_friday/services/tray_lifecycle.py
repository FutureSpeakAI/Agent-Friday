"""What stays in the tasks tray, and for how long.

THE REQUIREMENT. Cards for interrupted and completed work must not pile up in
the tray forever: the user can remove them by hand, and they age out after a
set time, so the panel is not overwhelmed.

WHAT THE PILE ACTUALLY IS. The notification QUEUE stays bounded (a cap, most
entries already dismissed). The tray's TASKS section is where rows accumulate:
task records, every one of them terminal, none of them ever leaving. A card
that shows a ✕ only while a task is running (as a cancel button) leaves a
finished card with no control at all; "Delete record" lives only inside the
drawer, and it deletes the journal rather than tidying the view. Two
different things wearing one name.

TWO CONTROLS, TWO MEANINGS, KEPT APART:

  * **dismiss** hides the card. The record, the journal and the result all
    stay exactly where they are and remain reachable from the records view.
    This is the one the ✕ should have been doing all along.
  * **delete** destroys the record. Already exists, already lives in the
    drawer behind a deliberate click, and is not what a tidy-up gesture
    should ever mean.

LIFETIMES BY WHAT THE ROW IS FOR, not by one clock:

  * A **finished** task is a receipt. Once it has been seen there is nothing
    to act on, so it ages out of the tray quickly — the result stays
    reachable, only the card goes.
  * A **failed / timed-out** task is a thing someone may still want to look
    at, so it gets longer.
  * An **interrupted** task that still holds a resume checkpoint is a HANDLE
    ON UNFINISHED WORK, and expiring it would quietly throw away the very
    thing the resume path exists to offer. It never expires on a clock. It
    leaves the tray when it is resumed, or when the user dismisses it.

That last rule is the reason this module exists rather than a single
`max_age_hours` setting. A uniform sweep would delete the user's resume
handles, which is the failure mode the whole checkpoint effort is trying to
end.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

#: Terminal statuses, by how long their card is worth keeping in the tray.
#: Hours. ``None`` means "never expires on a clock".
DEFAULT_TRAY_HOURS = {
    "complete": 6,
    "completed_unverified": 12,
    "superseded": 1,
    "cancelled": 6,
    "failed": 48,
    "timeout": 48,
    "killed": 48,
    "failed:watchdog-kill": 48,
    # Set below, per row, by whether a checkpoint actually exists.
    "interrupted": 48,
}

#: Statuses that are still live. A running task is never swept, whatever its
#: age — that was the fifteen-minute chat release's mistake in another costume.
LIVE = frozenset({"running", "queued", "queued-for-seat"})


def _settings() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def tray_hours(status: str) -> Optional[float]:
    """How long a card of this status stays, in hours. None = indefinitely."""
    cfg = (_settings().get("task_tray") or {}).get("hours") or {}
    if status in cfg:
        v = cfg[status]
        if v in (None, "", 0):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return DEFAULT_TRAY_HOURS.get(status)


def _has_checkpoint(task_id: str) -> bool:
    try:
        from agent_friday.services import task_resume as _tr
        return bool(_tr.resumability(task_id).get("resumable"))
    except Exception:
        return False


def visible(row: Dict[str, Any], now: Optional[float] = None) -> bool:
    """Should this row appear in the tray?

    Never raises and defaults to SHOWING the row: a bug in this function must
    not be able to hide work from the user.
    """
    try:
        now = now or time.time()
        if row.get("tray_dismissed"):
            return False
        status = row.get("status") or "running"
        if status in LIVE:
            return True
        # An interrupted task holding a live checkpoint is a resume handle,
        # not a receipt. It stays until it is acted on.
        if status == "interrupted" and _has_checkpoint(row.get("task_id") or ""):
            return True
        hours = tray_hours(status)
        if hours is None:
            return True
        ended = row.get("ended") or row.get("state_written") or row.get("created")
        if not ended:
            return True
        return (now - float(ended)) < hours * 3600.0
    except Exception as e:
        _log.debug("tray visibility check failed, showing the row (%s)", e)
        return True


def annotate(row: Dict[str, Any]) -> Dict[str, Any]:
    """Add what the card needs to render its controls honestly.

    ``resumable`` is what turns the ✕-and-nothing-else card into one with a
    Resume button, so it is computed here rather than guessed at in the UI.
    """
    try:
        if (row.get("status") or "") == "interrupted":
            from agent_friday.services import task_resume as _tr
            v = _tr.resumability(row.get("task_id") or "")
            row["resumable"] = bool(v.get("resumable"))
            row["resume_reason"] = v.get("reason")
            row["resume_needs_confirmation"] = bool(v.get("needs_confirmation"))
            row["resume_from_step"] = v.get("iteration")
    except Exception:
        pass
    return row


def dismiss(task_id: str) -> bool:
    """Hide a card. Does NOT touch the record, the journal or the result."""
    from agent_friday.services.agent import TASKS, TASKS_LOCK, _journal_state
    with TASKS_LOCK:
        rec = TASKS.get(task_id)
        if rec is None:
            return False
        if rec.get("status") in LIVE:
            # Dismissing live work would hide something still happening. The
            # card for a running task has a Cancel control; that is the one
            # that means "stop", and it is not this.
            return False
        rec["tray_dismissed"] = True
        rec["tray_dismissed_at"] = time.time()
    try:
        _journal_state(task_id)
    except Exception:
        pass
    return True


def dismiss_all(now: Optional[float] = None) -> int:
    """Dismiss every card that is currently dismissible. Returns the count.

    Live work is skipped, and so is anything holding a resume checkpoint: a
    "clear all" that silently discarded the user's unfinished work would be the
    same loss this codebase keeps removing, dressed as tidiness.
    """
    from agent_friday.services.agent import TASKS, TASKS_LOCK
    with TASKS_LOCK:
        ids = [tid for tid, r in TASKS.items()
               if (r or {}).get("status") not in LIVE
               and not (r or {}).get("tray_dismissed")]
    kept = 0
    for tid in ids:
        with TASKS_LOCK:
            rec = TASKS.get(tid) or {}
            status = rec.get("status")
        if status == "interrupted" and _has_checkpoint(tid):
            continue          # a resume handle is not clutter
        if dismiss(tid):
            kept += 1
    return kept

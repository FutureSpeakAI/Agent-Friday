"""Is a scheduled job keeping up? Two flags the System workspace shows beside each job.

* ``overdue``: the job has not succeeded within twice its expected interval (an hourly job silent for two hours, a daily
  job for two days). A job that never succeeded counts from the day it was made. The cause is not guessed: a job that
  keeps waiting for the local model, one whose runs fail, one that never starts and one that was switched off by a bug
  all show the same flag, and the job's own status says why.
* ``stalled``: a run that has been in flight past ``STALL_TIMEOUT_S`` with no progress since it started (or since its
  last ``progress_at`` mark). A run is hung, not slow, when nothing it does is recorded.

A pure module: it reads the record and the run history it is given. Each rule has its test:
tests/unit/test_schedule_health.py.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

#: A job is overdue when its last success is older than this many of its expected intervals.
OVERDUE_FACTOR = 2.0
#: A run in flight longer than this with no progress mark is stalled (seconds).
STALL_TIMEOUT_S = 600.0

OK, OVERDUE, STALLED = "ok", "overdue", "stalled"

_DAY, _WEEK = 86400.0, 7 * 86400.0


def expected_interval_s(rec: Dict[str, Any]) -> Optional[float]:
    """Seconds between runs the job is expected to make, or None when it has no regular cadence (a one-time job, an
    event trigger, a shape this module does not know)."""
    trigger = str((rec or {}).get("trigger") or "")
    spec = (rec or {}).get("spec") or {}
    if trigger == "interval":
        try:
            minutes = float(spec.get("every_minutes"))
        except (TypeError, ValueError):
            return None
        return minutes * 60.0 if minutes > 0 else None
    if trigger in ("daily", "idle_daily"):
        return _DAY
    if trigger == "weekly":
        return _WEEK
    return None


def last_success(runs: Iterable[Dict[str, Any]], schedule_id: str) -> Optional[float]:
    """The end time of the newest run of this schedule that completed, from the run history rows."""
    best = None
    for row in runs:
        if row.get("id") != schedule_id or row.get("status") != "complete":
            continue
        stamp = row.get("ended") or row.get("started")
        if isinstance(stamp, (int, float)) and (best is None or stamp > best):
            best = float(stamp)
    return best


def evaluate(rec: Dict[str, Any], now: float, last_ok: Optional[float], *,
             stall_s: float = STALL_TIMEOUT_S) -> Dict[str, Any]:
    """``{"state": ok|overdue|stalled, "reason": words for the owner}`` for one schedule record. ``last_ok`` is the
    time of its last success (``last_success``). A job that is switched off is ok: nothing is expected of it."""
    rec = rec or {}
    if not rec.get("enabled", True):
        return {"state": OK, "reason": ""}
    started = rec.get("started_at")
    if rec.get("in_flight") and isinstance(started, (int, float)):
        since = max(float(started), float(rec.get("progress_at") or 0.0))
        quiet = now - since
        if quiet > stall_s:
            return {"state": STALLED,
                    "reason": "Running for %s with no progress; it may be stuck." % _span(quiet)}
    interval = expected_interval_s(rec)
    if interval is not None:
        baseline = last_ok if last_ok is not None else rec.get("created")
        if isinstance(baseline, (int, float)) and now - float(baseline) > OVERDUE_FACTOR * interval:
            silent = _span(now - float(baseline))
            if last_ok is not None:
                reason = "Has not succeeded for %s (expected every %s)." % (silent, _span(interval))
            else:
                reason = "Has never succeeded in %s (expected every %s)." % (silent, _span(interval))
            return {"state": OVERDUE, "reason": reason}
    return {"state": OK, "reason": ""}


def _span(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds >= _DAY:
        days = int(seconds // _DAY)
        return "%d day%s" % (days, "" if days == 1 else "s")
    if seconds >= 3600:
        hours = int(seconds // 3600)
        return "%d hour%s" % (hours, "" if hours == 1 else "s")
    minutes = max(1, int(seconds // 60))
    return "%d minute%s" % (minutes, "" if minutes == 1 else "s")

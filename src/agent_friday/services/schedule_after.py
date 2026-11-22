"""The ``after`` trigger: run job B when job A finishes successfully, no later than a deadline.

Example: ``{"trigger": "after", "spec": {"job": "sch_news_morning", "deadline_hour": 6, "deadline_minute": 0}}``: the
afternoon digest waits for the morning news, and runs by 06:00 whatever happened. The day's run is decided here, from the
records alone (A's record: its last success, its last run and status, whether it is running or about to retry):

* A succeeded today: B runs now, on fresh data;
* A failed today (retries spent), or the deadline has passed: B runs anyway, and what it makes says its data is stale and
  how old it is (``stale_words`` for the run's summary, ``stale_prompt`` for a prompt job);
* otherwise B waits.

A job cannot wait for itself and two jobs cannot wait for each other, directly or through others (``validate``).
Guarded by tests/unit/test_schedule_after.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from agent_friday.user_errors import UserFacingValueError

MAX_CHAIN = 50


def deadline_of(spec: Dict[str, Any], now: datetime) -> datetime:
    """Today's deadline on ``now``'s clock."""
    return now.replace(hour=int(spec.get("deadline_hour", 0)), minute=int(spec.get("deadline_minute", 0)),
                       second=0, microsecond=0)


def validate(own_id: Optional[str], spec: Dict[str, Any], records: Iterable[Dict[str, Any]]) -> None:
    """Refuse a dependency that cannot work: no job named, itself, a job that does not exist, or a loop (A after B after A,
    however long the chain). ``own_id`` is None for a schedule not made yet (it cannot be part of a loop)."""
    target = str((spec or {}).get("job") or "").strip()
    if not target:
        raise UserFacingValueError("Say which job this one runs after (spec.job).")
    if own_id and target == own_id:
        raise UserFacingValueError("A job cannot run after itself.")
    by_id = {r.get("id"): r for r in records}
    if target not in by_id:
        raise UserFacingValueError("There is no schedule %r to run after. List the schedules for their ids." % target)
    seen, cursor = {own_id} if own_id else set(), target
    for _ in range(MAX_CHAIN):
        if cursor in seen:
            raise UserFacingValueError("That would make a loop: %s already runs after this job, directly or through "
                                       "others." % target)
        seen.add(cursor)
        record = by_id.get(cursor) or {}
        if record.get("trigger") != "after":
            return
        cursor = str((record.get("spec") or {}).get("job") or "")
        if not cursor or cursor not in by_id:
            return
    raise UserFacingValueError("That chain of dependencies is too long.")


def age_words(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds >= 86400:
        days = int(seconds // 86400)
        return "%d day%s" % (days, "" if days == 1 else "s")
    if seconds >= 3600:
        hours, minutes = int(seconds // 3600), int(seconds % 3600 // 60)
        return "%d hour%s%s" % (hours, "" if hours == 1 else "s", (" %d minute%s" % (minutes, "" if minutes == 1 else "s")) if minutes else "")
    minutes = max(1, int(seconds // 60))
    return "%d minute%s" % (minutes, "" if minutes == 1 else "s")


def evaluate(spec: Dict[str, Any], upstream: Optional[Dict[str, Any]], now: datetime) -> Optional[Dict[str, Any]]:
    """None while B should wait; else ``{"stale": None}`` (run now on fresh data) or ``{"stale": {...}}`` (run anyway, the
    data is stale). ``upstream`` is A's record, or None when A no longer exists. ``now`` is on the job's clock."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    stamp = now.timestamp()
    up = upstream or {}
    last_ok = up.get("last_success_ts")
    ok_today = isinstance(last_ok, (int, float)) and last_ok >= today
    if ok_today:
        return {"stale": None}
    ran_today = isinstance(up.get("last_run_ts"), (int, float)) and up["last_run_ts"] >= today
    running = bool(up.get("in_flight")) or bool(up.get("retry_pending"))
    failed = ran_today and not running and str(up.get("last_status") or "") in ("failed", "timeout", "error")
    late = stamp >= deadline_of(spec, now).timestamp()
    if not (failed or late):
        return None
    if upstream is None:
        why = "the job it runs after no longer exists"
    elif failed:
        why = "it failed today"
    elif running:
        why = "it had not finished by the deadline"
    else:
        why = "it had not run by the deadline"
    return {"stale": {"job": spec.get("job"), "name": up.get("name") or spec.get("job"), "why": why,
                      "age_s": (stamp - last_ok) if isinstance(last_ok, (int, float)) else None}}


def stale_words(stale: Dict[str, Any]) -> str:
    """The sentence a run's summary starts with when it ran on stale data."""
    age = ("its last success was %s ago" % age_words(stale["age_s"])) if stale.get("age_s") is not None \
        else "it has no successful run on record"
    return "Stale data: this job runs after %s, but %s, so what it used is stale (%s). " % (
        stale.get("name"), stale.get("why"), age)


def stale_prompt(stale: Dict[str, Any]) -> str:
    """What a prompt job is told, ahead of its own prompt, so its output says so too."""
    age = ("%s old" % age_words(stale["age_s"])) if stale.get("age_s") is not None else "of unknown age (no successful run on record)"
    return ("IMPORTANT: this run could not wait for %s: %s. The data you are given from it is stale, %s. Begin your "
            "output with one line saying the data is stale and exactly how old it is, then do the job.\n\n"
            % (stale.get("name"), stale.get("why"), age))

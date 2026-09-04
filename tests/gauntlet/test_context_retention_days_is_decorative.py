"""Gauntlet finding F3 (queued for Stephen, not fixed — see
docs/audits/gauntlet-2026-09-03/progress.md "QUEUED FOR STEPHEN" / Q1).

Settings > Privacy > Context Logging > Retention Period persists
`context_retention_days` and reads it back (GET /api/context/stats), which
is enough to pass scripts/check_settings_readers.py's "has a reader" proxy.
But no code path ever branches deletion behaviour on it: the only thing that
ever removes a context-log file by age is a fully manual
`DELETE /api/context/range`, which requires the user to pick an explicit
date range and type the literal string "DELETE". There is no scheduled
sweep.

This probe pins that absence directly, the same way test_edition_scheduler_
wiring.py and test_goal_scheduler_wiring.py pin what IS scheduled: it proves
no builtin task in the scheduler's real registry (the one start_scheduler()
actually calls at boot) is wired to context-log retention.

This is deliberately NOT fixed. Automating deletion of the user's own log
data, unattended and overnight, is a real blast-radius decision (a bug
deletes more than intended) that needs Stephen's call: wire a real sweep, or
change the UI copy to stop implying automatic enforcement. This probe stays
RED until one of those happens — that is the correct, honest state for a
queued finding, not a bug in the probe.
"""
from __future__ import annotations


def test_no_scheduled_task_prunes_context_logs_by_retention():
    from agent_friday.services import scheduler as sched
    sched._register_default_builtin_tasks()

    def _mentions_retention(entry: dict) -> bool:
        haystack = " ".join(str(v) for v in
                             (entry.get("label", ""),) ).lower()
        return "context" in haystack and (
            "retention" in haystack or "prune" in haystack
            or "cleanup" in haystack or "log" in haystack)

    matches = [ref for ref, entry in sched.BUILTIN_TASKS.items()
               if _mentions_retention(entry)]
    assert matches, (
        "no builtin scheduled task prunes context logs by "
        "context_retention_days -- the Retention Period setting in "
        "Settings > Privacy > Context Logging persists and reads back, but "
        "nothing ever automatically deletes an old log entry. This probe "
        "should stay RED until Stephen decides whether to wire a real "
        "sweep or change the UI copy (see progress.md Q1)."
    )

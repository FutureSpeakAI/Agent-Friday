"""Gauntlet finding F3 — resolved under the maintainer's 2026-09-04 delegation to
Claude (docs/history/audits/gauntlet-2026-09-03/progress.md "QUEUED FOR STEPHEN" /
Q1; findings.jsonl F3).

Settings > Privacy > Context Logging > Retention Period persists
`context_retention_days` and reads it back (GET /api/context/stats), and
its own DEFAULT_SETTINGS comment has claimed "0 = keep forever; 30 / 90 /
180 / 365 = prune older" since it was added. But no code path ever branched
deletion behaviour on it: the only thing that ever removed a context-log
file by age was a fully manual `DELETE /api/context/range`, requiring the
user to pick an explicit date range and type the literal string "DELETE".
There was no scheduled sweep -- the setting's own promise was false.

Under the delegation's "false claim is a defect" and "the user always knows
what is happening to their data" principles, this was mechanical to fix
correctly: CONTEXT_LOG_DIR stores exactly one <YYYY-MM-DD>.jsonl file per
day (core._context_log_files), so retention is whole-file deletion, not row
surgery -- no ambiguity about partial-day handling, no schema, low blast
radius. core.prune_context_logs() does the deletion (a no-op when the
setting is 0, "keep forever", the default) and is now registered as the
scheduler's real "context_log_retention" builtin task, running daily like
every other builtin sweep in this codebase.

This probe used to pin the ABSENCE of that wiring (deliberately red). It now
pins the presence and correctness of the fix instead.
"""
from __future__ import annotations

from datetime import datetime, timedelta


def test_context_log_retention_is_a_registered_scheduled_task():
    from agent_friday.services import scheduler as sched
    sched._register_default_builtin_tasks()

    def _mentions_retention(entry: dict) -> bool:
        haystack = " ".join(str(v) for v in
                             (entry.get("label", ""),)).lower()
        return "context" in haystack and (
            "retention" in haystack or "prune" in haystack
            or "cleanup" in haystack or "log" in haystack)

    matches = [ref for ref, entry in sched.BUILTIN_TASKS.items()
               if _mentions_retention(entry)]
    assert matches, (
        "no builtin scheduled task prunes context logs by "
        "context_retention_days -- the Retention Period setting in "
        "Settings > Privacy > Context Logging persists and reads back, but "
        "nothing automatically deletes an old log entry (F3 regressed)."
    )
    assert "context_log_retention" in sched.BUILTIN_TASKS


def test_prune_context_logs_is_a_noop_when_retention_is_disabled(monkeypatch):
    from agent_friday import core

    monkeypatch.setattr(core, "_load_settings",
                         lambda: {"context_retention_days": 0})
    result = core.prune_context_logs()
    assert result["changed"] is False


def test_prune_context_logs_deletes_only_files_older_than_the_setting(monkeypatch, tmp_path):
    from agent_friday import core

    monkeypatch.setattr(core, "CONTEXT_LOG_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings",
                         lambda: {"context_retention_days": 30})

    today = datetime.now()
    old_day = (today - timedelta(days=45)).strftime("%Y-%m-%d")
    recent_day = (today - timedelta(days=5)).strftime("%Y-%m-%d")
    old_file = tmp_path / f"{old_day}.jsonl"
    recent_file = tmp_path / f"{recent_day}.jsonl"
    old_file.write_text('{"type": "old"}\n', encoding="utf-8")
    recent_file.write_text('{"type": "recent"}\n', encoding="utf-8")

    result = core.prune_context_logs()

    assert result["changed"] is True
    assert result["count"] == 1
    assert not old_file.exists(), (
        "prune_context_logs() left a context-log file older than the "
        "configured retention period in place"
    )
    assert recent_file.exists(), (
        "prune_context_logs() deleted a context-log file that was still "
        "within the configured retention period -- it must only remove "
        "days strictly older than the setting"
    )

"""Gauntlet finding F56 (claim-corpus sweep, 2026-09-04):
scoped_agents.cleanup_old_tasks() computed a `cutoff` timestamp but never
compared anything to it, and its removal loop was a bare `for tid in
to_remove[-50:]: pass  # Actually keep them for now` -- it never removed a
single task, regardless of age, since the function was written.

This probe proves the fix: an old completed task is actually removed, a
recent one is kept, and a still-running task is never touched regardless
of how old it is.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import agent_friday.services.scoped_agents as scoped_agents


def _task(status, hours_ago=None):
    t = scoped_agents.ScopedTask("t-" + status + str(hours_ago), "prompt", [])
    t.status = status
    if hours_ago is not None:
        when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        t.completed_at = when.replace(tzinfo=None).isoformat()
    return t


class TestCleanupOldTasksActuallyRemoves:
    def setup_method(self):
        scoped_agents._SCOPED_TASKS.clear()

    def teardown_method(self):
        scoped_agents._SCOPED_TASKS.clear()

    def test_an_old_completed_task_is_removed(self):
        old = _task("complete", hours_ago=48)
        scoped_agents._SCOPED_TASKS[old.task_id] = old

        scoped_agents.cleanup_old_tasks(max_age_hours=24)

        assert old.task_id not in scoped_agents._SCOPED_TASKS, (
            "a completed task older than max_age_hours was not removed -- "
            "cleanup_old_tasks() is still a no-op (F56 regressed)"
        )

    def test_a_recent_completed_task_is_kept(self):
        recent = _task("complete", hours_ago=1)
        scoped_agents._SCOPED_TASKS[recent.task_id] = recent

        scoped_agents.cleanup_old_tasks(max_age_hours=24)

        assert recent.task_id in scoped_agents._SCOPED_TASKS, (
            "a task within max_age_hours was removed -- the age check is "
            "over-aggressive"
        )

    def test_a_still_running_task_is_never_removed_regardless_of_age(self):
        running = scoped_agents.ScopedTask("t-running", "prompt", [])
        running.status = "running"
        running.started_at = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
        scoped_agents._SCOPED_TASKS[running.task_id] = running

        scoped_agents.cleanup_old_tasks(max_age_hours=24)

        assert running.task_id in scoped_agents._SCOPED_TASKS

    def test_a_failed_old_task_is_also_removed(self):
        failed = _task("failed", hours_ago=48)
        scoped_agents._SCOPED_TASKS[failed.task_id] = failed

        scoped_agents.cleanup_old_tasks(max_age_hours=24)

        assert failed.task_id not in scoped_agents._SCOPED_TASKS

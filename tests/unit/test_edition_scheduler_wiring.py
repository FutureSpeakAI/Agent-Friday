"""Confirms the Edition E0 scheduler wiring (services/scheduler.py's
`edition_daily` builtin) registers correctly and never collides with the
existing `news_morning` (07:00) trigger it sits alongside."""
from __future__ import annotations

from agent_friday.services import edition_engine as ee


class TestEditionSchedulerRegistration:
    def test_edition_daily_registers_as_a_builtin(self):
        from agent_friday.services import scheduler as sched
        sched._register_default_builtin_tasks()
        assert "edition_daily" in sched.BUILTIN_TASKS

    def test_edition_daily_calls_run_edition_job(self):
        from agent_friday.services import scheduler as sched
        sched._register_default_builtin_tasks()
        assert sched.BUILTIN_TASKS["edition_daily"]["fn"] is ee.run_edition_job

    def test_edition_daily_time_does_not_collide_with_news_morning(self):
        from agent_friday.services import scheduler as sched
        sched._register_default_builtin_tasks()
        edition_spec = sched.BUILTIN_TASKS["edition_daily"]["default_spec"]
        news_spec = sched.BUILTIN_TASKS["news_morning"]["default_spec"]
        assert (edition_spec["hour"], edition_spec["minute"]) != \
               (news_spec["hour"], news_spec["minute"])

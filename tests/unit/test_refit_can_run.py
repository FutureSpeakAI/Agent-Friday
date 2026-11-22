"""The built-in "Learning from your corrections" (laya_personal_refit) can run (Scheduler, item 31).

It was set to run "once a day, while you're away", but its own presence rule (Decision 10) runs it only while the owner is
signed in, the session unlocked and within 30 minutes of their last input. Every run was skipped, and a skip was recorded
as a failure and retried every five minutes. The rule stays; the trigger now fires inside the window the rule allows, the
status names the rule, and a skip is a skip. practice_trials and daily_creation are checked for the same contradiction.
Synthetic idle windows only."""
from __future__ import annotations

import pytest

from agent_friday.services import background_gate, laya_personal_fit as fit, local_scheduler, scheduler

MINUTE = 60.0


@pytest.fixture
def quiet(monkeypatch):
    """The refit's own conditions all hold, and the owner has been idle for ``state['idle_s']``."""
    state = {"idle_s": 4 * MINUTE, "unlocked": True, "ac": True, "vault": True}
    monkeypatch.setattr(fit, "presence_override", None)
    monkeypatch.setattr(fit, "session_unlocked", lambda: state["unlocked"])
    monkeypatch.setattr(fit, "on_ac_power", lambda: state["ac"])
    monkeypatch.setattr(fit.store, "vault_open", lambda: state["vault"])
    monkeypatch.setattr(fit.lp, "learning_state", lambda profile=None: {"on": True, "why": ""})
    monkeypatch.setattr(fit.lp, "current_profile", lambda: "owner")
    from agent_friday.services import thermal
    monkeypatch.setattr(thermal, "too_hot", lambda: (False, ""))
    monkeypatch.setattr(background_gate, "idle_state", lambda: {"idle": True, "idle_s": state["idle_s"], "os_readable": True})
    monkeypatch.setattr(scheduler, "_idle_settings", lambda: {"enabled": True, "after_s": 600.0, "from_hour": 0, "to_hour": 24})
    monkeypatch.setattr(local_scheduler, "interactive_busy_reason", lambda: "")
    from agent_friday.services import residency_arbiter, stand_down
    monkeypatch.setattr(residency_arbiter, "exclusive_lease", lambda: None)
    monkeypatch.setattr(stand_down, "is_stood_down", lambda: False)
    monkeypatch.setattr(scheduler, "_RUNNING", set())
    return state


def _refit_record(monkeypatch):
    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {})
    fit.register()
    meta = scheduler.BUILTIN_TASKS[fit.JOB_REF]
    return scheduler._seed_record(fit.JOB_REF, meta)


def test_an_idle_window_makes_the_refit_eligible_to_run_at_least_once(quiet, monkeypatch):
    """Simulated idle window: the owner has been idle four minutes, session unlocked, on AC, vault open."""
    rec = _refit_record(monkeypatch)
    assert rec["trigger"] == "idle_daily"
    assert scheduler._is_due_on_schedule(rec, scheduler._now_central()) is True
    assert fit.blocked_reason() == ""


def test_the_trigger_asks_for_less_quiet_than_the_presence_rule_allows():
    """The contradiction itself: a trigger that needs more idle time than the rule tolerates can never fire."""
    assert 0 < fit.IDLE_AFTER_S < fit.PRESENT_WITHIN_S


def test_a_window_the_trigger_allows_is_a_window_the_rule_allows_for_every_idle_length(quiet, monkeypatch):
    rec = _refit_record(monkeypatch)
    eligible = []
    for minutes in range(0, 61):
        quiet["idle_s"] = minutes * MINUTE
        triggered = scheduler._is_due_on_schedule(dict(rec), scheduler._now_central())
        allowed = fit.blocked_reason() == ""
        if triggered and allowed:
            eligible.append(minutes)
    assert eligible and min(eligible) == fit.IDLE_AFTER_S // 60 and max(eligible) == fit.PRESENT_WITHIN_S // 60


def test_the_old_ten_minute_wait_left_no_window_on_a_pc_that_locks_sooner(quiet, monkeypatch):
    """What happened: unlocked requires input within the lock timeout, the trigger required ten idle minutes."""
    rec = _refit_record(monkeypatch)
    rec["spec"] = {"idle_after_s": fit.OLD_IDLE_AFTER_S}
    quiet["idle_s"] = 4 * MINUTE
    assert scheduler._is_due_on_schedule(dict(rec), scheduler._now_central()) is False      # not yet idle enough
    quiet["idle_s"] = 11 * MINUTE
    quiet["unlocked"] = False                                                              # the PC locked at ten minutes
    assert scheduler._is_due_on_schedule(dict(rec), scheduler._now_central()) is True
    assert "locked" in fit.blocked_reason()                                                # ...and the rule refuses


def test_the_status_names_the_rule_that_blocks_it(quiet):
    quiet["unlocked"] = False
    locked = fit.blocked_reason()
    assert "Decision 10" in locked and "locked" in locked and "30 minutes" in locked
    quiet["unlocked"], quiet["idle_s"] = True, 50 * MINUTE
    away = fit.blocked_reason()
    assert "Decision 10" in away and "50 minutes" in away
    quiet["idle_s"], quiet["ac"] = 4 * MINUTE, False
    assert "battery" in fit.blocked_reason()


def test_an_untouched_old_default_moves_to_the_new_wait_once_and_an_edited_one_stays(quiet, monkeypatch, tmp_path):
    from agent_friday.services import builtin_overrides
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(builtin_overrides, "_file", lambda: tmp_path / "overrides.json")
    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {})
    fit.register()
    meta = scheduler.BUILTIN_TASKS[fit.JOB_REF]
    old = scheduler._seed_record(fit.JOB_REF, meta)
    old["spec"] = {"idle_after_s": fit.OLD_IDLE_AFTER_S}
    scheduler._write_store([old])
    scheduler._seed_and_reconcile()
    assert scheduler.get_schedule("sch_" + fit.JOB_REF)["spec"] == {"idle_after_s": fit.IDLE_AFTER_S}
    scheduler.update_schedule("sch_" + fit.JOB_REF, {"spec": {"idle_after_s": 900}}, by="owner")
    scheduler._seed_and_reconcile()
    assert scheduler.get_schedule("sch_" + fit.JOB_REF)["spec"] == {"idle_after_s": 900}


def test_a_skipped_run_is_recorded_as_a_skip_with_its_reason_and_is_not_retried(tmp_path, monkeypatch):
    """The refit's wait raised SkippedRun, which had no handler: it counted as a failure and retried every five minutes."""
    import threading
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "runs.jsonl")

    def waits():
        raise scheduler.SkippedRun("Laya's personal refit waits: you are not signed in and here.")

    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {"waits": {
        "fn": waits, "label": "Waits", "default_trigger": "daily", "default_spec": {"hour": 7, "minute": 0},
        "notify": "silent", "weekday_only": None, "default_enabled": True}})
    scheduler._seed_and_reconcile()

    class Sync(threading.Thread):
        def start(self):
            self.run()

    monkeypatch.setattr(scheduler.threading, "Thread", Sync)
    scheduler.dispatch(scheduler.get_schedule("sch_waits"), manual=True)
    rec = scheduler.get_schedule("sch_waits")
    assert rec["last_status"] == "skipped" and "not signed in" in rec["last_summary"]
    assert not rec.get("retry_pending") and not rec.get("retry_count")


def test_practice_trials_and_daily_creation_have_no_presence_rule_to_contradict_their_idle_trigger(quiet, monkeypatch):
    """Both are 'while you're away' built-ins; neither asks for the owner to be present, so an away window is a window."""
    import inspect
    from agent_friday.services import creations, trial_job
    for module in (trial_job, creations):
        source = inspect.getsource(module)
        assert "owner_present" not in source and "session_unlocked" not in source and "presence_reason" not in source
    away = {"trigger": "idle_daily", "enabled": True, "id": "sch_x", "spec": {"idle_after_s": 600}}
    quiet["idle_s"] = 6 * 60 * MINUTE                                     # away for six hours, session locked
    quiet["unlocked"] = False
    assert scheduler._is_due_on_schedule(dict(away), scheduler._now_central()) is True

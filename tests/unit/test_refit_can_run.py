"""The built-in "Learning from your corrections" (laya_personal_refit) can run (Scheduler, item 31).

It was set to run "once a day, while you're away", but Decision 10 let it run only while the owner was signed in, the
session unlocked and within 30 minutes of their last input: away and present at once. Every run was skipped, and a skip was
recorded as a failure and retried every five minutes. The owner amended the rule on 2026-11-22: "Allow it for as many hours
as the PC is idle." The refit now runs while the Windows session is signed in, locked or unlocked, for as long as the PC
stays idle; it never runs while he is using the PC or has a voice session open, and it yields when his input resumes. The
other gates stay: AC power, the vault open, the GPU lease, Battery Saver. practice_trials and daily_creation are checked for
the same contradiction. Synthetic idle windows only."""
from __future__ import annotations

import pytest

from agent_friday.services import background_gate, laya_personal_fit as fit, local_scheduler, power_state, scheduler

MINUTE = 60.0
HOUR = 3600.0


@pytest.fixture
def quiet(monkeypatch):
    """Every gate of the refit holds, and the owner has been idle for ``state['idle_s']``."""
    state = {"idle_s": 4 * MINUTE, "ac": True, "vault": True, "voice": False, "saver": False, "lease": None}
    monkeypatch.setattr(fit, "presence_override", None)
    monkeypatch.setattr(fit, "on_ac_power", lambda: state["ac"])
    monkeypatch.setattr(fit.store, "vault_open", lambda: state["vault"])
    monkeypatch.setattr(fit.lp, "learning_state", lambda profile=None: {"on": True, "why": ""})
    monkeypatch.setattr(fit.lp, "current_profile", lambda: "owner")
    from agent_friday.services import residency_arbiter, stand_down, thermal
    monkeypatch.setattr(thermal, "too_hot", lambda: (False, ""))
    monkeypatch.setattr(background_gate, "idle_state", lambda: {"idle": True, "idle_s": state["idle_s"], "os_readable": True})
    monkeypatch.setattr(background_gate, "interactive_kinds",
                        lambda: [background_gate.KIND_VOICE_CALL] if state["voice"] else [])
    monkeypatch.setattr(power_state, "battery_saver_on", lambda: state["saver"])
    monkeypatch.setattr(residency_arbiter, "exclusive_lease", lambda: state["lease"])
    monkeypatch.setattr(stand_down, "is_stood_down", lambda: False)
    monkeypatch.setattr(scheduler, "_idle_settings", lambda: {"enabled": True, "after_s": 600.0, "from_hour": 0, "to_hour": 24})
    monkeypatch.setattr(local_scheduler, "interactive_busy_reason", lambda: "")
    monkeypatch.setattr(scheduler, "_RUNNING", set())
    return state


def _refit_record(monkeypatch):
    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {})
    fit.register()
    return scheduler._seed_record(fit.JOB_REF, scheduler.BUILTIN_TASKS[fit.JOB_REF])


def _due(rec):
    return scheduler._is_due_on_schedule(dict(rec), scheduler._now_central())


def test_an_idle_window_makes_the_refit_eligible_to_run_at_least_once(quiet, monkeypatch):
    rec = _refit_record(monkeypatch)
    assert rec["trigger"] == "idle_daily"
    assert _due(rec) is True and fit.blocked_reason() == ""


def test_it_is_eligible_while_the_pc_is_idle_for_any_number_of_hours_there_is_no_cap(quiet, monkeypatch):
    """The owner: 'Allow it for as many hours as the PC is idle.' The session is locked, which the rule no longer asks about."""
    rec = _refit_record(monkeypatch)
    for hours in (0.5, 2, 8, 30, 200):
        quiet["idle_s"] = hours * HOUR
        assert _due(rec) is True and fit.blocked_reason() == "", hours


def test_it_is_eligible_overnight_when_the_pc_is_idle_the_scheduler_window_does_not_stop_it(quiet, monkeypatch):
    """The scheduler's default idle window is 09:00-23:00; 'as many hours as the PC is idle' includes the small hours."""
    from datetime import datetime
    monkeypatch.setattr(scheduler, "_idle_settings", lambda: {"enabled": True, "after_s": 600.0, "from_hour": 9, "to_hour": 23})
    rec = _refit_record(monkeypatch)
    quiet["idle_s"] = 4 * HOUR
    night = datetime(2026, 11, 22, 3, 30, tzinfo=scheduler._now_central().tzinfo)
    assert scheduler._is_due_on_schedule(dict(rec), night) is True
    other = {"trigger": "idle_daily", "enabled": True, "id": "sch_other", "spec": {"idle_after_s": 600}}
    assert scheduler._is_due_on_schedule(dict(other), night) is False          # any other idle job keeps the window


def test_it_is_not_eligible_while_the_owner_is_using_the_pc(quiet, monkeypatch):
    rec = _refit_record(monkeypatch)
    for seconds in (0, 5, 60, fit.IDLE_AFTER_S - 1):
        quiet["idle_s"] = seconds
        assert _due(rec) is False, seconds
        reason = fit.blocked_reason()
        assert "used the PC" in reason and "Decision 10, amended by owner 2026-11-22" in reason
    quiet["idle_s"] = fit.IDLE_AFTER_S
    assert fit.blocked_reason() == "" and _due(rec) is True


def test_it_never_runs_during_an_open_voice_session_however_idle_the_pc_is(quiet, monkeypatch):
    quiet["idle_s"] = 5 * HOUR
    quiet["voice"] = True
    assert fit.blocked_reason().startswith("a voice session is open") and "amended by owner" in fit.blocked_reason()
    quiet["voice"] = False
    assert fit.blocked_reason() == ""


@pytest.mark.parametrize("flag,value,words", [("ac", False, "battery"), ("vault", False, "vault is locked"),
                                              ("saver", True, "Battery Saver"), ("lease", {"role": "an image job"}, "GPU")])
def test_the_other_gates_stay(quiet, flag, value, words):
    quiet["idle_s"] = 5 * HOUR
    quiet[flag] = value
    assert words in fit.blocked_reason()


def test_when_input_resumes_the_refit_yields_at_the_next_checkpoint_and_applies_nothing(quiet, monkeypatch):
    """A refit under way asks yield_reason at each checkpoint; the owner's input ends the idle time between two of them."""
    import threading
    quiet["idle_s"] = 2 * HOUR
    later = []
    monkeypatch.setattr(fit.lp, "_build_lock", threading.RLock())
    monkeypatch.setattr(fit.lp, "rebuild", lambda profile, why: (quiet.__setitem__("idle_s", 3.0) or {"status": "ok"}))
    monkeypatch.setattr(fit.lp, "base_state", lambda: later.append("base") or None)
    out = fit.refit("owner", should_yield=fit.yield_reason)
    assert out["status"] == "yielded" and out["stage"] == "after_rebuild" and "amended by owner" in out["why"]
    assert later == []                                          # nothing after the checkpoint ran, so nothing was applied


def test_a_refit_that_is_not_the_scheduled_run_does_not_yield(quiet, monkeypatch):
    """The owner pressing 'refit now' is at the PC by definition: only the scheduled run passes yield_reason."""
    import threading
    quiet["idle_s"] = 1.0
    monkeypatch.setattr(fit.lp, "_build_lock", threading.RLock())
    monkeypatch.setattr(fit.lp, "rebuild", lambda profile, why: {"status": "no_check", "why": "x"})
    assert fit.refit("owner")["status"] == "no_check"


def test_the_trigger_fires_the_moment_the_pc_is_idle_long_enough_so_it_can_never_contradict_the_rule(quiet, monkeypatch):
    rec = _refit_record(monkeypatch)
    assert rec["spec"] == fit.IDLE_SPEC
    eligible = []
    for minutes in range(0, 12 * 60, 5):
        quiet["idle_s"] = minutes * MINUTE
        if _due(rec) and fit.blocked_reason() == "":
            eligible.append(minutes)
    assert eligible and min(eligible) * MINUTE >= fit.IDLE_AFTER_S and max(eligible) == 11 * 60 + 55


def test_an_untouched_old_default_moves_to_the_new_wait_once_and_an_edited_one_stays(quiet, monkeypatch, tmp_path):
    from agent_friday.services import builtin_overrides
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(builtin_overrides, "_file", lambda: tmp_path / "overrides.json")
    old = _refit_record(monkeypatch)
    old["spec"] = {"idle_after_s": fit.OLD_IDLE_AFTER_S}
    scheduler._write_store([old])
    scheduler._seed_and_reconcile()
    assert scheduler.get_schedule("sch_" + fit.JOB_REF)["spec"] == fit.IDLE_SPEC
    scheduler.update_schedule("sch_" + fit.JOB_REF, {"spec": {"idle_after_s": 900}}, by="owner")
    scheduler._seed_and_reconcile()
    assert scheduler.get_schedule("sch_" + fit.JOB_REF)["spec"] == {"idle_after_s": 900}


def test_a_skipped_run_is_recorded_as_a_skip_with_its_reason_and_is_not_retried(tmp_path, monkeypatch):
    """The refit's wait raised SkippedRun, which had no handler: it counted as a failure and retried every five minutes."""
    import threading
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "runs.jsonl")

    def waits():
        raise scheduler.SkippedRun("Laya's personal refit waits: you used the PC 30 seconds ago.")

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
    assert rec["last_status"] == "skipped" and "used the PC" in rec["last_summary"]
    assert not rec.get("retry_pending") and not rec.get("retry_count")


def test_practice_trials_and_daily_creation_have_no_presence_rule_to_contradict_their_idle_trigger(quiet):
    """Both are 'while you're away' built-ins; neither asks for the owner to be present, so an away window is a window."""
    import inspect
    from agent_friday.services import creations, trial_job
    for module in (trial_job, creations):
        source = inspect.getsource(module)
        assert "activity_reason" not in source and "session_unlocked" not in source and "owner_present" not in source
    away = {"trigger": "idle_daily", "enabled": True, "id": "sch_x", "spec": {"idle_after_s": 600}}
    quiet["idle_s"] = 6 * HOUR
    assert scheduler._is_due_on_schedule(dict(away), scheduler._now_central()) is True

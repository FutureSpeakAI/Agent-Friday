"""The ``on_idle`` trigger: a job that runs after N quiet minutes, and not during activity (Scheduler, item 28a).

Idle is no input from the owner for N minutes. It never starts during an open voice session, waits for the GPU lease,
and waits while Battery Saver is on or the computer runs on its battery. Synthetic jobs and a controlled clock only."""
from __future__ import annotations

import pytest

from agent_friday.services import background_gate, local_scheduler, power_state, scheduler
from agent_friday.services import workflow_operations, workflow_overview, schedule_tools
from agent_friday.services.library import runtime  # noqa: F401  (imported so a patch of psutil reaches one reader)

MINUTE = 60.0


@pytest.fixture
def quiet(monkeypatch):
    """A machine that is idle by every measure; each test turns one thing on."""
    state = {"idle_s": 15 * MINUTE, "voice": False, "interactive": "", "lease": None, "saver": False, "battery": False,
             "switch_on": True}
    monkeypatch.setattr(scheduler, "_idle_settings", lambda: {"enabled": state["switch_on"], "after_s": 600.0,
                                                              "from_hour": 0, "to_hour": 24})
    monkeypatch.setattr(background_gate, "idle_state", lambda: {"idle": True, "idle_s": state["idle_s"],
                                                                "os_readable": True})
    monkeypatch.setattr(background_gate, "interactive_kinds",
                        lambda: [background_gate.KIND_VOICE_CALL] if state["voice"] else [])
    monkeypatch.setattr(local_scheduler, "interactive_busy_reason", lambda: state["interactive"])
    from agent_friday.services import residency_arbiter, stand_down
    monkeypatch.setattr(residency_arbiter, "exclusive_lease", lambda: state["lease"])
    monkeypatch.setattr(stand_down, "is_stood_down", lambda: False)
    monkeypatch.setattr(power_state, "battery_saver_on", lambda: state["saver"])
    monkeypatch.setattr(power_state, "on_battery", lambda: state["battery"])
    monkeypatch.setattr(scheduler, "_RUNNING", set())
    return state


def _job(**spec):
    return {"id": "sch_idle_job", "name": "Idle job", "trigger": "on_idle", "enabled": True,
            "spec": dict({"idle_minutes": 10, "min_gap_minutes": 60}, **spec), "task": {"kind": "agent_prompt", "prompt": "x"}}


def _due(rec):
    return scheduler._is_due_on_schedule(rec, scheduler._now_central())


def test_an_idle_trigger_fires_after_n_quiet_minutes(quiet):
    assert _due(_job()) is True


def test_it_does_not_fire_during_activity_and_waits_for_the_full_n_minutes(quiet):
    quiet["idle_s"] = 2 * MINUTE
    assert _due(_job()) is False
    quiet["idle_s"] = 9 * MINUTE + 59
    assert _due(_job()) is False
    quiet["idle_s"] = 10 * MINUTE
    assert _due(_job()) is True


def test_n_comes_from_the_job_so_a_longer_quiet_is_needed_for_a_longer_n(quiet):
    quiet["idle_s"] = 15 * MINUTE
    assert _due(_job(idle_minutes=30)) is False
    quiet["idle_s"] = 31 * MINUTE
    assert _due(_job(idle_minutes=30)) is True


def test_a_run_is_followed_by_a_quiet_gap_before_the_next(quiet):
    import time
    rec = _job()
    rec["last_run_ts"] = time.time() - 10 * MINUTE
    assert _due(rec) is False
    rec["last_run_ts"] = time.time() - 61 * MINUTE
    assert _due(rec) is True


def test_it_never_starts_during_an_open_voice_session(quiet):
    quiet["voice"] = True
    assert _due(_job()) is False
    assert "voice" in scheduler.on_idle_blocked_reason(_job(), _job()["spec"], scheduler._now_central())
    quiet["voice"] = False
    assert _due(_job()) is True


def test_it_waits_for_the_gpu_lease(quiet):
    quiet["lease"] = {"role": "image job"}
    assert _due(_job()) is False
    assert "GPU" in scheduler.on_idle_blocked_reason(_job(), _job()["spec"], scheduler._now_central())
    quiet["lease"] = None
    assert _due(_job()) is True


@pytest.mark.parametrize("flag,words", [("saver", "Battery Saver"), ("battery", "battery")])
def test_it_waits_while_battery_saver_is_on_or_the_computer_runs_on_its_battery(quiet, flag, words):
    quiet[flag] = True
    assert _due(_job()) is False
    assert words in scheduler.on_idle_blocked_reason(_job(), _job()["spec"], scheduler._now_central())
    quiet[flag] = False
    assert _due(_job()) is True


def test_it_waits_while_an_answer_is_being_given_or_background_work_is_switched_off(quiet):
    quiet["interactive"] = "Friday is answering you"
    assert _due(_job()) is False
    quiet["interactive"] = ""
    quiet["switch_on"] = False
    assert _due(_job()) is False


def test_a_job_switched_off_or_already_running_does_not_fire(quiet):
    rec = _job()
    rec["enabled"] = False
    assert _due(rec) is False
    rec["enabled"] = True
    scheduler._RUNNING.add(rec["id"])
    assert _due(rec) is False


def test_the_timing_check_accepts_on_idle_with_defaults_and_refuses_nonsense():
    got = workflow_operations.validate_timing({"trigger": "on_idle", "spec": {}})
    assert got == {"trigger": "on_idle", "spec": {"idle_minutes": 10, "min_gap_minutes": 60}}
    for bad in ({"idle_minutes": 0}, {"idle_minutes": 5000}, {"idle_minutes": "ten"}, {"min_gap_minutes": -1},
                {"min_gap_minutes": 0}, {"from_hour": 25}, {"to_hour": -1}):
        with pytest.raises(Exception):
            workflow_operations.validate_timing({"trigger": "on_idle", "spec": bad})


def test_the_owner_reads_it_in_plain_words_and_the_model_may_ask_for_it():
    text = workflow_overview.describe_when({"trigger": "on_idle", "spec": {"idle_minutes": 15, "min_gap_minutes": 120}})
    assert "away from the computer for 15 minutes" in text and "2 hours" in text
    assert "on_idle" in schedule_tools.TRIGGERS
    props = schedule_tools.TOOL_SCHEMAS[0]["input_schema"]["properties"]["spec"]["properties"]
    assert "idle_minutes" in props and "min_gap_minutes" in props


@pytest.mark.parametrize("status,saver,battery", [((0, 1), True, True), ((1, 0), False, False), ((0, 0), False, True)])
def test_power_state_reads_windows_power_status(monkeypatch, status, saver, battery):
    monkeypatch.setattr(power_state, "_windows_status", lambda: status)
    assert power_state.battery_saver_on() is saver and power_state.on_battery() is battery
    assert bool(power_state.blocked_reason()) is (saver or battery)


def test_power_state_counts_an_unreadable_machine_as_plugged_in(monkeypatch):
    monkeypatch.setattr(power_state, "_windows_status", lambda: None)
    import sys
    fake = type(sys)("psutil")
    fake.sensors_battery = lambda: None
    monkeypatch.setitem(sys.modules, "psutil", fake)
    assert power_state.on_battery() is False and power_state.blocked_reason() == ""


def test_a_zero_gap_cannot_make_it_fire_on_every_tick(quiet):
    """The timing check refuses a gap of 0, and a stored 0 is still held to a minute."""
    import time
    rec = _job(min_gap_minutes=0)
    rec["last_run_ts"] = time.time() - 5
    assert _due(rec) is False


def test_a_job_that_never_ran_is_due_the_first_quiet_moment(quiet):
    rec = _job()
    assert "last_run_ts" not in rec and _due(rec) is True


def test_when_the_idle_time_cannot_be_read_it_does_not_fire(quiet, monkeypatch):
    monkeypatch.setattr(background_gate, "idle_state", lambda: {"idle": False, "idle_s": 99 * HOUR, "os_readable": False})
    assert _due(_job()) is False


def test_the_owners_settings_window_applies_unless_the_job_names_its_own(quiet, monkeypatch):
    """The Settings idle window (09:00-23:00 by default) keeps a PC left on overnight quiet; a job can ask for its own."""
    from datetime import datetime
    monkeypatch.setattr(scheduler, "_idle_settings", lambda: {"enabled": True, "after_s": 600.0, "from_hour": 9, "to_hour": 23})
    night = datetime(2026, 11, 22, 3, 30, tzinfo=scheduler._now_central().tzinfo)
    noon = datetime(2026, 11, 22, 12, 0, tzinfo=scheduler._now_central().tzinfo)
    assert scheduler._is_due_on_schedule(_job(), night) is False
    assert "outside the idle window" in scheduler.on_idle_blocked_reason(_job(), _job()["spec"], night)
    assert scheduler._is_due_on_schedule(_job(), noon) is True
    assert scheduler._is_due_on_schedule(_job(from_hour=0, to_hour=24), night) is True


def test_the_workflow_tool_can_ask_for_it_too():
    from agent_friday.services import workflow_tools
    text = repr(workflow_tools.TOOL_SCHEMAS if hasattr(workflow_tools, "TOOL_SCHEMAS") else workflow_tools.__dict__)
    assert "on_idle" in text and "idle_minutes" in text

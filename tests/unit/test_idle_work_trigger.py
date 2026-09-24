"""Daily creation runs while the user is away, and says why when it doesn't.

Stephen, 2026-09-24: "Why doesn't the daily creation run by default during idle
time?"

IT WAS NOT RUNNING AT ALL, and the reason was neither cost nor GPU contention.
`sch_daily_creation` in his schedules.json carried:

    "trigger": "daily", "spec": {"hour": 8}, "enabled": false,
    "last_run_ts": 2026-09-09, "last_status": "complete"

Switched off. The newest artifact in ~/.friday/creations is 2026-08-30. So the
honest answer to "why wasn't it running" is: somebody turned it off, and nothing
ever said so.

It now uses an `idle_daily` trigger: once a day, while he is away, inside a
window, with the GPU free and Friday not stood down. Every condition is a reason
to WAIT rather than to fail, and the day's mark is only set when it actually runs
-- so a day he never steps away simply produces nothing, and the reason is
available rather than inferred.
"""

import datetime

import pytest


@pytest.fixture
def sched(monkeypatch):
    from agent_friday.services import scheduler
    # Away, nothing running, no lease, not stood down: the "can run" baseline.
    from agent_friday.services import work_queue
    monkeypatch.setattr(work_queue, "idle_seconds", lambda: 9999.0)
    monkeypatch.setattr(scheduler, "_RUNNING", set(), raising=False)
    import agent_friday.services.residency_arbiter as ra
    monkeypatch.setattr(ra, "exclusive_lease", lambda: None)
    from agent_friday.services import stand_down as sd
    monkeypatch.setattr(sd, "is_stood_down", lambda: False)
    return scheduler


NOON = datetime.datetime(2026, 9, 24, 12, 0, 0)


def test_it_can_run_when_the_user_is_away(sched):
    assert sched.idle_work_blocked_reason({}, {}, NOON) == ""


def test_it_waits_while_the_user_is_at_the_keyboard(sched, monkeypatch):
    from agent_friday.services import work_queue
    monkeypatch.setattr(work_queue, "idle_seconds", lambda: 5.0)
    why = sched.idle_work_blocked_reason({}, {}, NOON)
    assert why and "active" in why, why


def test_it_waits_outside_the_window(sched):
    why = sched.idle_work_blocked_reason({}, {}, datetime.datetime(2026, 9, 24, 3, 0))
    assert "window" in why, why


def test_it_waits_while_friday_is_stood_down(sched, monkeypatch):
    """The machine was handed to the user; idle work is exactly what must not
    wake the card."""
    from agent_friday.services import stand_down as sd
    monkeypatch.setattr(sd, "is_stood_down", lambda: True)
    why = sched.idle_work_blocked_reason({}, {}, NOON)
    assert "stood down" in why, why


def test_it_waits_while_the_gpu_is_leased(sched, monkeypatch):
    """The documented failure: "An hourly heartbeat launched while I was running
    my last image job and the whole computer slowed to a crawl.\""""
    import agent_friday.services.residency_arbiter as ra
    monkeypatch.setattr(ra, "exclusive_lease", lambda: {"role": "image"})
    why = sched.idle_work_blocked_reason({}, {}, NOON)
    assert "GPU" in why, why


def test_it_waits_while_another_scheduled_run_is_going(sched, monkeypatch):
    monkeypatch.setattr(sched, "_RUNNING", {"sch_other"}, raising=False)
    why = sched.idle_work_blocked_reason({}, {}, NOON)
    assert "busy" in why, why


def test_the_switch_turns_it_off(sched, monkeypatch):
    import agent_friday.core as core
    real = core._load_settings
    monkeypatch.setattr(core, "_load_settings",
                        lambda *a, **k: dict(real() or {},
                                             idle_work={"enabled": False}))
    why = sched.idle_work_blocked_reason({}, {}, NOON)
    assert "switched off" in why, why


def test_the_window_is_configurable_per_schedule(sched):
    spec = {"from_hour": 1, "to_hour": 5}
    assert sched.idle_work_blocked_reason({}, spec,
                                          datetime.datetime(2026, 9, 24, 3, 0)) == ""


def test_the_shipped_default_is_on_with_a_daytime_window():
    from agent_friday.core import DEFAULT_SETTINGS
    blk = DEFAULT_SETTINGS.get("idle_work") or {}
    assert blk.get("enabled") is True, "idle work must ship ON; that was the ask"
    assert 0 < blk.get("from_hour", 0) < blk.get("to_hour", 0) <= 24


def test_the_trigger_is_wired_into_is_due(sched, monkeypatch):
    """End to end through the real `_is_due`."""
    rec = {"id": "sch_daily_creation", "enabled": True,
           "trigger": "idle_daily", "spec": {}, "last_run_date": None}
    assert sched._is_due(rec, NOON) is True

    from agent_friday.services import work_queue
    monkeypatch.setattr(work_queue, "idle_seconds", lambda: 1.0)
    assert sched._is_due(rec, NOON) is False, "ran while the user was active"


def test_it_still_runs_only_once_a_day(sched):
    rec = {"id": "s", "enabled": True, "trigger": "idle_daily", "spec": {},
           "last_run_date": NOON.strftime("%Y-%m-%d")}
    assert sched._is_due(rec, NOON) is False


def test_daily_creation_is_declared_local_only():
    """Idle work on the local seat is the point: it costs nothing."""
    from agent_friday.services import scheduler
    assert "sch_daily_creation" in scheduler.LOCAL_ONLY_BY_DEFAULT

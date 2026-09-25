"""A schedule can fire on several days a week, and can run a saved workflow.

"Every weekday" is the most common thing a person asks an automation for; a
weekly trigger that knew only one day could not say it. And a workflow with
more than one step could be run by hand but never on a schedule, which split
the Workflows screen into two unrelated lists.
"""
from datetime import datetime, timedelta

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import scheduler as s


@pytest.fixture(autouse=True)
def _clean_store(friday_dir):
    if s.SCHEDULES_FILE.exists():
        s.SCHEDULES_FILE.unlink()
    if s.RUNS_FILE.exists():
        s.RUNS_FILE.unlink()
    s._RUNNING.clear()
    yield


# ── several days a week ─────────────────────────────────────────────────────

WEEKDAYS = {"enabled": True, "trigger": "weekly",
            "spec": {"weekdays": [0, 1, 2, 3, 4], "hour": 7, "minute": 30}}


def test_weekdays_fire_monday_to_friday_only():
    # 2026-06-29 is a Monday.
    for offset in range(5):
        day = datetime(2026, 6, 29, 8, 0) + timedelta(days=offset)
        assert s._is_due(dict(WEEKDAYS), day), day
    assert not s._is_due(dict(WEEKDAYS), datetime(2026, 7, 4, 8, 0))   # Saturday
    assert not s._is_due(dict(WEEKDAYS), datetime(2026, 7, 5, 8, 0))   # Sunday


def test_weekdays_respect_the_time_and_the_days_mark():
    assert not s._is_due(dict(WEEKDAYS), datetime(2026, 6, 29, 7, 0))
    rec = dict(WEEKDAYS, last_run_date="2026-06-29")
    assert not s._is_due(rec, datetime(2026, 6, 29, 9, 0))
    assert s._is_due(rec, datetime(2026, 6, 30, 9, 0))


def test_a_single_weekday_record_keeps_its_meaning():
    rec = {"enabled": True, "trigger": "weekly",
           "spec": {"weekday": 3, "hour": 9, "minute": 0}}
    assert s._is_due(rec, datetime(2026, 7, 2, 10, 0))        # Thursday
    assert not s._is_due(rec, datetime(2026, 7, 1, 10, 0))    # Wednesday


def test_next_run_for_weekdays_skips_the_weekend():
    friday_evening = datetime(2026, 7, 3, 18, 0)
    nxt = datetime.fromtimestamp(s._next_run_ts(dict(WEEKDAYS), friday_evening))
    assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 7, 6, 7, 30)


def test_next_run_after_todays_run_is_the_next_listed_day():
    rec = {"enabled": True, "trigger": "weekly",
           "spec": {"weekdays": [0, 3], "hour": 9, "minute": 0},
           "last_run_date": "2026-06-29"}
    monday_early = datetime(2026, 6, 29, 8, 0)   # before 9, but today already ran
    nxt = datetime.fromtimestamp(s._next_run_ts(rec, monday_early))
    assert (nxt.month, nxt.day) == (7, 2)


def test_bad_weekday_entries_fall_back_to_the_single_day():
    assert s._spec_weekdays({"weekdays": ["x", 9], "weekday": 2}) == [2]
    assert s._spec_weekdays({"weekdays": [4, 0, 4]}) == [0, 4]


# ── a schedule that runs a saved workflow ───────────────────────────────────

def _status(states, steps=2):
    """A fake chain_run_status that walks through `states`, one per read."""
    seq = list(states)

    def fake(slug):
        st = seq.pop(0) if len(seq) > 1 else seq[0]
        return {"state": st, "steps": [
            {"index": i, "name": f"s{i}", "status": "completed" if st == "completed" else
             ("failed" if st == "failed" and i == 1 else "completed" if i == 0 else "pending"),
             "result_tail": "memo written" if i == steps - 1 else "", "reason": "bad input"}
            for i in range(steps)]}
    return fake


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(s, "WORKFLOW_POLL_S", 0)
    monkeypatch.setattr(s, "WORKFLOW_STALL_S", 0)
    started = []
    monkeypatch.setattr(ag, "run_workflow_chain",
                        lambda slug, conversation_id=None: started.append(slug) or "t1")
    return started


def test_workflow_schedule_runs_the_chain_and_returns_its_last_result(monkeypatch, fast):
    monkeypatch.setattr(ag, "chain_run_status", _status(["running", "running", "completed"]))
    rec = {"id": "w1", "task": {"kind": "workflow", "ref": "tips"}}
    assert s._run_task(rec) == "memo written"
    assert fast == ["tips"]


def test_a_failure_that_persists_fails_the_run_and_names_the_step(monkeypatch, fast):
    monkeypatch.setattr(ag, "chain_run_status", _status(["running", "failed", "failed"]))
    with pytest.raises(RuntimeError, match=r"step 2 \(s1\) failed: bad input"):
        s._run_task({"id": "w1", "task": {"kind": "workflow", "ref": "tips"}})


def test_a_failure_that_retries_is_not_the_end(monkeypatch, fast):
    monkeypatch.setattr(ag, "chain_run_status",
                        _status(["failed", "running", "completed"]))
    assert s._run_task({"id": "w1", "task": {"kind": "workflow", "ref": "tips"}}) == "memo written"


def test_a_workflow_that_stops_partway_says_how_far_it_got(monkeypatch, fast):
    monkeypatch.setattr(ag, "chain_run_status", _status(["idle"]))
    with pytest.raises(RuntimeError, match="stopped after 1 of 2 steps"):
        s._run_task({"id": "w1", "task": {"kind": "workflow", "ref": "tips"}})


def test_a_missing_workflow_is_an_error_not_a_silent_success(monkeypatch):
    monkeypatch.setattr(ag, "run_workflow_chain", lambda slug, conversation_id=None: None)
    with pytest.raises(RuntimeError, match="missing or has no steps"):
        s._run_task({"id": "w1", "task": {"kind": "workflow", "ref": "gone"}})

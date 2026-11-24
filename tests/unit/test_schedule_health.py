"""The System workspace flags a scheduled job that is not keeping up (services/schedule_health). Synthetic records."""
from __future__ import annotations

import json
import time

import pytest

from agent_friday.services import schedule_health as sh

HOUR, DAY = 3600.0, 86400.0
NOW = 1_800_000_000.0


def _rec(**kw):
    base = {"id": "sch_x", "name": "Job", "trigger": "interval", "spec": {"every_minutes": 60}, "enabled": True,
            "created": NOW - 30 * DAY, "in_flight": False}
    base.update(kw)
    return base


@pytest.mark.parametrize("trigger,spec,expected", [
    ("interval", {"every_minutes": 60}, HOUR), ("interval", {"every_minutes": "30"}, 1800.0),
    ("daily", {"hour": 7}, DAY), ("idle_daily", {"idle_after_s": 600}, DAY), ("weekly", {}, 7 * DAY),
    ("once", {}, None), ("interval", {"every_minutes": 0}, None), ("interval", {}, None), ("event", {}, None)])
def test_the_expected_interval_of_each_trigger(trigger, spec, expected):
    assert sh.expected_interval_s({"trigger": trigger, "spec": spec}) == expected


def test_the_last_success_is_the_newest_completed_run_of_that_schedule():
    runs = [{"id": "a", "status": "complete", "ended": 100.0}, {"id": "a", "status": "failed", "ended": 900.0},
            {"id": "b", "status": "complete", "ended": 500.0}, {"id": "a", "status": "complete", "ended": 300.0}]
    assert sh.last_success(runs, "a") == 300.0 and sh.last_success(runs, "none") is None


def test_a_missed_run_raises_the_overdue_flag():
    """Simulated missed run: an hourly job whose last success was three hours ago."""
    flag = sh.evaluate(_rec(), NOW, NOW - 3 * HOUR)
    assert flag["state"] == sh.OVERDUE and "3 hours" in flag["reason"] and "expected every 1 hour" in flag["reason"]


def test_a_job_inside_twice_its_interval_is_ok_and_exactly_at_the_limit_is_not_yet_overdue():
    assert sh.evaluate(_rec(), NOW, NOW - 1.5 * HOUR)["state"] == sh.OK
    assert sh.evaluate(_rec(), NOW, NOW - 2 * HOUR)["state"] == sh.OK
    assert sh.evaluate(_rec(), NOW, NOW - 2 * HOUR - 1)["state"] == sh.OVERDUE


def test_a_daily_job_silent_for_three_days_is_overdue_and_a_job_that_never_succeeded_counts_from_its_creation():
    daily = _rec(trigger="daily", spec={"hour": 16}, created=NOW - 30 * DAY)
    assert sh.evaluate(daily, NOW, NOW - 3 * DAY)["state"] == sh.OVERDUE
    never = sh.evaluate(daily, NOW, None)
    assert never["state"] == sh.OVERDUE and "never succeeded" in never["reason"]
    assert sh.evaluate(_rec(trigger="daily", spec={"hour": 16}, created=NOW - DAY), NOW, None)["state"] == sh.OK


def test_a_hung_run_raises_the_stalled_flag():
    """Simulated hung run: in flight for twelve minutes and nothing recorded since it started."""
    flag = sh.evaluate(_rec(in_flight=True, started_at=NOW - 12 * 60), NOW, NOW - 10)
    assert flag["state"] == sh.STALLED and "12 minutes" in flag["reason"] and "no progress" in flag["reason"]


def test_a_run_that_reported_progress_recently_is_not_stalled_and_a_young_run_is_not_stalled():
    recent = _rec(in_flight=True, started_at=NOW - 40 * 60, progress_at=NOW - 60)
    assert sh.evaluate(recent, NOW, NOW - 10)["state"] == sh.OK
    assert sh.evaluate(_rec(in_flight=True, started_at=NOW - 5 * 60), NOW, NOW - 10)["state"] == sh.OK
    old = _rec(in_flight=True, started_at=NOW - 40 * 60, progress_at=NOW - 20 * 60)
    assert sh.evaluate(old, NOW, NOW - 10)["state"] == sh.STALLED


def test_a_stalled_run_outranks_overdue_and_a_switched_off_job_is_never_flagged():
    both = sh.evaluate(_rec(in_flight=True, started_at=NOW - 20 * 60), NOW, NOW - 5 * HOUR)
    assert both["state"] == sh.STALLED
    assert sh.evaluate(_rec(enabled=False), NOW, NOW - 99 * DAY)["state"] == sh.OK
    assert sh.evaluate(_rec(trigger="once"), NOW, None)["state"] == sh.OK


def test_the_schedule_list_carries_the_flag(tmp_path, monkeypatch):
    """Through the real list: a missed hourly run and a hung run both come back flagged."""
    from agent_friday.services import scheduler
    now = time.time()
    store = [dict(_rec(id="sch_missed", created=now - 30 * DAY, last_run_ts=now - 3 * HOUR)),
             dict(_rec(id="sch_hung", in_flight=True, started_at=now - 15 * 60, created=now - 30 * DAY)),
             dict(_rec(id="sch_fine", created=now - 30 * DAY))]
    runs = [{"id": "sch_missed", "status": "complete", "ended": now - 3 * HOUR},
            {"id": "sch_hung", "status": "complete", "ended": now - 20 * 60},
            {"id": "sch_fine", "status": "complete", "ended": now - 600}]
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "runs.jsonl")
    (tmp_path / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in runs) + "\n", encoding="utf-8")
    monkeypatch.setattr(scheduler, "_read_store", lambda: store)
    got = {r["id"]: r for r in scheduler.list_schedules()}
    assert got["sch_missed"]["health"] == "overdue" and got["sch_hung"]["health"] == "stalled"
    assert got["sch_fine"]["health"] == "ok" and got["sch_missed"]["health_reason"]

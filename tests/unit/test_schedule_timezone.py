"""Scheduled actions keep their timezone and daylight-saving behaviour explicitly (NS-32.16-1).

Before: every schedule was read on one hard-coded clock (America/Chicago) whatever the owner's
timezone setting, and a schedule stored neither its timezone nor how it meets a clock change. Now a
schedule the owner makes stores the timezone it keeps (theirs when it was made, or one asked for)
and its DST rule ("wall_clock"); it comes due on its own wall clock; built-in routines follow the
owner's timezone; the list shows both. Synthetic schedules only.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent_friday.services import scheduler as s

LONDON = ZoneInfo("Europe/London")
CHICAGO = ZoneInfo("America/Chicago")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "SCHEDULES_FILE", tmp_path / "schedules.json")
    settings = {"timezone": "Europe/London"}
    monkeypatch.setattr(s, "_load_settings", lambda: dict(settings))
    return settings


def _job(**extra):
    return dict({"name": "Synthetic morning note", "trigger": "daily", "spec": {"hour": 9, "minute": 0},
                 "task": {"kind": "agent_prompt", "prompt": "Write the morning note."}}, **extra)


def test_a_new_schedule_stores_its_timezone_and_dst_rule(store):
    rec = s.register_schedule(_job())
    assert rec.get("tz") == "Europe/London", "the schedule does not say which clock it keeps"
    assert rec.get("dst") == "wall_clock"


def test_a_schedule_comes_due_on_its_own_wall_clock(store):
    rec = s.register_schedule(_job())
    # 09:05 in London is 03:05 in Chicago: the job made for 9:00 is due now
    now_london = datetime(2026, 11, 4, 9, 5, tzinfo=LONDON)
    rec["created"] = now_london.timestamp() - 7200
    assert s._is_due_on_schedule(dict(rec), now_london.astimezone(CHICAGO)) is True


def test_a_timezone_asked_for_is_kept_and_a_bad_one_is_refused(store):
    rec = s.register_schedule(_job(tz="Asia/Tokyo"))
    assert rec["tz"] == "Asia/Tokyo"
    out = s.update_schedule(rec["id"], {"tz": "Not/AZone"})
    assert out["tz"] == "Asia/Tokyo", "a value that is not a timezone was stored"
    out = s.update_schedule(rec["id"], {"tz": "America/Chicago"})
    assert out["tz"] == "America/Chicago"


def test_without_a_timezone_setting_the_default_zone_is_used(store):
    store["timezone"] = ""
    assert s.register_schedule(_job()).get("tz") == s.DEFAULT_ZONE


def test_the_list_shows_the_timezone_and_how_a_clock_change_is_met(store):
    rec = s.register_schedule(_job())
    view = s.schedule_view(rec)
    assert view.get("timezone") == "Europe/London" and view.get("dst") == "wall_clock"
    assert "daylight-saving" in (view.get("dst_text") or "")


def test_a_time_skipped_by_spring_forward_runs_just_after_the_gap(store):
    rec = s.register_schedule(_job(tz="America/Chicago", spec={"hour": 2, "minute": 30}))
    before = datetime(2027, 3, 14, 1, 0, tzinfo=CHICAGO)          # US clocks go forward at 02:00
    nxt = datetime.fromtimestamp(s._next_run_ts(dict(rec, created=before.timestamp() - 60), before), CHICAGO)
    assert (nxt.month, nxt.day, nxt.hour, nxt.minute) == (3, 14, 3, 30)

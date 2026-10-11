"""A calendar question that names a day asks for that day.

"What about tomorrow?" after "What's on my calendar today?" re-ran the
calendar with empty arguments, so the speaker was handed both days and had to
pick. The router now passes the day it heard as an ISO date, and the tool
returns that day's events; a day beyond the fetched window says so.
"""
import datetime
import json

import pytest

from agent_friday.services import laya_router as lr
from agent_friday.services import voice_engine as ve

TODAY = datetime.date(2026, 10, 14)            # a Wednesday
TOMORROW = (TODAY + datetime.timedelta(days=1)).isoformat()


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(lr, "_today", lambda: TODAY)


@pytest.mark.parametrize("words,day", [
    ("What about tomorrow?", TOMORROW),
    ("and tomorrow?", TOMORROW),
    ("What's on my calendar tomorrow", TOMORROW),
    ("what's on my calendar today", TODAY.isoformat()),
    ("what's on tonight", TODAY.isoformat()),
    ("the day after tomorrow", "2026-10-16"),
    ("how about Friday", "2026-10-16"),
    ("how about Wednesday", TODAY.isoformat()),
    ("how about Monday", "2026-10-19"),
])
def test_the_router_passes_the_day_it_heard(words, day):
    assert lr._calendar_args(words)["day"] == day


def test_a_question_that_names_no_day_passes_none():
    assert "day" not in lr._calendar_args("what meetings do I have")


def test_the_follow_up_carries_the_new_day_to_the_calendar():
    r = lr.follow_up("What about tomorrow?", {"tool": "query_calendar", "args": {}})
    assert r.decision == "tool" and r.tool == "query_calendar"
    assert r.args == {"day": TOMORROW}


def test_the_first_turn_that_names_a_day_asks_for_it():
    r = lr.route("What's on my calendar tomorrow?", tools=["query_calendar"], log=False)
    assert r.tool == "query_calendar" and r.args.get("day") == TOMORROW


def _events():
    return [
        {"title": "Standup", "start_time": "2026-10-14T09:00:00", "end_time": "2026-10-14T09:30:00"},
        {"title": "Dentist", "start_time": "2026-10-15T14:00:00", "end_time": "2026-10-15T15:00:00"},
        {"title": "Retreat", "start_time": "2026-10-15", "end_time": "2026-10-16"},
        {"title": "Offsite", "start_time": "2026-10-14", "end_time": "2026-10-15"},
    ]


@pytest.fixture()
def calendar(monkeypatch):
    monkeypatch.setattr(ve, "_voice_calendar", lambda: _events())
    monkeypatch.setattr(ve, "_google_section_error", lambda e: None)

    class _DT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 14, 8, 0, 0)
    monkeypatch.setattr(ve, "datetime", _DT)


def _titles(day):
    out = json.loads(ve._tool_query_calendar({"day": day} if day else {}))
    return out, [e["title"] for e in out["events"]]


def test_the_tool_returns_only_the_asked_day(calendar):
    out, titles = _titles(TOMORROW)
    assert sorted(titles) == ["Dentist", "Retreat"] and out["count"] == 2


def test_today_leaves_out_tomorrow_and_keeps_an_all_day_event(calendar):
    _out, titles = _titles(TODAY.isoformat())
    assert sorted(titles) == ["Offsite", "Standup"]


def test_with_no_day_both_days_are_read_as_before(calendar):
    _out, titles = _titles(None)
    assert len(titles) == 4


def test_a_day_beyond_the_fetched_window_says_so_instead_of_reading_empty(calendar):
    out, titles = _titles("2026-10-19")
    assert titles == [] and out["outside_window"] is True
    assert "can only read today's and tomorrow's" in out["note"]

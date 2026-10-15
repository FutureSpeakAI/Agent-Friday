"""GET /api/calendar/freebusy: a week's busy blocks and free time, narrowed to some calendars when Friday's
chip says so, refused for a range it cannot mean. Synthetic data only."""
from __future__ import annotations

import pytest


@pytest.fixture
def views(monkeypatch):
    from agent_friday.services import calendar_views as cv
    asked = []
    monkeypatch.setattr(cv, "multi_calendar_available", lambda: True)
    monkeypatch.setattr(cv, "cached_calendars", lambda fresh_for=600: {"calendars": [], "accounts": []})

    def fake(start, end, keys=None, calendars=None):
        asked.append(keys)
        return {"events": [{"title": "Private thing", "start_time": "2026-10-19T10:00:00-05:00",
                            "end_time": "2026-10-19T11:00:00-05:00", "calendar_name": "Family"}],
                "errors": [], "calendars": [{"key": "home|primary", "on": True}, {"key": "home|fam", "on": True}]}
    monkeypatch.setattr(cv, "events_in_range", fake)
    import agent_friday.routes.calendar as rc
    monkeypatch.setattr(rc, "_load_local_events", lambda: [])
    return asked


def test_a_week_of_free_and_busy_names_calendars_and_never_titles(client, views):
    r = client.get("/api/calendar/freebusy?start=2026-10-18&end=2026-10-25")
    d = r.get_json()
    assert r.status_code == 200 and len(d["days"]) == 7
    assert "Private thing" not in r.get_data(as_text=True)
    assert any(b["calendars"] == ["Family"] for x in d["days"] for b in x["busy"])
    assert views == [None]


def test_friday_s_only_chip_narrows_the_read(client, views):
    client.get("/api/calendar/freebusy?start=2026-10-18&end=2026-10-25&keys=home%7Cfam")
    assert views == [["home|fam"]]


@pytest.mark.parametrize("q", ["start=2026-10-18&end=2026-11-18", "start=2026-10-25&end=2026-10-18", "start=x&end=y"])
def test_a_range_it_cannot_mean_is_refused(client, views, q):
    assert client.get("/api/calendar/freebusy?" + q).status_code == 400
    assert views == []


def test_when_every_calendar_failed_it_is_an_error_never_a_free_week(client, monkeypatch, views):
    from agent_friday.services import calendar_views as cv
    monkeypatch.setattr(cv, "cached_calendars", lambda fresh_for=600: {"calendars": [
        {"key": "home|primary", "on": True}, {"key": "home|fam", "on": True}], "accounts": []})
    monkeypatch.setattr(cv, "events_in_range", lambda start, end, keys=None, calendars=None: {
        "events": [], "errors": [{"calendar_key": "home|primary", "name": "Home", "error": "x"},
                                 {"calendar_key": "home|fam", "name": "Family", "error": "x"}],
        "calendars": [{"key": "home|primary", "on": True}, {"key": "home|fam", "on": True}]})
    r = client.get("/api/calendar/freebusy?start=2026-10-18&end=2026-10-25")
    assert r.status_code == 502 and "free_minutes" not in r.get_json()


def test_with_no_calendar_switched_on_there_is_no_free_week(client, monkeypatch, views):
    from agent_friday.services import calendar_views as cv
    monkeypatch.setattr(cv, "events_in_range", lambda start, end, keys=None, calendars=None: {
        "events": [], "errors": [], "calendars": [{"key": "home|primary", "on": False}]})
    assert client.get("/api/calendar/freebusy?start=2026-10-18&end=2026-10-25").status_code == 409


def test_some_calendars_unread_marks_the_free_time_partial(client, monkeypatch, views):
    from agent_friday.services import calendar_views as cv
    monkeypatch.setattr(cv, "events_in_range", lambda start, end, keys=None, calendars=None: {
        "events": [{"title": "x", "start_time": "2026-10-19T10:00:00-05:00", "end_time": "2026-10-19T11:00:00-05:00",
                    "calendar_name": "Home"}],
        "errors": [{"calendar_key": "home|fam", "name": "Family", "error": "x"}],
        "calendars": [{"key": "home|primary", "on": True}, {"key": "home|fam", "on": True}]})
    d = client.get("/api/calendar/freebusy?start=2026-10-18&end=2026-10-25").get_json()
    assert d["partial"] is True and d["errors"][0]["name"] == "Family"


def test_an_event_kept_on_this_pc_takes_its_time_too(client, monkeypatch, views):
    import agent_friday.routes.calendar as rc
    monkeypatch.setattr(rc, "_load_local_events", lambda: [
        {"id": "local-1", "title": "Kept here", "start_time": "2026-10-20T13:00", "end_time": "2026-10-20T14:00"}])
    d = client.get("/api/calendar/freebusy?start=2026-10-18&end=2026-10-25").get_json()
    tue = [x for x in d["days"] if x["date"] == "2026-10-20"][0]
    assert any("On this PC" in b["calendars"] for b in tue["busy"])
    assert "Kept here" not in str(d)

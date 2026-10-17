"""The Week and Month views read a date range in one request, every switched-on calendar.

GET /api/calendar/range answers with the events of [start, end) in their Google
colours, plus the calendars that could not be read; GET /api/calendar/calendars
lists every calendar with whether it is on; POST /api/calendar/calendars/visible
switches calendars in Friday only. A bad range is refused, never read as empty.
Synthetic data only.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def views(monkeypatch, tmp_path):
    from agent_friday.services import calendar_engine as ce
    from agent_friday.services import calendar_views as cv
    cals = {"calendars": [
        {"key": "home|primary", "account_id": "home", "calendar_id": "primary", "name": "Home",
         "color": "#7986cb", "primary": True, "on": True, "access_role": "owner", "default_reminders": []},
        {"key": "home|fam", "account_id": "home", "calendar_id": "fam", "name": "Family",
         "color": "#e67c73", "primary": False, "on": True, "access_role": "writer", "default_reminders": []},
    ], "accounts": [{"account_id": "home", "label": "Home", "can_list": True, "reason": ""}]}
    events = [
        {"id": "e1", "title": "School play", "start_time": "2026-10-21T15:00:00-05:00",
         "end_time": "2026-10-21T16:00:00-05:00", "all_day": False, "attendees": [], "color": "#e67c73",
         "calendar_key": "home|fam", "calendar_id": "fam", "account_id": "home", "source": "google"},
    ]
    asked = []
    monkeypatch.setattr(cv, "multi_calendar_available", lambda: True)
    monkeypatch.setattr(cv, "cached_calendars", lambda fresh_for=600: cals)

    def fake_range(start, end, keys=None, calendars=None):
        asked.append((start, end))
        return {"events": [dict(e) for e in events], "errors": [{"calendar_key": "home|work", "name": "Work",
                                                                  "error": "Couldn't read Work (HttpError)."}],
                "calendars": cals["calendars"]}
    monkeypatch.setattr(cv, "events_in_range", fake_range)
    monkeypatch.setattr(cv, "VISIBLE_FILE", tmp_path / "visible.json")
    monkeypatch.setattr(cv, "CALENDAR_DIR", tmp_path)
    monkeypatch.setattr(ce, "_load_local_events", lambda: [])
    return asked


def test_a_week_is_read_in_one_request_in_google_colours(client, views):
    r = client.get("/api/calendar/range?start=2026-10-18&end=2026-10-25")
    assert r.status_code == 200
    d = r.get_json()
    assert [e["title"] for e in d["events"]] == ["School play"]
    assert d["events"][0]["color"] == "#e67c73"
    assert d["errors"][0]["name"] == "Work", "a calendar that failed is named, not dropped"
    assert len(views) == 1


@pytest.mark.parametrize("q", ["start=2026-10-25&end=2026-10-18", "start=2026-10-01&end=2027-01-01",
                               "start=yesterday&end=2026-10-18", ""])
def test_a_bad_range_is_refused_not_read_as_empty(client, views, q):
    r = client.get("/api/calendar/range?" + q)
    assert r.status_code == 400
    assert views == []


def test_the_calendar_list_says_which_are_on(client, views):
    d = client.get("/api/calendar/calendars").get_json()
    assert [(c["name"], c["on"]) for c in d["calendars"]] == [("Home", True), ("Family", True)]


def _page():
    """The headers Friday's own page sends on a click: same-origin browser metadata and its session token."""
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", "X-Friday-Token": core._current_api_token()}


def test_switching_a_calendar_writes_friday_only(client, views, tmp_path):
    r = client.post("/api/calendar/calendars/visible", json={"changes": {"home|fam": False}}, headers=_page())
    assert r.status_code == 200 and r.get_json()["visible"] == {"home|fam": False}
    assert (tmp_path / "visible.json").is_file()
    assert client.post("/api/calendar/calendars/visible", json={"changes": {}}, headers=_page()).status_code == 400


def test_a_calendar_is_switched_only_by_the_owners_click_on_the_page(client, views, tmp_path):
    # A local program with no browser metadata passes check_auth without the token; it is not a click.
    body = {"changes": {"home|fam": False}}
    for headers in ({}, {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
                    {"X-Friday-Token": _page()["X-Friday-Token"]}):
        r = client.post("/api/calendar/calendars/visible", json=body, headers=headers)
        assert r.status_code == 403, (headers, r.get_json())
    assert not (tmp_path / "visible.json").exists(), "a refused switch changed the stored choice"


def test_only_a_calendar_in_her_list_is_switched(client, views, tmp_path):
    r = client.post("/api/calendar/calendars/visible", json={"changes": {"home|someone-else": True}}, headers=_page())
    assert r.status_code == 400
    assert not (tmp_path / "visible.json").exists()


def test_a_day_whose_read_failed_gets_no_free_gaps_and_no_note_written_from_nothing(client, monkeypatch):
    import agent_friday.routes.calendar as rc
    from agent_friday.services import calendar_engine as ce
    from agent_friday.services import calendar_views as cv
    monkeypatch.setattr(rc, "_events_for_day", lambda d: [])
    monkeypatch.setattr(ce, "range_failed", lambda s, e: True)
    monkeypatch.setattr(cv, "errors_for", lambda s, e: [])

    def no_note(*a, **k):
        raise AssertionError("a note was written from a failed read")
    monkeypatch.setattr(rc, "_day_annotation", no_note)
    d = client.get("/api/calendar/day/2026-10-20").get_json()
    assert d["read_failed"] is True and d["gaps"] == [] and d["annotation"].startswith("Couldn't read")

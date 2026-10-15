"""The Calendar workspace reads every calendar the owner has, in Google's colours.

It used to read one calendar (the primary calendar of the first account) and draw
every event in Friday's own two colours, so nothing on screen matched Google
Calendar and a second calendar (family, work, a shared one) was simply missing.

These tests drive the real read path (calendar_engine._fetch_calendar_range_live,
which the Day view, the week strip and the briefing all use) against a fake
Google client with two accounts, and check:
  * every switched-on calendar is read, and a hidden one is not;
  * an event wears its own Google colour, else its calendar's;
  * the reminder minutes Google would use come with each event (the event's own,
    else its calendar's defaults), and the owner's reply to an invitation;
  * an account whose token cannot read its calendar list still shows its main
    calendar, with the reason, and no new scope is asked for;
  * one failing calendar is reported, never turned into "no events".
Synthetic accounts and events only.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from agent_friday.services import calendar_engine as ce
from agent_friday.services import calendar_write as cw
from agent_friday.services import google_accounts as ga

FULL = "https://www.googleapis.com/auth/calendar"
EVENTS_ONLY = "https://www.googleapis.com/auth/calendar.events"
DAY = datetime(2026, 10, 20)


def _acct(aid, label, scopes):
    return {"id": aid, "label": label, "email": "%s@example.com" % aid, "status": "connected",
            "scopes": list(scopes), "services": {"calendar": True}, "color": "#2dd4bf"}


class _Exec:
    def __init__(self, v, boom=None):
        self.v, self.boom = v, boom

    def execute(self):
        if self.boom:
            raise self.boom
        return self.v


class _Google:
    """One account's Calendar API: its calendar list and the events on each calendar."""

    def __init__(self, calendars, events, fail=()):
        self.calendars, self.events_by_cal, self.fail = calendars, events, set(fail)
        self.read = []
        # Google answers "primary" for the account's own calendar, whatever its id is
        self.primary = next((c["id"] for c in calendars if c.get("primary")), "primary")

    def calendarList(self):
        return self

    def events(self):
        return _Events(self)

    def list(self, **kw):           # calendarList().list
        return _Exec({"items": self.calendars})


class _Events:
    def __init__(self, g):
        self.g = g

    def list(self, **kw):
        cid = self.g.primary if kw["calendarId"] == "primary" else kw["calendarId"]
        self.g.read.append(cid)
        if cid in self.g.fail:
            return _Exec(None, boom=RuntimeError("HttpError 500"))
        return _Exec({"items": self.g.events_by_cal.get(cid, [])})


def _ev(eid, title, hour, **extra):
    s = DAY.replace(hour=hour).astimezone()
    out = {"id": eid, "summary": title, "start": {"dateTime": s.isoformat()},
           "end": {"dateTime": (s + timedelta(hours=1)).isoformat()}}
    out.update(extra)
    return out


@pytest.fixture
def world(monkeypatch, tmp_path):
    from agent_friday.services import swr_cache
    try:
        from agent_friday.services import calendar_views as cv
    except ImportError:         # the code before this change: the tests below fail on what they read, not here
        cv = None
    if cv is not None:
        monkeypatch.setattr(cv, "VISIBLE_FILE", tmp_path / "visible.json")
        monkeypatch.setattr(cv, "CALENDAR_DIR", tmp_path)
    swr_cache.invalidate("calendar.")
    home = _Google(
        calendars=[
            {"id": "me@home.example", "primary": True, "summary": "me@home.example", "backgroundColor": "#7986cb",
             "foregroundColor": "#000000", "accessRole": "owner", "selected": True,
             "defaultReminders": [{"method": "popup", "minutes": 30}]},
            {"id": "family123@group.calendar.google.com", "summary": "Family", "backgroundColor": "#e67c73",
             "accessRole": "writer", "selected": True, "defaultReminders": []},
            {"id": "holidays@group.v.calendar.google.com", "summary": "Holidays", "backgroundColor": "#0b8043",
             "accessRole": "reader", "selected": False},
        ],
        events={
            "me@home.example": [
                _ev("a1", "Dentist", 9),
                _ev("a2", "Book club", 18, colorId="5", reminders={"useDefault": False,
                                                                   "overrides": [{"method": "popup", "minutes": 10}]}),
                _ev("a3", "Lunch with Dana", 12, attendees=[{"email": "dana@example.com"},
                                                            {"email": "me@home.example", "self": True,
                                                             "responseStatus": "needsAction"}]),
            ],
            "family123@group.calendar.google.com": [_ev("f1", "School play", 15)],
            "holidays@group.v.calendar.google.com": [_ev("h1", "A holiday", 0)],
        })
    old = _Google(calendars=[], events={"primary": [_ev("o1", "Standup", 10)]})
    accts = [_acct("home", "Home", [FULL]), _acct("old", "Old", [EVENTS_ONLY])]
    monkeypatch.setattr(ga, "has_accounts", lambda: True)
    monkeypatch.setattr(ga, "_accounts_with", lambda service: list(accts))
    services = {"home": home, "old": old}
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (services[account_id], None))
    # the legacy read, for the code before this change: the first account's primary calendar only
    import googleapiclient.discovery as discovery
    monkeypatch.setattr(ce, "_google_credentials", lambda: object())
    monkeypatch.setattr(discovery, "build", lambda *a, **k: _LegacyPrimary(home))
    return {"home": home, "old": old, "accts": accts}


class _LegacyPrimary:
    def __init__(self, g):
        self.g = g

    def events(self):
        return self

    def list(self, **kw):
        return _Exec({"items": self.g.events_by_cal["me@home.example"]})


def _read():
    return ce._fetch_calendar_range_live(DAY, DAY + timedelta(days=1))


def test_every_switched_on_calendar_of_every_account_is_read_and_a_hidden_one_is_not(world):
    titles = {e["title"] for e in _read()}
    assert {"Dentist", "Book club", "Lunch with Dana", "School play", "Standup"} <= titles
    assert "A holiday" not in titles, "a calendar unticked in Google stays off until the owner switches it on"
    assert "holidays@group.v.calendar.google.com" not in world["home"].read


def test_an_event_wears_its_own_google_colour_else_its_calendars(world):
    by = {e["title"]: e for e in _read()}
    assert by["Book club"]["color"] == "#fbd75b"        # colorId 5
    assert by["Dentist"]["color"] == "#7986cb"          # the primary calendar's colour
    assert by["School play"]["color"] == "#e67c73"      # the Family calendar's colour
    assert by["School play"]["calendar_name"] == "Family"
    assert by["School play"]["calendar_id"] == "family123@group.calendar.google.com"


def test_each_event_carries_the_reminders_google_would_use_and_the_owners_reply(world):
    by = {e["title"]: e for e in _read()}
    assert by["Book club"]["reminders"] == [{"method": "popup", "minutes": 10}]
    assert by["Book club"]["reminders_source"] == "event"
    assert by["Dentist"]["reminders"] == [{"method": "popup", "minutes": 30}]
    assert by["Dentist"]["reminders_source"] == "calendar"
    assert by["Lunch with Dana"]["self_rsvp"] == "needsAction"
    assert by["Dentist"]["self_rsvp"] == ""


def test_an_account_that_cannot_read_its_calendar_list_shows_its_main_calendar_and_says_why(world):
    from agent_friday.services import calendar_views as cv
    listing = cv.list_calendars()
    old = [a for a in listing["accounts"] if a["account_id"] == "old"][0]
    assert old["can_list"] is False and "Reconnect Google" in old["reason"]
    assert [c["calendar_id"] for c in listing["calendars"] if c["account_id"] == "old"] == ["primary"]
    assert "Standup" in {e["title"] for e in _read()}


def test_the_owner_switches_a_calendar_on_and_off_in_friday_only(world):
    from agent_friday.services import calendar_views as cv
    cv.set_visible({cv.key_of("home", "holidays@group.v.calendar.google.com"): True,
                    cv.key_of("home", "family123@group.calendar.google.com"): False})
    titles = {e["title"] for e in _read()}
    assert "A holiday" in titles and "School play" not in titles
    with pytest.raises(ValueError):
        cv.set_visible({"not a key": True})
    with pytest.raises(ValueError):
        cv.set_visible({})


def test_one_failing_calendar_is_reported_and_the_rest_still_show(world):
    from agent_friday.services import calendar_views as cv
    world["home"].fail.add("family123@group.calendar.google.com")
    got = cv.events_in_range(DAY, DAY + timedelta(days=1))
    assert [e["name"] for e in got["errors"]] == ["Family"]
    assert "Dentist" in {e["title"] for e in got["events"]}


def test_when_every_calendar_fails_the_read_raises_instead_of_saying_empty(world):
    world["home"].fail.update({"me@home.example", "family123@group.calendar.google.com"})
    world["old"].fail.add("primary")
    with pytest.raises(RuntimeError):
        _read()


def test_no_new_google_scope_is_requested():
    """The views need nothing the Settings connection does not already ask for."""
    assert ce.GOOGLE_SCOPES == ["https://www.googleapis.com/auth/gmail.readonly",
                                "https://www.googleapis.com/auth/calendar.events"]
    assert FULL in ga.GOOGLE_MULTI_SCOPES
    assert not any("calendarlist" in s or "freebusy" in s for s in ga.GOOGLE_MULTI_SCOPES)


def test_a_day_whose_read_failed_is_marked_so_the_page_never_draws_it_as_free(world):
    world["home"].fail.update({"me@home.example", "family123@group.calendar.google.com"})
    world["old"].fail.add("primary")
    assert ce._fetch_calendar_range(DAY, DAY + timedelta(days=1)) == []
    assert ce.range_failed(DAY, DAY + timedelta(days=1)) is True
    world["home"].fail.clear()
    world["old"].fail.clear()
    from agent_friday.services import swr_cache
    swr_cache.invalidate("calendar.")
    assert ce._fetch_calendar_range(DAY, DAY + timedelta(days=1))
    assert ce.range_failed(DAY, DAY + timedelta(days=1)) is False


def test_the_calendars_that_failed_are_kept_with_their_day_even_when_the_read_is_cached(world):
    from agent_friday.services import calendar_views as cv
    world["home"].fail.add("family123@group.calendar.google.com")
    ce._fetch_calendar_range(DAY, DAY + timedelta(days=1))
    cv.events_in_range(DAY + timedelta(days=3), DAY + timedelta(days=4))     # another range read in between
    assert [e["name"] for e in cv.errors_for(DAY, DAY + timedelta(days=1))] == ["Family"]


def test_on_someone_elses_calendar_googles_self_is_not_the_owner_and_her_own_copy_wins():
    from agent_friday.services import calendar_views as cv
    mine = {"key": "home|primary", "primary": True, "account_email": "me@home.example", "access_role": "owner",
            "calendar_id": "primary", "name": "Home"}
    shared = {"key": "home|col", "primary": False, "account_email": "me@home.example", "access_role": "writer",
              "calendar_id": "colleague@work.example", "name": "Colleague"}
    on_shared = _ev("inv", "Budget review", 14, organizer={"email": "boss@work.example"},
                    attendees=[{"email": "colleague@work.example", "self": True, "responseStatus": "accepted"},
                               {"email": "me@home.example", "responseStatus": "needsAction"}])
    on_mine = _ev("inv", "Budget review", 14, organizer={"email": "boss@work.example"},
                  attendees=[{"email": "colleague@work.example", "responseStatus": "accepted"},
                             {"email": "me@home.example", "self": True, "responseStatus": "needsAction"}])
    a, b = cv.normalize(on_shared, shared), cv.normalize(on_mine, mine)
    assert a["self_rsvp"] == "", "the colleague's reply is not hers to show or change"
    assert b["self_rsvp"] == "needsAction"
    by = {c["key"]: c for c in (mine, shared)}
    assert sorted([a, b], key=lambda e: cv._rank(e, by))[0]["calendar_key"] == "home|primary"

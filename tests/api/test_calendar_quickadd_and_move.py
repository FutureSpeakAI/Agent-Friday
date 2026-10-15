"""Quick add shows before it writes; a dragged event moves only after the owner confirms.

Before: POST /api/calendar/quick-add read the sentence with a model and wrote the
event at once (to Google, or to this PC), so the owner never saw what the model
had made of "lunch with Dana Tuesday". Now the route answers with a preview and
the calendars it can go on, and only POST /api/calendar/events with the owner's
confirmed Add writes, on the calendar the owner picked. A drag proposes; POST
/api/calendar/events/<id>/move with confirmed: true moves the event on its own
calendar and answers with a receipt whose Undo puts it back.
Synthetic accounts and events only.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from agent_friday.services import calendar_engine as ce
from agent_friday.services import calendar_write as cw


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


class _Events:
    def __init__(self, store, log):
        self.store, self.log = store, log

    def get(self, calendarId, eventId):
        self.log.append(("get", calendarId, eventId))
        return _Exec(dict(self.store[(calendarId, eventId)]))

    def patch(self, calendarId, eventId, body, **kw):
        self.log.append(("patch", calendarId, eventId, body))
        ev = self.store[(calendarId, eventId)]
        ev.update(body)
        return _Exec(dict(ev))

    def insert(self, calendarId, body, **kw):
        self.log.append(("insert", calendarId, body))
        return _Exec({"id": "new1", "summary": body.get("summary"), "htmlLink": ""})


class _Svc:
    def __init__(self, store, log):
        self.ev = _Events(store, log)

    def events(self):
        return self.ev


START = (datetime.now() + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0).astimezone()


@pytest.fixture
def google(monkeypatch, tmp_path):
    from agent_friday.services import google_accounts as ga
    log = []
    store = {("fam", "e1"): {"id": "e1", "summary": "School play",
                             "start": {"dateTime": START.isoformat(), "timeZone": "America/Chicago"},
                             "end": {"dateTime": (START + timedelta(hours=1)).isoformat()},
                             "attendees": [{"email": "dana@example.com"}]}}
    acct = {"id": "home", "label": "Home", "email": "home@example.com", "status": "connected",
            "scopes": ["https://www.googleapis.com/auth/calendar"], "services": {"calendar": True}}
    monkeypatch.setattr(ga, "list_accounts", lambda: [acct])
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (_Svc(store, log), None))
    try:
        from agent_friday.services import calendar_views as cv
        cals = {"calendars": [
            {"key": "home|primary", "account_id": "home", "calendar_id": "primary", "name": "Home",
             "access_role": "owner", "primary": True, "on": True, "color": "#7986cb"},
            {"key": "home|fam", "account_id": "home", "calendar_id": "fam", "name": "Family",
             "access_role": "writer", "primary": False, "on": True, "color": "#e67c73"},
            {"key": "home|hol", "account_id": "home", "calendar_id": "hol", "name": "Holidays",
             "access_role": "reader", "primary": False, "on": False, "color": "#0b8043"}],
            "accounts": []}
        monkeypatch.setattr(cv, "cached_calendars", lambda fresh_for=600: cals)
    except ImportError:
        pass
    local = []
    monkeypatch.setattr(ce, "_load_local_events", lambda: list(local))
    monkeypatch.setattr(ce, "_save_local_events", lambda evs: local.extend(evs[len(local):]) or evs)
    import agent_friday.routes.calendar as rc
    monkeypatch.setattr(rc, "_load_local_events", lambda: list(local))
    monkeypatch.setattr(rc, "_save_local_events", lambda evs: local.extend(evs[len(local):]) or evs)
    return {"log": log, "store": store, "local": local}


def test_quick_add_answers_with_a_preview_and_writes_nothing(client, google):
    r = client.post("/api/calendar/quick-add", json={"text": "lunch with Dana Tuesday 12pm"})
    d = r.get_json()
    assert r.status_code == 200 and d["status"] == "ok", d
    assert d["preview"]["title"] == "Lunch with Dana"
    assert datetime.fromisoformat(d["preview"]["start_time"]).hour == 12
    assert [c["name"] for c in d["calendars"]] == ["Home", "Family"], "a calendar she can only read is not offered"
    assert [x for x in google["log"] if x[0] in ("insert", "patch")] == [], "the preview wrote to Google"
    assert google["local"] == [], "the preview kept an event on this PC"


def test_an_event_is_added_only_from_the_owners_add_on_the_calendar_she_picked(client, google):
    body = {"title": "Lunch with Dana", "start_time": START.isoformat(), "end_time": (START + timedelta(hours=1)).isoformat(),
            "calendar_key": "home|fam"}
    assert client.post("/api/calendar/events", json=body).status_code == 400
    assert google["log"] == []
    r = client.post("/api/calendar/events", json=dict(body, confirmed=True))
    assert r.status_code == 200 and r.get_json()["created_in_google"] is True
    inserts = [x for x in google["log"] if x[0] == "insert"]
    assert len(inserts) == 1 and inserts[0][1] == "fam" and inserts[0][2]["summary"] == "Lunch with Dana"
    assert client.post("/api/calendar/events", json=dict(body, confirmed=True, calendar_key="home|nope")).status_code == 400


def test_a_drag_moves_nothing_until_confirmed_then_moves_on_its_own_calendar_and_undo_puts_it_back(client, google):
    new_start = START + timedelta(days=1, minutes=90)
    body = {"calendar_key": "home|fam", "start_time": new_start.isoformat(),
            "end_time": (new_start + timedelta(hours=1)).isoformat()}
    assert client.post("/api/calendar/events/e1/move", json=body).status_code == 400
    assert [x for x in google["log"] if x[0] == "patch"] == []
    r = client.post("/api/calendar/events/e1/move", json=dict(body, confirmed=True))
    d = r.get_json()
    assert r.status_code == 200 and d["status"] == "ok", d
    patches = [x for x in google["log"] if x[0] == "patch"]
    assert len(patches) == 1 and patches[0][1] == "fam"
    assert datetime.fromisoformat(google["store"][("fam", "e1")]["start"]["dateTime"]) == new_start
    assert google["store"][("fam", "e1")]["start"].get("timeZone") == "America/Chicago", "a move keeps the event's zone"
    u = client.post("/api/actions/receipts/%s/undo" % d["receipt_id"])
    assert u.status_code == 200, u.get_json()
    assert datetime.fromisoformat(google["store"][("fam", "e1")]["start"]["dateTime"]) == START
    assert [x[1] for x in google["log"] if x[0] == "patch"] == ["fam", "fam"]
    assert google["store"][("fam", "e1")]["start"].get("timeZone") == "America/Chicago", "and so does its Undo"


@pytest.mark.parametrize("start,end", [
    ("2026-10-21T10:00:00", "2026-10-21T11:00:00"),            # no time zone
    ("2026-10-21T11:00:00-05:00", "2026-10-21T10:00:00-05:00"),  # ends before it starts
    ("tomorrow", "later"),
])
def test_a_move_that_cannot_be_meant_is_refused_and_changes_nothing(client, google, start, end):
    r = client.post("/api/calendar/events/e1/move",
                    json={"calendar_key": "home|fam", "start_time": start, "end_time": end, "confirmed": True})
    assert r.status_code == 409
    assert [x for x in google["log"] if x[0] == "patch"] == []


def test_a_write_names_its_calendar_and_a_bad_calendar_id_is_refused_not_redirected(google):
    out = cw.update_event("e1", title="Play", calendar_id="fam", account_id="home")
    assert out.get("ok") and out["calendar_id"] == "fam"
    bad = cw.update_event("e1", title="Play", calendar_id="a/b", account_id="home")
    assert "error" in bad
    assert [x[1] for x in google["log"] if x[0] == "patch"] == ["fam"]


def test_an_event_is_not_added_to_a_calendar_she_can_only_read(client, google):
    body = {"title": "Lunch", "start_time": START.isoformat(), "calendar_key": "home|hol", "confirmed": True}
    assert client.post("/api/calendar/events", json=body).status_code == 400
    assert [x for x in google["log"] if x[0] == "insert"] == []


def test_when_google_can_no_longer_be_written_an_event_meant_for_it_is_not_kept_on_this_pc(client, google, monkeypatch):
    monkeypatch.setattr(cw, "write_ready", lambda: (False, "read-only"))
    body = {"title": "Lunch", "start_time": START.isoformat(), "calendar_key": "home|fam", "confirmed": True}
    r = client.post("/api/calendar/events", json=body)
    assert r.status_code == 409 and "Nothing was added" in r.get_json()["message"]
    assert google["local"] == [] and [x for x in google["log"] if x[0] == "insert"] == []


def test_a_time_given_in_utc_is_read_as_the_same_moment_here(client, google):
    import agent_friday.routes.calendar as rc
    utc = START.astimezone().astimezone(__import__("datetime").timezone.utc)
    assert rc._local_dt(utc.isoformat().replace("+00:00", "Z")) == START.replace(tzinfo=None)

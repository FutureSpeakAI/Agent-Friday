"""Replying to an invitation from the Calendar: only after the owner confirms, only her own guest row.

POST /api/calendar/events/<id>/rsvp with confirmed: true changes the attendee Google marks `self` to
going, maybe or not going, on the event's own calendar, and asks Google to tell the organiser. Without
confirmed, on an event she organises, or on one with no invitation for her, nothing is sent.
Synthetic accounts and events only.
"""
from __future__ import annotations

import pytest

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
        return _Exec({k: (list(map(dict, v)) if isinstance(v, list) else v) for k, v in self.store[(calendarId, eventId)].items()})

    def patch(self, calendarId, eventId, body, **kw):
        # As Google does: with attendeesOmitted the rows sent are merged by address into the guest list;
        # without it the list sent replaces the whole guest list.
        self.log.append((calendarId, eventId, body, kw))
        ev = self.store[(calendarId, eventId)]
        body = dict(body)
        if body.pop("attendeesOmitted", False):
            sent = {a.get("email"): a for a in body.pop("attendees", [])}
            ev["attendees"] = [dict(a, **sent.get(a.get("email"), {})) for a in ev.get("attendees") or []]
        ev.update(body)
        return _Exec(dict(ev))


class _Svc:
    def __init__(self, store, log):
        self.e = _Events(store, log)

    def events(self):
        return self.e


@pytest.fixture
def google(monkeypatch):
    from agent_friday.services import calendar_views as cv
    from agent_friday.services import google_accounts as ga
    log = []
    store = {
        ("fam", "inv"): {"id": "inv", "summary": "Lunch with Dana", "organizer": {"email": "dana@example.com"},
                         "attendees": [{"email": "dana@example.com", "organizer": True, "responseStatus": "accepted"},
                                       {"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
                                       {"email": "sam@example.com", "responseStatus": "tentative"}]},
        ("fam", "mine"): {"id": "mine", "summary": "Book club", "organizer": {"self": True, "email": "me@example.com"},
                          "attendees": [{"email": "me@example.com", "self": True, "organizer": True, "responseStatus": "accepted"}]},
        ("fam", "solo"): {"id": "solo", "summary": "Gym"},
    }
    acct = {"id": "home", "label": "Home", "email": "me@example.com", "status": "connected",
            "scopes": ["https://www.googleapis.com/auth/calendar"], "services": {"calendar": True}}
    monkeypatch.setattr(ga, "list_accounts", lambda: [acct])
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (_Svc(store, log), None))
    monkeypatch.setattr(cv, "cached_calendars", lambda fresh_for=600: {"calendars": [
        {"key": "home|fam", "account_id": "home", "calendar_id": "fam", "name": "Family", "access_role": "writer"}], "accounts": []})
    return store, log


def _page():
    """The headers Friday's own page sends on a click: same-origin browser metadata and its session token."""
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", "X-Friday-Token": core._current_api_token()}


def test_nothing_is_sent_without_the_owners_confirmation(client, google):
    store, log = google
    r = client.post("/api/calendar/events/inv/rsvp", json={"calendar_key": "home|fam", "response": "accepted"}, headers=_page())
    assert r.status_code == 400 and log == []


def test_a_confirmed_reply_changes_only_her_own_row_and_google_tells_the_organiser(client, google):
    store, log = google
    r = client.post("/api/calendar/events/inv/rsvp", json={"calendar_key": "home|fam", "response": "accepted", "confirmed": True}, headers=_page())
    d = r.get_json()
    assert r.status_code == 200 and d["response"] == "accepted" and d["before"] == "needsAction", d
    assert len(log) == 1 and log[0][0] == "fam" and log[0][3].get("sendUpdates") == "all"
    # Only her own row goes to Google: the other guests' replies are never written back from the earlier read.
    assert log[0][2] == {"attendees": [{"email": "me@example.com", "self": True, "responseStatus": "accepted"}],
                         "attendeesOmitted": True}, log[0][2]
    rows = {a["email"]: a["responseStatus"] for a in store[("fam", "inv")]["attendees"]}
    assert rows == {"dana@example.com": "accepted", "me@example.com": "accepted", "sam@example.com": "tentative"}


@pytest.mark.parametrize("eid,response", [("mine", "declined"), ("solo", "accepted"), ("inv", "maybe later")])
def test_no_reply_where_there_is_no_invitation_for_her_or_no_such_answer(client, google, eid, response):
    store, log = google
    r = client.post("/api/calendar/events/%s/rsvp" % eid, json={"calendar_key": "home|fam", "response": response, "confirmed": True}, headers=_page())
    assert r.status_code == 409 and log == []


def test_the_same_reply_twice_sends_nothing_the_second_time(client, google):
    store, log = google
    body = {"calendar_key": "home|fam", "response": "tentative", "confirmed": True}
    client.post("/api/calendar/events/inv/rsvp", json=body, headers=_page())
    r = client.post("/api/calendar/events/inv/rsvp", json=body, headers=_page())
    assert r.get_json()["unchanged"] is True and len(log) == 1


def test_on_a_calendar_shared_with_her_the_row_google_marks_self_is_not_hers_to_answer(client, google):
    store, log = google
    store[("fam", "col")] = {"id": "col", "summary": "Team sync", "organizer": {"email": "boss@example.com"},
                             "attendees": [{"email": "colleague@example.com", "self": True, "responseStatus": "accepted"},
                                           {"email": "boss@example.com", "organizer": True, "responseStatus": "accepted"}]}
    r = client.post("/api/calendar/events/col/rsvp", json={"calendar_key": "home|fam", "response": "declined", "confirmed": True}, headers=_page())
    assert r.status_code == 409 and log == []
    assert store[("fam", "col")]["attendees"][0]["responseStatus"] == "accepted"


def test_a_program_without_the_owners_click_sends_no_reply(client, google):
    # check_auth lets a local caller with no browser metadata through without the token; `confirmed: true`
    # in its body is no click, so a prompt-injected command cannot decline a meeting.
    store, log = google
    body = {"calendar_key": "home|fam", "response": "declined", "confirmed": True}
    for headers in ({}, {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"},
                    {"X-Friday-Token": _page()["X-Friday-Token"]}):
        r = client.post("/api/calendar/events/inv/rsvp", json=body, headers=headers)
        assert r.status_code == 403, (headers, r.get_json())
    assert log == [], "Google was called for a request that was not the owner's click"
    assert store[("fam", "inv")]["attendees"][1]["responseStatus"] == "needsAction"

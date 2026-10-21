"""Every write the Calendar page sends to Google passes the action checkpoint first.

Delete already did; adding an event, answering an invitation, moving an event
and putting a moved event back called Google straight after the owner's click,
with no integrity check and no signed receipt. Now each one goes through
governance/action_gate.record_external before Google is called, and when the
checkpoint holds it the answer says nothing was done and Google is never called.
The undo route also takes only the owner's press in Friday's page.
Synthetic accounts and events only (the fake Google of test_calendar_quickadd_and_move).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import calendar_write as cw
from tests.api.test_calendar_quickadd_and_move import START, _page, google  # noqa: F401  (fixture)


@pytest.fixture
def checkpoint(monkeypatch, google):  # noqa: F811
    seen = {"calls": [], "hold": False}

    def record_external(action, *, surface, approval_id=None, target=""):
        # What Google had been asked when the checkpoint ran: it must run first.
        seen["calls"].append({"action": action, "surface": surface, "target": target,
                              "google_before": len([x for x in google["log"] if x[0] != "get"])})
        if seen["hold"]:
            raise ag.Held("the signed receipt could not be written")

    monkeypatch.setattr(ag, "record_external", record_external)
    return seen


def _add_body():
    return {"title": "Lunch", "start_time": START.isoformat(), "calendar_key": "home|fam", "confirmed": True}


def _move_body():
    new_start = START + timedelta(days=1)
    return {"calendar_key": "home|fam", "start_time": new_start.isoformat(),
            "end_time": (new_start + timedelta(hours=1)).isoformat(), "confirmed": True}


def test_adding_an_event_passes_the_checkpoint_before_google(client, google, checkpoint):  # noqa: F811
    r = client.post("/api/calendar/events", json=_add_body(), headers=_page())
    assert r.status_code == 200, r.get_json()
    calls = [c for c in checkpoint["calls"] if c["action"] == "calendar:create_event"]
    assert len(calls) == 1, checkpoint["calls"]
    assert calls[0]["surface"] == "calendar_workspace"
    assert calls[0]["google_before"] == 0
    assert any(x[0] == "insert" for x in google["log"])


def test_a_held_add_writes_nothing_and_says_so(client, google, checkpoint):  # noqa: F811
    checkpoint["hold"] = True
    r = client.post("/api/calendar/events", json=_add_body(), headers=_page())
    assert r.status_code == 403
    assert r.get_json()["message"].startswith("Nothing was added")
    assert not any(x[0] == "insert" for x in google["log"])
    assert google["local"] == []


def test_moving_an_event_passes_the_checkpoint_before_google(client, google, checkpoint):  # noqa: F811
    r = client.post("/api/calendar/events/e1/move", json=_move_body(), headers=_page())
    assert r.status_code == 200, r.get_json()
    calls = [c for c in checkpoint["calls"] if c["action"] == "calendar:move_event"]
    assert len(calls) == 1 and calls[0]["google_before"] == 0
    assert any(x[0] == "patch" for x in google["log"])


def test_a_held_move_moves_nothing(client, google, checkpoint):  # noqa: F811
    checkpoint["hold"] = True
    r = client.post("/api/calendar/events/e1/move", json=_move_body(), headers=_page())
    assert r.status_code == 403
    assert r.get_json()["message"].startswith("Nothing was moved")
    assert not any(x[0] == "patch" for x in google["log"])


@pytest.fixture
def replies(monkeypatch):
    sent = []

    def respond_to_event(event_id, response, account_id=None, calendar_id=None):
        sent.append((event_id, response, calendar_id))
        return {"response": response, "before": "needsAction"}

    monkeypatch.setattr(cw, "respond_to_event", respond_to_event)
    return sent


def test_answering_an_invitation_passes_the_checkpoint_first(client, google, checkpoint, replies):  # noqa: F811
    r = client.post("/api/calendar/events/e2/rsvp", json={"calendar_key": "home|fam", "response": "accepted",
                                                          "confirmed": True}, headers=_page())
    assert r.status_code == 200, r.get_json()
    assert [c["action"] for c in checkpoint["calls"]] == ["calendar:respond_to_event"]
    assert replies == [("e2", "accepted", "fam")]


def test_a_held_reply_is_not_sent(client, google, checkpoint, replies):  # noqa: F811
    checkpoint["hold"] = True
    r = client.post("/api/calendar/events/e2/rsvp", json={"calendar_key": "home|fam", "response": "declined",
                                                          "confirmed": True}, headers=_page())
    assert r.status_code == 403
    assert r.get_json()["message"].startswith("No reply was sent")
    assert replies == []


def test_putting_a_move_back_needs_the_owners_press_and_passes_the_checkpoint(client, google, checkpoint):  # noqa: F811
    r = client.post("/api/calendar/events/e1/move", json=_move_body(), headers=_page())
    rid = r.get_json()["receipt_id"]
    patches = len([x for x in google["log"] if x[0] == "patch"])
    for headers in ({}, {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"}):
        u = client.post("/api/actions/receipts/%s/undo" % rid, headers=headers)
        assert u.status_code == 403, headers
    assert len([x for x in google["log"] if x[0] == "patch"]) == patches, "an undo without a press reached Google"
    u = client.post("/api/actions/receipts/%s/undo" % rid, headers=_page())
    assert u.status_code == 200, u.get_json()
    assert "calendar:undo" in [c["action"] for c in checkpoint["calls"]]
    assert len([x for x in google["log"] if x[0] == "patch"]) == patches + 1

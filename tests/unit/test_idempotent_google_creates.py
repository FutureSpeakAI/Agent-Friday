"""F3 (BD-06, NS-34.11-2): a Google create the model repeats in the same turn is made once.

A retry after an unconfirmed reply (a timeout, a 5xx, a dropped connection) used to make a second
event or task. Now a calendar create carries an event id derived from the turn, the tool and its
arguments, so Google refuses the copy (409) and Friday reports the event that exists, and a lost
reply is looked up by that id. A Google Tasks create remembers what it made in this turn, and a
lost reply is looked for in the list (one task with that title, just changed) before anyone is
told to check. A new turn is a new request. Google is a stateful fake; synthetic ids only.
"""
from __future__ import annotations

import itertools
import json

import pytest

from agent_friday.services import action_journal as journal
from agent_friday.services import agent

_ids = itertools.count(1)


class HttpError(Exception):
    def __init__(self, status):
        super().__init__("HttpError %s" % status)
        self.resp = type("R", (), {"status": status})()


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, *a, **k):
        return self.fn()


class FakeCalendar:
    """Event ids are unique, as Google's are: a second insert with the same id is a 409."""

    def __init__(self):
        self.store, self.lose_reply, self.inserted = {}, False, []

    def events(self):
        return self

    def insert(self, calendarId, body, **kw):
        def run():
            eid = body.get("id") or "gen%d" % next(_ids)
            if eid in self.store:
                raise HttpError(409)
            self.store[eid] = dict(body, id=eid)
            self.inserted.append(dict(body))
            if self.lose_reply:
                raise HttpError(503)       # applied, but the reply never came back
            return dict(self.store[eid])
        return _Exec(run)

    def get(self, calendarId, eventId):
        def run():
            if eventId not in self.store:
                raise HttpError(404)
            return dict(self.store[eventId])
        return _Exec(run)


class FakeTasks:
    def __init__(self):
        self.store, self.lose_reply = {}, False

    def tasks(self):
        return self

    def insert(self, tasklist, body):
        def run():
            tid = "t%d" % next(_ids)
            self.store[tid] = dict({"status": "needsAction"}, **body, id=tid)
            if self.lose_reply:
                raise TimeoutError("read timed out")
            return dict(self.store[tid])
        return _Exec(run)

    def get(self, tasklist, task):
        def run():
            if task not in self.store:
                raise HttpError(404)
            return dict(self.store[task])
        return _Exec(run)

    def list(self, tasklist, **kw):
        return _Exec(lambda: {"items": [dict(t) for t in self.store.values()]})


@pytest.fixture
def google(tmp_path, monkeypatch):
    from agent_friday.services import calendar_write as cw, google_accounts as ga, turn_plan
    import googleapiclient.discovery as discovery
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    cal, tasks, box = FakeCalendar(), FakeTasks(), {"turn": "turn-1"}
    monkeypatch.setattr(turn_plan, "current_turn_id", lambda: box["turn"])
    monkeypatch.setattr(cw, "write_ready", lambda: (True, ""))
    monkeypatch.setattr(cw, "resolve_write_account", lambda aid=None: ("acct_c1", None))
    monkeypatch.setattr(cw, "_service", lambda aid=None: (cal, None))
    monkeypatch.setattr(cw, "_gate_calendar_field", lambda text, field: (text, ""))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda *a, **k: None)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    monkeypatch.setattr(ga, "tasks_writable", lambda aid: True)
    monkeypatch.setattr(ga, "_gate_task_text", lambda body: (dict(body), ""))
    monkeypatch.setattr(ga.cs, "audit_event", lambda *a, **k: None)
    monkeypatch.setattr(ga, "_RECENT_CREATES", {}, raising=False)
    monkeypatch.setattr(discovery, "build", lambda *a, **k: tasks)
    yield cal, tasks, box
    journal.reset()


EVENT = {"title": "Lunch", "start": "2026-11-03T12:00:00+00:00", "end": "2026-11-03T13:00:00+00:00"}
TASK = {"account_id": "acct_t1", "title": "Call the bank", "tasklist_id": "tl1"}


def _res(text):
    return json.JSONDecoder().raw_decode(text[text.index("{"):])[0]


def test_the_same_event_create_twice_in_a_turn_is_one_event(google):
    cal, _, _ = google
    first = _res(agent._tool_create_calendar_event(dict(EVENT)))
    second = _res(agent._tool_create_calendar_event(dict(EVENT)))
    assert len(cal.store) == 1, "the retry made a second event"
    assert first["id"] == second["id"] and second.get("already_made")


def test_an_event_whose_reply_was_lost_is_found_by_its_id_not_reported_unconfirmed(google):
    cal, _, _ = google
    cal.lose_reply = True
    res = _res(agent._tool_create_calendar_event(dict(EVENT)))
    assert res.get("ok") is True and res["id"] in cal.store and len(cal.store) == 1


def test_a_new_turn_is_a_new_request(google):
    cal, _, box = google
    agent._tool_create_calendar_event(dict(EVENT))
    box["turn"] = "turn-2"
    agent._tool_create_calendar_event(dict(EVENT))
    assert len(cal.store) == 2


def test_without_a_turn_or_task_no_id_is_chosen(google):
    cal, _, box = google
    box["turn"] = None
    from agent_friday.services import connector_outcome as co
    assert co.idem_key("create_calendar_event", EVENT) is None
    agent._tool_create_calendar_event(dict(EVENT))
    assert "id" not in cal.inserted[-1], "an owner's own page keeps Google's own ids"


def test_holds_placed_twice_in_a_turn_are_placed_once_in_one_series(google):
    cal, _, _ = google
    slots = {"title": "Planning", "slots": [
        {"start": "2026-11-02T10:00:00+00:00", "end": "2026-11-02T11:00:00+00:00"},
        {"start": "2026-11-03T10:00:00+00:00", "end": "2026-11-03T11:00:00+00:00"}]}
    first = _res(agent._tool_hold_slots(dict(slots)))
    second = _res(agent._tool_hold_slots(dict(slots)))
    assert len(cal.store) == 2 and first["series_id"] == second["series_id"]
    assert [h["id"] for h in first["holds"]] == [h["id"] for h in second["holds"]]


def test_the_same_task_create_twice_in_a_turn_is_one_task(google):
    _, tasks, _ = google
    first = _res(agent._tool_create_task(dict(TASK)))
    second = _res(agent._tool_create_task(dict(TASK)))
    assert len(tasks.store) == 1 and first["id"] == second["id"] and second.get("already_made")


def test_a_task_whose_reply_was_lost_is_found_in_the_list(google):
    _, tasks, _ = google
    tasks.lose_reply = True
    res = _res(agent._tool_create_task(dict(TASK)))
    assert res.get("id") in tasks.store and not res.get("error") and len(tasks.store) == 1


def test_a_task_the_owner_made_by_hand_is_never_adopted(google):
    _, tasks, _ = google
    tasks.store["mine"] = {"id": "mine", "title": "Call the bank", "notes": "about the card",
                           "status": "needsAction"}
    tasks.lose_reply = True
    lost = dict(TASK, notes="")
    res = _res(agent._tool_create_task(lost))
    assert res.get("id") != "mine", "an owner's own task with other notes is not Friday's"


def test_without_a_key_a_lost_task_is_not_looked_for(google):
    _, tasks, box = google
    box["turn"] = None
    tasks.lose_reply = True
    res = _res(agent._tool_create_task(dict(TASK)))
    assert res.get("error") and "did not confirm" in res["error"]


def test_two_tasks_with_that_title_cannot_be_told_apart_so_the_owner_is_asked(google):
    _, tasks, _ = google
    tasks.store["old"] = {"id": "old", "title": "Call the bank", "status": "needsAction"}
    tasks.lose_reply = True
    res = _res(agent._tool_create_task(dict(TASK)))
    assert res.get("error") and "did not confirm" in res["error"]

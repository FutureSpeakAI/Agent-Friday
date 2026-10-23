"""F2 (BD-06): Friday's own Google Calendar and Google Tasks changes leave a receipt, and "undo that"
puts each one back, on one approval card (putting back writes to Google too).

Before: create/update/delete of an event and every Google Tasks change left no receipt in the
action journal, so Undo had nothing to put back. Google is a stateful fake here; no network.
Synthetic ids only.
"""
from __future__ import annotations

import itertools
import json

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import agent, approvals as ap, desktop_bus, item_actions as ia

_ids = itertools.count(1)


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, *a, **k):
        return self.fn()


class FakeCalendar:
    def __init__(self):
        self.store = {"e1": {"id": "e1", "summary": "Dentist", "location": "Main St",
                             "description": "Bring the form. " * 40,
                             "start": {"dateTime": "2026-11-02T10:00:00+00:00"},
                             "end": {"dateTime": "2026-11-02T11:00:00+00:00"}},
                      "allday": {"id": "allday", "summary": "Holiday",
                                 "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}}}

    def events(self):
        return self

    def insert(self, calendarId, body, **kw):
        def run():
            eid = "new%d" % next(_ids)
            self.store[eid] = dict(body, id=eid)
            return dict(self.store[eid])
        return _Exec(run)

    def get(self, calendarId, eventId):
        def run():
            if eventId not in self.store:
                raise RuntimeError("not found")
            return dict(self.store[eventId])
        return _Exec(run)

    def patch(self, calendarId, eventId, body, **kw):
        def run():
            self.store[eventId].update(body)
            return dict(self.store[eventId])
        return _Exec(run)

    def delete(self, calendarId, eventId, **kw):
        def run():
            self.store.pop(eventId)
            return ""
        return _Exec(run)


class FakeTasks:
    def __init__(self):
        self.store = {"k1": {"id": "k1", "title": "Buy stamps", "notes": "the blue ones",
                             "status": "needsAction", "due": ""}}

    def tasks(self):
        return self

    def get(self, tasklist, task):
        def run():
            if task not in self.store:
                raise RuntimeError("not found")
            return dict(self.store[task])
        return _Exec(run)

    def insert(self, tasklist, body):
        def run():
            tid = "t%d" % next(_ids)
            self.store[tid] = dict({"status": "needsAction"}, **body, id=tid)
            return dict(self.store[tid])
        return _Exec(run)

    def patch(self, tasklist, task, body):
        def run():
            self.store[task].update(body)
            return dict(self.store[task])
        return _Exec(run)

    def delete(self, tasklist, task):
        def run():
            self.store.pop(task)
            return ""
        return _Exec(run)


@pytest.fixture
def world(tmp_path, monkeypatch):
    from agent_friday.services import calendar_write as cw, dissent_gate as dg, google_accounts as ga
    import googleapiclient.discovery as discovery
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    cal, tasks = FakeCalendar(), FakeTasks()
    monkeypatch.setattr(cw, "write_ready", lambda: (True, ""))
    monkeypatch.setattr(cw, "read_ready", lambda: (True, ""), raising=False)
    monkeypatch.setattr(cw, "resolve_write_account", lambda aid=None: ("acct_c1", None))
    monkeypatch.setattr(cw, "resolve_read_account", lambda aid=None: ("acct_c1", None), raising=False)
    monkeypatch.setattr(cw, "_service", lambda aid=None: (cal, None))
    monkeypatch.setattr(cw, "_gate_calendar_field", lambda text, field: (text, ""))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda *a, **k: None)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    monkeypatch.setattr(ga, "tasks_writable", lambda aid: True)
    monkeypatch.setattr(ga, "_gate_task_text", lambda body: (dict(body), ""))
    monkeypatch.setattr(ga.cs, "audit_event", lambda *a, **k: None)
    monkeypatch.setattr(discovery, "build", lambda *a, **k: tasks)
    yield cal, tasks
    journal.reset()
    desktop_bus.reset()


def _undo_on_card(receipt_id):
    """Ask for Undo, approve its card, wait for it to run. Returns the undo's receipt."""
    out = ia.undo(receipt_id)
    assert out["status"] == "pending_approval", out
    ap.decide_with_outcome(out["approval_id"], "approve", decided_by="owner")
    done = ia.wait_for(out["approval_id"], 10)
    assert done and done["status"] == "complete", done
    return done


def _rid(text):
    res, _end = json.JSONDecoder().raw_decode(text[text.index("{"):])   # a travel note may follow
    assert "undo_before" not in res, "the whole before-values stay in the receipt, not the model's view"
    return res["receipt_id"]


def test_an_event_friday_created_is_deleted_by_undo(world):
    cal, _ = world
    out = agent._tool_create_calendar_event({"title": "Lunch", "start": "2026-11-03T12:00:00+00:00",
                                             "end": "2026-11-03T13:00:00+00:00"})
    rid = _rid(out)
    created = [k for k in cal.store if k.startswith("new")]
    assert len(created) == 1
    assert ia.classify_undo({"receipt_id": rid})[0] == "outward"
    _undo_on_card(rid)
    assert not [k for k in cal.store if k.startswith("new")]
    assert journal.get(rid)["undone"] is True


def test_changed_event_fields_are_set_back_whole(world):
    cal, _ = world
    before = dict(cal.store["e1"])
    rid = _rid(agent._tool_update_calendar_event({"event_id": "e1", "title": "Dentist (moved)",
                                                  "location": "Elm St", "description": "short"}))
    assert cal.store["e1"]["location"] == "Elm St"
    _undo_on_card(rid)
    for k in ("summary", "location", "description"):
        assert cal.store["e1"][k] == before[k], k


def test_moved_event_times_are_set_back(world):
    cal, _ = world
    rid = _rid(agent._tool_update_calendar_event({"event_id": "e1", "start": "2026-11-04T10:00:00+00:00",
                                                  "end": "2026-11-04T11:00:00+00:00"}))
    _undo_on_card(rid)
    assert cal.store["e1"]["start"]["dateTime"] == "2026-11-02T10:00:00+00:00"
    assert cal.store["e1"]["end"]["dateTime"] == "2026-11-02T11:00:00+00:00"


def test_a_deleted_event_is_made_again_by_undo(world):
    cal, _ = world
    rid = _rid(agent._tool_delete_calendar_event({"event_id": "e1", "notify_guests": False}))
    assert "e1" not in cal.store
    _undo_on_card(rid)
    again = [e for e in cal.store.values() if e.get("summary") == "Dentist"]
    assert len(again) == 1 and again[0]["start"]["dateTime"] == "2026-11-02T10:00:00+00:00"
    assert again[0]["description"].startswith("Bring the form.") and len(again[0]["description"]) > 300


def test_an_all_day_move_says_it_cannot_be_undone(world):
    out = agent._tool_update_calendar_event({"event_id": "allday", "start": "2026-11-07T10:00:00+00:00",
                                             "end": "2026-11-07T11:00:00+00:00"})
    rec = journal.get(_rid(out))
    assert not rec["undo"] and "cannot be undone" in rec["summary"]


def test_a_task_friday_added_is_deleted_by_undo(world):
    _, tasks = world
    rid = _rid(agent._tool_create_task({"account_id": "acct_t1", "title": "Call the bank", "tasklist_id": "tl1"}))
    assert any(t["title"] == "Call the bank" for t in tasks.store.values())
    assert ia.classify_undo({"receipt_id": rid})[0] == "outward"
    _undo_on_card(rid)
    assert not any(t["title"] == "Call the bank" for t in tasks.store.values())


def test_a_completed_task_is_open_again_after_undo(world):
    _, tasks = world
    rid = _rid(agent._tool_complete_task({"account_id": "acct_t1", "tasklist_id": "tl1", "task_id": "k1"}))
    assert tasks.store["k1"]["status"] == "completed"
    _undo_on_card(rid)
    assert tasks.store["k1"]["status"] == "needsAction"


def test_changed_task_notes_are_set_back(world):
    _, tasks = world
    rid = _rid(agent._tool_update_task({"account_id": "acct_t1", "tasklist_id": "tl1", "task_id": "k1",
                                        "notes": "any colour"}))
    _undo_on_card(rid)
    assert tasks.store["k1"]["notes"] == "the blue ones" and tasks.store["k1"]["title"] == "Buy stamps"


def test_a_deleted_task_is_made_again_by_undo(world):
    _, tasks = world
    rid = _rid(agent._tool_delete_task({"account_id": "acct_t1", "tasklist_id": "tl1", "task_id": "k1"}))
    assert "k1" not in tasks.store
    _undo_on_card(rid)
    again = [t for t in tasks.store.values() if t["title"] == "Buy stamps"]
    assert len(again) == 1 and again[0]["notes"] == "the blue ones"


def test_undo_twice_puts_back_once(world):
    _, tasks = world
    rid = _rid(agent._tool_create_task({"account_id": "acct_t1", "title": "Once", "tasklist_id": "tl1"}))
    _undo_on_card(rid)
    assert ia.undo(rid)["status"] == "nothing"

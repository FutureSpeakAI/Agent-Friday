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
        self.patches = []

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
        self.patches.append(dict(body))

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
    monkeypatch.setattr(ga, "tasks_writable", lambda aid: True, raising=False)
    monkeypatch.setattr(ga, "_gate_task_text", lambda body: (dict(body), ""))
    monkeypatch.setattr(ga.cs, "audit_event", lambda *a, **k: None)
    monkeypatch.setattr(discovery, "build", lambda *a, **k: tasks)
    # Undo data is sealed as A20's sent text is (services/undo_store): a stand-in key store here,
    # and no vault passphrase (never derive a real key).
    from agent_friday.services import credential_store as cs, sent_text_store as sts
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data[::-1], "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):][::-1])
    monkeypatch.setattr(cs, "looks_protected", lambda blob: "keystore" if blob.startswith(b"SEALED:") else None)
    monkeypatch.setattr(sts, "_vault_key", lambda derive=True: None)
    monkeypatch.setattr(sts, "_passphrase_set", lambda: False)
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
    assert "{" in text, text
    res, _end = json.JSONDecoder().raw_decode(text[text.index("{"):])   # a travel note may follow
    assert "receipt_id" in res, "the change must leave a receipt Undo can find"
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


def test_the_card_says_undo_will_delete_what_friday_created(world):
    rid = _rid(agent._tool_create_calendar_event({"title": "Lunch", "start": "2026-11-03T12:00:00+00:00",
                                                  "end": "2026-11-03T13:00:00+00:00"}))
    out = ia.undo(rid)
    card = ap.get_approval(out["approval_id"])
    assert any("Delete" in line and "Lunch" in line for line in card["payload"]["lines"])
    assert "Delete" in out["readback"]


def test_an_event_that_gained_guests_is_not_deleted_by_undo(world):
    cal, _ = world
    rid = _rid(agent._tool_create_calendar_event({"title": "Lunch", "start": "2026-11-03T12:00:00+00:00",
                                                  "end": "2026-11-03T13:00:00+00:00"}))
    new = [k for k in cal.store if k.startswith("new")][0]
    cal.store[new]["attendees"] = [{"email": "guest@example.com"}]
    out = ia.undo(rid)
    ap.decide_with_outcome(out["approval_id"], "approve", decided_by="owner")
    done = ia.wait_for(out["approval_id"], 10)
    assert done and done["status"] == "failed" and new in cal.store, "guests would be cancelled on silently"


def test_an_event_created_with_guests_is_undone_and_they_are_told(world, monkeypatch):
    from agent_friday.services import calendar_write as cw
    cal, _ = world
    told = []
    real = cw.delete_event
    monkeypatch.setattr(cw, "delete_event", lambda eid, **k: told.append(k.get("notify_guests")) or real(eid, **k))
    rid = _rid(agent._tool_create_calendar_event({"title": "Team lunch", "start": "2026-11-03T12:00:00+00:00",
                                                  "end": "2026-11-03T13:00:00+00:00",
                                                  "attendees": ["ann@example.com"]}))
    _undo_on_card(rid)
    assert not [k for k in cal.store if k.startswith("new")] and told == [True]


def test_a_repeating_event_deleted_says_undo_cannot_make_it_again(world):
    cal, _ = world
    cal.store["rec1"] = {"id": "rec1", "summary": "Standup", "recurrence": ["RRULE:FREQ=DAILY"],
                         "start": {"dateTime": "2026-11-02T09:00:00+00:00"},
                         "end": {"dateTime": "2026-11-02T09:15:00+00:00"}}
    rec = journal.get(_rid(agent._tool_delete_calendar_event({"event_id": "rec1", "notify_guests": False})))
    assert not rec["undo"] and "repeating" in rec["summary"]


def test_a_due_date_friday_set_is_cleared_again_with_null(world):
    _, tasks = world
    rid = _rid(agent._tool_update_task({"account_id": "acct_t1", "tasklist_id": "tl1", "task_id": "k1",
                                        "due": "2026-11-09T00:00:00Z"}))
    _undo_on_card(rid)
    assert tasks.patches[-1].get("due", "missing") is None, "Google Tasks clears a due date with null"


def test_reopening_a_task_clears_its_completed_time(world):
    _, tasks = world
    rid = _rid(agent._tool_complete_task({"account_id": "acct_t1", "tasklist_id": "tl1", "task_id": "k1"}))
    _undo_on_card(rid)
    assert tasks.patches[-1].get("status") == "needsAction" and "completed" in tasks.patches[-1] \
        and tasks.patches[-1]["completed"] is None


def test_a_change_already_put_back_is_never_put_back_twice(world):
    _, tasks = world
    rec = journal.record("delete_task", "delete", "tasks", items=[], summary="two", status="complete",
                         undo={"tasks": [
                             {"op": "recreate", "id": "gone1", "title": "Again", "account": "acct_t1",
                              "tasklist": "tl1", "fields": {"title": "Again", "notes": "", "due": "",
                                                            "status": "needsAction"}},
                             {"op": "restore", "id": "nope", "title": "Missing", "account": "acct_t1",
                              "tasklist": "tl1", "fields": {"notes": "x"}}]})
    first = ia.undo_tasks(journal.get(rec["receipt_id"]))
    assert first["status"] == "partial"
    ia.undo_tasks(journal.get(rec["receipt_id"]))
    assert sum(1 for t in tasks.store.values() if t["title"] == "Again") == 1


# ── Undo data is encrypted at rest; the signed receipt keeps none of the words ──

def _undo_files():
    from agent_friday.services import undo_store
    d = undo_store._dir()
    return list(d.glob("u*.bin")) if d.is_dir() else []


def test_the_receipt_keeps_no_description_or_notes_and_the_kept_data_is_sealed(world):
    before = set(_undo_files())
    rid_e = _rid(agent._tool_delete_calendar_event({"event_id": "e1", "notify_guests": False}))
    rid_t = _rid(agent._tool_update_task({"account_id": "acct_t1", "tasklist_id": "tl1", "task_id": "k1",
                                          "notes": "any colour"}))
    for rid, secret in ((rid_e, "Bring the form"), (rid_t, "the blue ones")):
        rec = journal.get(rid)
        assert secret not in json.dumps(rec), "the item's own words are not in the ledger entry"
        row = rec["undo"][rec["domain"]][0]
        assert row.get("sealed") and "fields" not in row and row.get("field_names")
    files = [f for f in _undo_files() if f not in before]
    assert len(files) == 2
    for f in files:
        raw = f.read_bytes()
        assert raw.startswith(b"SEALED:") and b"Bring the form" not in raw and b"the blue ones" not in raw


def test_nothing_readable_is_kept_when_nothing_can_seal_it(world, monkeypatch):
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "protect", lambda data: (data, "plaintext"))
    before = len(_undo_files())
    rec = journal.get(_rid(agent._tool_update_task({"account_id": "acct_t1", "tasklist_id": "tl1",
                                                    "task_id": "k1", "notes": "any colour"})))
    assert not rec["undo"] and "could not be kept encrypted" in rec["summary"]
    assert len(_undo_files()) == before


def test_the_sealed_data_goes_once_its_undo_has_run(world):
    rid = _rid(agent._tool_update_task({"account_id": "acct_t1", "tasklist_id": "tl1", "task_id": "k1",
                                        "notes": "any colour"}))
    token = journal.get(rid)["undo"]["tasks"][0]["sealed"]
    from agent_friday.services import undo_store
    assert undo_store.read(token) == {"notes": "the blue ones"}
    _undo_on_card(rid)
    assert undo_store.read(token) is None and undo_store._path(token).exists() is False


def test_forgetting_a_person_removes_undo_data_that_names_them(world):
    cal, _ = world
    cal.store["e1"]["description"] = "Lunch with Ann Example about the house"
    rid = _rid(agent._tool_delete_calendar_event({"event_id": "e1", "notify_guests": False}))
    token = journal.get(rid)["undo"]["calendar"][0]["sealed"]
    from agent_friday.services import forget_registry, undo_store
    assert "undo_data" in forget_registry.registered()
    assert undo_store.forget_find({"ann example"}, set()) >= 1
    undo_store.forget_purge({"ann example"}, set())
    assert undo_store.read(token) is None


def test_off_the_record_undo_data_never_touches_disk(world, monkeypatch):
    from agent_friday.services import off_record, undo_store
    monkeypatch.setattr(off_record, "active", lambda: True)
    before = len(_undo_files())
    token = undo_store.keep({"notes": "private"})
    assert token and undo_store.read(token) == {"notes": "private"} and len(_undo_files()) == before


def test_undo_twice_puts_back_once(world):
    _, tasks = world
    rid = _rid(agent._tool_create_task({"account_id": "acct_t1", "title": "Once", "tasklist_id": "tl1"}))
    _undo_on_card(rid)
    assert ia.undo(rid)["status"] == "nothing"

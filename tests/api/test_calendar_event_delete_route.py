"""POST /api/calendar/events/<id>/delete: the Calendar page's delete, the owner's own click.

Body {calendar_id, account_id}; answers {status, ok, id, title, before}. It calls the same
calendar_write.delete_event the model tool does, against a fake Google here; a failure says nothing was
deleted and never carries the library's exception text to the browser.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import calendar_write as cw

INTERNAL = "SECRET-INTERNAL-/path/detail"  # pragma: allowlist secret


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Events:
    def __init__(self):
        self.store = {"ev7": {"id": "ev7", "summary": "Team lunch", "start": {"dateTime": "2026-10-22T12:00:00Z"},
                              "end": {"dateTime": "2026-10-22T13:00:00Z"}}}
        self.deleted = []
        self.fail_delete = False

    def get(self, calendarId, eventId):
        def run():
            if eventId not in self.store:
                raise RuntimeError("404 " + INTERNAL)
            return dict(self.store[eventId])
        return _Exec(run)

    def delete(self, calendarId, eventId, **kw):
        def run():
            if self.fail_delete:
                raise RuntimeError(INTERNAL)
            self.deleted.append((calendarId, eventId))
            return ""
        return _Exec(run)


@pytest.fixture
def google(monkeypatch):
    ev = _Events()

    class Svc:
        def events(self):
            return ev
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    monkeypatch.setattr(cw, "writable_accounts", lambda: [])
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (Svc(), None))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda: None)
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    return ev


def test_the_route_deletes_the_event_and_answers_with_what_it_was(client, google):
    r = client.post("/api/calendar/events/ev7/delete", json={"calendar_id": "work@group.example", "account_id": ""})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    d = r.get_json()
    assert d["status"] == "ok" and d["ok"] is True and d["id"] == "ev7" and d["title"] == "Team lunch"
    assert d["before"]["start"] == "2026-10-22T12:00:00Z" and d["before"]["calendar_id"] == "work@group.example"
    assert google.deleted == [("work@group.example", "ev7")]


def test_a_missing_body_means_the_main_calendar(client, google):
    r = client.post("/api/calendar/events/ev7/delete")
    assert r.status_code == 200 and google.deleted == [("primary", "ev7")]


def test_a_failure_says_nothing_was_deleted_and_hides_the_internals(client, google):
    google.fail_delete = True
    r = client.post("/api/calendar/events/ev7/delete", json={})
    raw = r.get_data(as_text=True)
    assert r.status_code == 400 and "SECRET-INTERNAL" not in raw
    d = r.get_json()
    assert d["ok"] is False and d["message"].startswith("Nothing was deleted") and "error" in d["message"]
    r = client.post("/api/calendar/events/missing/delete", json={})
    assert "SECRET-INTERNAL" not in r.get_data(as_text=True) and r.get_json()["ok"] is False
    assert google.deleted == []


def test_a_quick_add_event_kept_on_this_pc_is_removed_from_the_local_list(client, google, monkeypatch):
    from agent_friday.routes import calendar as rc
    kept = [{"id": "local-abc", "title": "Pick up parcel", "start_time": "2026-10-23T09:00"},
            {"id": "local-def", "title": "Other", "start_time": "2026-10-24T09:00"}]
    saved = []
    monkeypatch.setattr(rc, "_load_local_events", lambda: list(kept))
    monkeypatch.setattr(rc, "_save_local_events", lambda evs: saved.append(evs))
    monkeypatch.setattr(rc, "_drop_calendar_cache", lambda: None)
    d = client.post("/api/calendar/events/local-abc/delete", json={}).get_json()
    assert d["ok"] and d["title"] == "Pick up parcel" and d["before"]["calendar_id"] == "local"
    assert [e["id"] for e in saved[-1]] == ["local-def"] and google.deleted == []
    r = client.post("/api/calendar/events/local-zzz/delete", json={})
    assert r.status_code == 404 and r.get_json()["ok"] is False


def test_without_a_write_token_the_page_is_told_to_reconnect(client, google, monkeypatch):
    monkeypatch.setattr(cw, "write_ready", lambda: (False, "read-only"))
    d = client.post("/api/calendar/events/ev7/delete", json={}).get_json()
    assert d["ok"] is False and d["needs_reconnect"] is True and google.deleted == []

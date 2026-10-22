"""BD-06 read first: connecting Google asks only to READ the calendar and Google Tasks.

Before: every connect (and every reconnect) asked to read AND change the calendar and Tasks,
whether or not the owner ever wanted Friday to change them. Now an ordinary connect asks for
calendar.readonly and tasks.readonly; changing them is asked for per account, only from its own
"Allow calendar changes" / "Allow task changes" button (Google's own consent screen then shows
exactly that). A read-only account still reads and names its events; a change on it is refused
before Google is called, with the words that say how to allow it. Synthetic accounts only.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.routes import google_accounts as ra
from agent_friday.services import calendar_write as cw
from agent_friday.services import google_accounts as ga
from agent_friday.services import google_oauth_client as goc

CAL_READ = "https://www.googleapis.com/auth/calendar.readonly"
CAL_RW = "https://www.googleapis.com/auth/calendar"
CAL_EVENTS = "https://www.googleapis.com/auth/calendar.events"
TASKS_READ = "https://www.googleapis.com/auth/tasks.readonly"
TASKS_RW = "https://www.googleapis.com/auth/tasks"

_CFG = {"installed": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s",
                      "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                      "token_uri": "https://oauth2.googleapis.com/token",
                      "redirect_uris": ["http://127.0.0.1"]}}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    import shutil
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)
    ga._MIGRATION_DONE = False
    ra._RL_HITS.clear()
    ra._PENDING.clear()
    monkeypatch.setattr(goc, "active_client", lambda discover=None: (_CFG, "test", "own"))
    yield
    ra._PENDING.clear()
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)


def _write(scopes, aid="a1"):
    ga.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"id": aid, "email": "me@example.test", "label": "Me", "status": "connected",
           "services": {"gmail": True, "calendar": True}, "scopes": list(scopes), "enc_method": "vault"}
    ga.ACCOUNTS_INDEX.write_text(json.dumps({"version": 1, "accounts": [rec]}), encoding="utf-8")
    ga._MIGRATION_DONE = True
    return rec


def _scopes_in(url):
    from urllib.parse import urlparse, parse_qs
    return set(parse_qs(urlparse(url).query)["scope"][0].split())


def test_an_ordinary_connect_asks_only_to_read_the_calendar_and_tasks(client):
    d = client.post("/api/google/accounts/connect", json={"label": "Home"}).get_json()
    asked = _scopes_in(d["auth_url"])
    assert {CAL_READ, TASKS_READ} <= asked
    assert not asked & {CAL_RW, CAL_EVENTS, TASKS_RW}, "a plain connect asked to change the calendar or Tasks"


def test_a_reconnect_does_not_ask_for_changes_either(client):
    _write([CAL_READ, TASKS_READ])
    d = client.post("/api/google/accounts/connect", json={"account_id": "a1"}).get_json()
    assert not _scopes_in(d["auth_url"]) & {CAL_RW, CAL_EVENTS, TASKS_RW}


@pytest.mark.parametrize("flag,wanted,not_wanted", [
    ("include_calendar_write", CAL_RW, TASKS_RW),
    ("include_tasks_write", TASKS_RW, CAL_RW),
], ids=["calendar", "tasks"])
def test_allowing_changes_asks_for_exactly_that_on_googles_screen(client, flag, wanted, not_wanted):
    _write([CAL_READ, TASKS_READ])
    d = client.post("/api/google/accounts/connect", json={"account_id": "a1", flag: True}).get_json()
    asked = _scopes_in(d["auth_url"])
    assert wanted in asked and not_wanted not in asked
    assert "https://www.googleapis.com/auth/gmail.send" not in asked
    assert ra._PENDING[d["state"]][flag] is True, "the callback leg must rebuild the same request"
    assert wanted in [x["scope"] for x in d["requested_scopes"]], "the owner is shown what is asked first"


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class _Events:
    def __init__(self):
        self.deleted = []

    def get(self, calendarId, eventId):
        return _Exec(lambda: {"id": eventId, "summary": "Dentist", "status": "confirmed",
                              "start": {"dateTime": "2026-11-03T10:00:00Z"},
                              "end": {"dateTime": "2026-11-03T10:30:00Z"}})

    def delete(self, calendarId, eventId, **kw):
        return _Exec(lambda: self.deleted.append(eventId) or "")


class _Svc:
    def __init__(self):
        self.ev = _Events()

    def events(self):
        return self.ev


@pytest.fixture
def read_only_calendar(monkeypatch):
    rec = _write([CAL_READ, TASKS_READ])
    svc = _Svc()
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (svc, None))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda: None)
    return rec, svc.ev


def test_a_read_only_account_still_reads_and_names_its_events(read_only_calendar):
    out = cw.get_event("ev1")
    assert out.get("ok") is True and out["title"] == "Dentist" and out["account_id"] == "a1"
    got = cw.read_event("ev1")
    assert got.get("ok") is True and got["before"]["title"] == "Dentist"


def test_a_change_on_a_read_only_calendar_is_refused_with_the_way_to_allow_it(read_only_calendar):
    _rec, ev = read_only_calendar
    out = cw.delete_event("ev1")
    assert out.get("needs_reconnect") and not out.get("ok")
    assert "Allow calendar changes" in out["error"]
    assert ev.deleted == [], "Google was asked to delete on an account that may only read"
    assert "Allow calendar changes" in cw.public_error({"code": "needs_reconnect"})


def test_a_task_change_on_a_read_only_account_is_refused_before_google(monkeypatch):
    _write([CAL_READ, TASKS_READ])
    called = []
    monkeypatch.setattr(ga, "credentials_for", lambda aid: called.append(aid) or None)
    out = ga.create_task("a1", "Buy stamps")
    assert "Allow task changes" in out.get("error", "")
    assert called == [], "Google credentials were loaded for a change the account may not make"
    out = ga.delete_task("a1", "@default", "t1")
    assert "Allow task changes" in out.get("error", "") and called == []


def test_an_account_that_allowed_changes_keeps_them(monkeypatch):
    _write([CAL_READ, TASKS_READ, CAL_RW, TASKS_RW])
    assert cw.write_ready()[0] is True
    public = ga._public_record(ga.get_account("a1") or {})
    assert "changes" in public, "an account says what it may change"
    assert public["changes"] == {"calendar": True, "tasks": True}
    _write([CAL_READ, TASKS_READ])
    assert ga._public_record(ga.get_account("a1") or {})["changes"] == {"calendar": False, "tasks": False}


def _two_accounts():
    ga.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    recs = [{"id": "w1", "email": "work@example.test", "label": "Work", "status": "connected",
             "services": {"calendar": True}, "scopes": [CAL_READ, CAL_RW], "enc_method": "vault"},
            {"id": "r1", "email": "home@example.test", "label": "Home", "status": "connected",
             "services": {"calendar": True}, "scopes": [CAL_READ], "enc_method": "vault"}]
    ga.ACCOUNTS_INDEX.write_text(json.dumps({"version": 1, "accounts": recs}), encoding="utf-8")
    ga._MIGRATION_DONE = True


def test_with_one_account_that_may_change_and_one_that_may_only_read(monkeypatch):
    _two_accounts()
    svc = _Svc()
    monkeypatch.setattr(cw, "_service", lambda account_id=None: (svc, None))
    monkeypatch.setattr(cw, "_drop_calendar_cache", lambda: None)
    assert cw.get_event("ev1")["account_id"] == "w1", "an unnamed read uses the account a change would"
    assert cw.get_event("ev1", account_id="Home")["account_id"] == "r1", "the read-only account still reads"
    out = cw.delete_event("ev1", account_id="Home")
    assert "Allow calendar changes" in out["error"] and svc.ev.deleted == []
    plan = cw.get_event("ev1", account_id="Home", for_write=True)
    assert not plan.get("ok") and "Allow calendar changes" in plan["error"], \
        "a change is never planned on an account that may not make it"

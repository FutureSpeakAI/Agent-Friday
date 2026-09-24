"""Every calendar write names the account it lands on.

With two Google accounts able to write (Personal and Work, say), an event
created without naming one used to land on whichever account was connected
first. An interview on the wrong calendar is visible to the wrong people, so
an unnamed write with more than one candidate is refused, and the refusal
lists the accounts so the next call can name one.
"""
from __future__ import annotations

import pytest

from agent_friday.services import calendar_write as cw
from agent_friday.services import google_accounts as ga

W = cw.FULL_CALENDAR_SCOPE
R = "https://www.googleapis.com/auth/calendar.readonly"


def _acct(aid, label, email, scopes=(W,), status="connected", calendar=True):
    return {"id": aid, "label": label, "email": email, "status": status,
            "scopes": list(scopes), "services": {"calendar": calendar}}


class _Events:
    def __init__(self):
        self.inserted, self.patched = [], []

    def insert(self, calendarId, body, **kw):
        self.inserted.append((calendarId, body))
        return _Exec({"id": "new1", "summary": body.get("summary")})

    def get(self, calendarId, eventId):
        return _Exec({"id": eventId, "summary": "old"})

    def patch(self, calendarId, eventId, body, **kw):
        self.patched.append((eventId, body))
        return _Exec({"id": eventId, "summary": body.get("summary")})

    def list(self, **kw):
        return _Exec({"items": [{"id": "e1", "summary": "Dentist",
                                 "start": {"dateTime": "2026-10-01T09:00:00Z"}}]})


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


class _Svc:
    def __init__(self):
        self.ev = _Events()

    def events(self):
        return self.ev


@pytest.fixture
def two_accounts(monkeypatch):
    accts = [_acct("p1", "Personal", "me@example.com"),
             _acct("w1", "Work", "me@work.example.com")]
    monkeypatch.setattr(ga, "list_accounts", lambda: accts)
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    used = []
    svc = _Svc()

    def fake_service(account_id=None):
        used.append(account_id)
        return svc, None
    monkeypatch.setattr(cw, "_service", fake_service)
    return used, svc


def test_an_unnamed_create_is_refused_when_two_accounts_can_write(two_accounts):
    used, svc = two_accounts
    out = cw.create_event(title="Interview", start="2026-10-01T10:00:00-05:00")
    assert out.get("needs_account") is True
    assert "Personal" in out["error"] and "Work" in out["error"]
    assert "p1" in out["error"] and "w1" in out["error"]
    assert not svc.ev.inserted and not used, "nothing may be written or even opened"


def test_an_unnamed_update_and_annotate_are_refused_too(two_accounts):
    used, svc = two_accounts
    assert cw.update_event("e1", title="x").get("needs_account") is True
    assert cw.annotate_events("dentist", note="bring card").get("needs_account") is True
    assert not svc.ev.patched and not used


@pytest.mark.parametrize("name", ["w1", "Work", "work", "me@work.example.com"])
def test_a_named_account_is_the_one_written(two_accounts, name):
    used, svc = two_accounts
    out = cw.create_event(title="Interview", start="2026-10-01T10:00:00-05:00",
                          account_id=name)
    assert out.get("ok") is True, out
    assert out["account_id"] == "w1"
    assert used == ["w1"]
    assert svc.ev.inserted


def test_an_unknown_account_is_refused_with_the_list(two_accounts):
    used, svc = two_accounts
    out = cw.create_event(title="Interview", start="2026-10-01T10:00:00-05:00",
                          account_id="nobody")
    assert out.get("needs_account") is True
    assert "Personal" in out["error"] and not used


def test_annotate_searches_and_writes_the_same_named_account(two_accounts):
    used, svc = two_accounts
    out = cw.annotate_events("dentist", note="bring card", account_id="Personal")
    assert out.get("ok") is True, out
    assert set(used) == {"p1"}, used
    assert svc.ev.patched


def test_one_writable_account_needs_no_name(monkeypatch):
    # A read-only second account cannot receive the write, so there is no
    # ambiguity to resolve.
    monkeypatch.setattr(ga, "list_accounts", lambda: [
        _acct("p1", "Personal", "me@example.com"),
        _acct("w1", "Work", "me@work.example.com", scopes=(R,)),
        _acct("x1", "Old", "old@example.com", status="needs_reauth"),
        _acct("c1", "NoCal", "nocal@example.com", calendar=False)])
    aid, err = cw.resolve_write_account(None)
    assert (aid, err) == ("p1", None)
    aid, err = cw.resolve_write_account("Work")
    assert aid is None and "Personal" in err


def test_no_multi_account_store_keeps_the_legacy_path(monkeypatch):
    monkeypatch.setattr(ga, "list_accounts", lambda: [])
    assert cw.resolve_write_account(None) == (None, None)


def test_the_named_account_supplies_the_credentials(monkeypatch):
    got = []
    monkeypatch.setattr(ga, "credentials_for", lambda aid: got.append(aid) or "CREDS")
    import googleapiclient.discovery as disc
    monkeypatch.setattr(disc, "build", lambda *a, **k: ("SVC", k.get("credentials")))
    svc, err = cw._service("w1")
    assert err is None and svc == ("SVC", "CREDS") and got == ["w1"]
    monkeypatch.setattr(ga, "credentials_for", lambda aid: None)
    svc, err = cw._service("w1")
    assert svc is None and "reconnect" in err


def test_the_tools_pass_the_named_account_through(monkeypatch):
    import agent_friday.services.agent as agent
    seen = {}

    def spy(kind):
        def f(*a, **k):
            seen[kind] = k
            return {"ok": True}
        return f
    monkeypatch.setattr(cw, "create_event", spy("create"))
    monkeypatch.setattr(cw, "update_event", spy("update"))
    monkeypatch.setattr(cw, "annotate_events", spy("annotate"))
    agent._tool_create_calendar_event({"title": "x", "start": "2026-10-01T10:00:00",
                                       "account_id": "Work"})
    agent._tool_update_calendar_event({"event_id": "e1", "title": "y", "account_id": "Work"})
    agent._tool_annotate_calendar_events({"query": "q", "note": "n", "account_id": "Work"})
    assert {k: v["account_id"] for k, v in seen.items()} == {
        "create": "Work", "update": "Work", "annotate": "Work"}
    schemas = {t["name"]: t for t in agent.CLAUDE_TOOLS}
    for name in ("create_calendar_event", "update_calendar_event",
                 "annotate_calendar_events"):
        assert "account_id" in schemas[name]["input_schema"]["properties"], name

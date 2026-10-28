"""A new write permission shows its difference before Google is asked (BD-06, NS-23.2-2).

Before: "Allow calendar changes", "Allow task changes" and "Allow saving to Google Contacts"
went straight to Google's consent screen; only "Reconnect with sending" listed what it asked
for first, and the server took any capability a request named. Now each one first shows the
permission difference (each permission added, in plain words; what it lets Friday do; what
still waits for a card; what stays the same), and the connect route refuses to ask Google for
a new write permission on an existing account unless the request carries the diff_id of that
exact difference. Synthetic accounts only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.routes import google_accounts as ra
from agent_friday.services import google_accounts as ga
from agent_friday.services import google_oauth_client as goc

ROOT = Path(__file__).resolve().parents[2]
CAL_READ = "https://www.googleapis.com/auth/calendar.readonly"
CAL_RW = "https://www.googleapis.com/auth/calendar"
CAL_EVENTS = "https://www.googleapis.com/auth/calendar.events"
TASKS_READ = "https://www.googleapis.com/auth/tasks.readonly"
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


def _diff(client, caps, aid="a1"):
    r = client.get("/api/google/accounts/%s/permission-diff?capability=%s" % (aid, ",".join(caps)))
    assert r.status_code == 200, "there is no permission difference to show (%s)" % r.status_code
    return r.get_json()["diff"]


def test_the_difference_says_what_is_added_what_it_allows_and_what_still_asks(client):
    _write([CAL_READ, TASKS_READ])
    d = _diff(client, ["calendar"])
    assert [a["scope"] for a in d["adds"]] == [CAL_RW]
    assert "calendar" in d["adds"][0]["what"].lower()
    assert "create calendar event" in d["lets_friday"]
    assert any("card" in s for s in d["still"])
    assert CAL_READ in [a["scope"] for a in d["already"]] and d["diff_id"]


def test_google_is_not_asked_for_a_new_write_permission_without_its_difference(client):
    _write([CAL_READ, TASKS_READ])
    r = client.post("/api/google/accounts/connect", json={"account_id": "a1", "include_calendar_write": True})
    assert r.status_code == 409, "a new write permission was asked for without showing what it adds"
    body = r.get_json()
    assert "auth_url" not in body and body["diff"]["adds"] and not ra._PENDING
    stale = _diff(client, ["tasks"])["diff_id"]                 # a difference for something else
    r = client.post("/api/google/accounts/connect",
                    json={"account_id": "a1", "include_calendar_write": True, "diff_id": stale})
    assert r.status_code == 409
    ok = client.post("/api/google/accounts/connect", json={"account_id": "a1", "include_calendar_write": True,
                                                            "diff_id": _diff(client, ["calendar"])["diff_id"]})
    assert ok.status_code == 200 and ok.get_json().get("auth_url")


def test_a_permission_already_held_is_not_shown_as_new(client):
    _write([CAL_READ, CAL_EVENTS])
    assert _diff(client, ["calendar"])["adds"] == []


def test_each_capability_the_panel_offers_has_a_difference(client):
    _write([CAL_READ, TASKS_READ])
    for cap in ("send", "calendar", "tasks", "contacts"):
        assert _diff(client, [cap])["adds"], cap
    assert client.get("/api/google/accounts/a1/permission-diff?capability=delete_everything").status_code == 400
    assert client.get("/api/google/accounts/nope/permission-diff?capability=tasks").status_code == 404


def test_a_new_account_is_unchanged(client):
    d = client.post("/api/google/accounts/connect", json={"label": "Home", "include_send": True}).get_json()
    assert d["status"] == "ok" and d.get("auth_url")


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_buttons_show_the_difference_before_google(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    assert "permission-diff" in text and "permissionDiffText" in text, \
        "the Allow buttons go to Google without showing what they add"
    assert text.count("diff_id") >= 3

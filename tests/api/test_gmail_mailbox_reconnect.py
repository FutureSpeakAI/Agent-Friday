"""Reconnect with sending: one consent screen per account asks for read, send
and mailbox changes together, only from the explicit button, and the owner is
shown exactly those scopes first. Ordinary connects and reconnects ask for
neither send nor modify."""
import json

import pytest

from agent_friday.services import google_accounts as ga
from agent_friday.services import google_oauth_client as goc
from agent_friday.routes import google_accounts as ra

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


def _write(rec):
    ga.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    ga.ACCOUNTS_INDEX.write_text(json.dumps({"version": 1, "accounts": [rec]}), encoding="utf-8")
    ga._MIGRATION_DONE = True


def _scopes_in(url):
    from urllib.parse import urlparse, parse_qs
    return set(parse_qs(urlparse(url).query)["scope"][0].split())


def test_the_flow_asks_for_send_and_modify_only_when_told():
    plain, _, _ = ga.build_auth_flow()
    both, _, _ = ga.build_auth_flow(include_send=True, include_modify=True)
    assert ga.GMAIL_SEND not in plain.oauth2session.scope and ga.GMAIL_MODIFY not in plain.oauth2session.scope
    assert {ga.GMAIL_SEND, ga.GMAIL_MODIFY, ga.GMAIL_READ} <= set(both.oauth2session.scope)


def test_reconnect_with_sending_is_one_consent_for_both(client):
    _write({"id": "a1", "email": "me@example.test", "label": "Me", "status": "connected",
            "services": {"gmail": True}, "scopes": [ga.GMAIL_READ], "enc_method": "vault"})
    d = client.post("/api/google/accounts/connect", json={"account_id": "a1", "mailbox": True}).get_json()
    assert d["status"] == "ok" and d["requesting_send"] and d["requesting_modify"]
    asked = _scopes_in(d["auth_url"])
    assert {ga.GMAIL_READ, ga.GMAIL_SEND, ga.GMAIL_MODIFY} <= asked          # one screen, all of them
    assert "https://mail.google.com/" not in asked                          # never permanent delete
    assert [x["scope"] for x in d["requested_scopes"]] == [x["scope"] for x in ga.mailbox_scopes()]
    assert d["login_hint"] == "me@example.test"
    # "new" is judged against what this account actually granted (read only here)
    by = {x["scope"]: x for x in d["requested_scopes"]}
    assert by[ga.CALENDAR_RW]["new"] and by[ga.GMAIL_SEND]["new"] and not by[ga.GMAIL_READ]["new"]
    # the callback leg rebuilds the same scope set
    pending = ra._PENDING[d["state"]]
    assert pending["include_send"] is True and pending["include_modify"] is True


def test_plain_reconnect_asks_for_neither(client):
    _write({"id": "a1", "email": "me@example.test", "label": "Me", "status": "needs_reauth",
            "services": {"gmail": True}, "scopes": [ga.GMAIL_READ, ga.GMAIL_SEND], "enc_method": "vault"})
    d = client.post("/api/google/accounts/connect", json={"account_id": "a1"}).get_json()
    asked = _scopes_in(d["auth_url"])
    assert ga.GMAIL_SEND not in asked and ga.GMAIL_MODIFY not in asked
    assert d["requesting_send"] is False and d["requesting_modify"] is False


def test_settings_can_show_the_exact_scopes_and_what_each_account_has(client):
    s = client.get("/api/google/accounts/mailbox-scopes").get_json()["scopes"]
    by = {x["scope"]: x for x in s}
    assert by[ga.GMAIL_SEND]["new"] and by[ga.GMAIL_MODIFY]["new"] and not by[ga.GMAIL_READ]["new"]
    assert "approval" in by[ga.GMAIL_SEND]["what"] and "delete" in by[ga.GMAIL_MODIFY]["what"]
    _write({"id": "a1", "email": "me@example.test", "label": "Me", "status": "connected",
            "services": {"gmail": True}, "scopes": [ga.GMAIL_READ], "enc_method": "vault"})
    acct = client.get("/api/google/accounts").get_json()["accounts"][0]
    assert acct["mail"] == {"read": True, "send": False, "modify": False}


def test_own_client_setup_lists_send_and_modify():
    assert ga.GMAIL_SEND in goc.byo_scopes() and ga.GMAIL_MODIFY in goc.byo_scopes()

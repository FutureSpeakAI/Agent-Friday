"""Relationship memory routes: the LinkedIn import, follow-ups, settings, and
the opt-in consent for saving to Google Contacts."""
import json
import shutil

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
    # The isolated test home (root conftest); the app's routes and this
    # module must read the same people graph, so FRIDAY_DIR is not moved.
    from pathlib import Path
    import agent_friday.core as core
    home = Path(core.FRIDAY_DIR)
    shutil.rmtree(home / "relationships", ignore_errors=True)
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)
    ga._MIGRATION_DONE = False
    ra._RL_HITS.clear()
    ra._PENDING.clear()
    monkeypatch.setattr(goc, "active_client", lambda discover=None: (_CFG, "test", "own"))
    yield home
    ra._PENDING.clear()
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)


def _account(scopes):
    ga.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    ga.ACCOUNTS_INDEX.write_text(json.dumps({"version": 1, "accounts": [
        {"id": "a1", "email": "me@example.test", "label": "Me", "status": "connected",
         "services": {"contacts": True}, "scopes": scopes, "enc_method": "vault"}]}),
        encoding="utf-8")
    ga._MIGRATION_DONE = True


def _scopes_in(url):
    from urllib.parse import urlparse, parse_qs
    return set(parse_qs(urlparse(url).query)["scope"][0].split())


def test_saving_contacts_is_asked_for_only_from_its_own_button(client):
    _account([ga.CONTACTS_READ])
    plain = client.post("/api/google/accounts/connect", json={"account_id": "a1"}).get_json()
    assert ga.CONTACTS_RW not in _scopes_in(plain["auth_url"])
    mailbox = client.post("/api/google/accounts/connect",
                          json={"account_id": "a1", "mailbox": True}).get_json()
    assert ga.CONTACTS_RW not in _scopes_in(mailbox["auth_url"])
    d = client.post("/api/google/accounts/connect",
                    json={"account_id": "a1", "include_contacts_write": True}).get_json()
    assert d["requesting_contacts_write"] is True
    asked = _scopes_in(d["auth_url"])
    assert ga.CONTACTS_RW in asked and ga.GMAIL_SEND not in asked
    assert ra._PENDING[d["state"]]["include_contacts_write"] is True


def test_the_contacts_panel_sees_which_accounts_can_save(client):
    _account([ga.CONTACTS_READ])
    d = client.get("/api/contacts/google-write").get_json()
    assert d["accounts"] == [{"id": "a1", "email": "me@example.test", "label": "Me",
                              "can_save": False}]
    _account([ga.CONTACTS_READ, ga.CONTACTS_RW])
    assert client.get("/api/contacts/google-write").get_json()["accounts"][0]["can_save"]


def test_linkedin_import_route(client):
    csv_text = ("First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
                "Rae,Lin,https://www.linkedin.com/in/rae-example,rae@initech.example.com,"
                "Initech,Recruiter,03 Mar 2026\n")
    d = client.post("/api/contacts/import/linkedin", json={"csv": csv_text}).get_json()
    assert d["status"] == "ok" and d["result"]["created"] == 1
    rows = client.get("/api/contacts").get_json()["contacts"]
    rae = next(c for c in rows if c["name"] == "Rae Lin")
    assert rae["company"] == "Initech" and rae["sources"] == ["linkedin"]
    bad = client.post("/api/contacts/import/linkedin", json={"csv": "a,b\n1,2\n"})
    assert bad.status_code == 400


def test_follow_up_routes(client):
    r = client.post("/api/relationships/follow-ups",
                    json={"person": "Rae Lin", "note": "salary band", "in_days": 1})
    fu = r.get_json()["follow_up"]
    listed = client.get("/api/relationships/follow-ups").get_json()["follow_ups"]
    assert [f["id"] for f in listed] == [fu["id"]]
    assert client.post("/api/relationships/follow-ups/%s/done" % fu["id"]).status_code == 200
    assert client.get("/api/relationships/follow-ups").get_json()["follow_ups"] == []
    assert client.post("/api/relationships/follow-ups", json={}).status_code == 400


def test_settings_are_clamped(client):
    d = client.post("/api/relationships/config",
                    json={"cold_after_days": 500, "sync_enabled": False}).get_json()
    assert d["config"]["cold_after_days"] == 90 and d["config"]["sync_enabled"] is False
    assert client.post("/api/relationships/config",
                       json={"cold_after_days": "soon"}).status_code == 400

"""Every stored credential can be replaced and removed from one list, and says where it came
from (BD-06: NS-23.3-4, NS-23.3-5).

Before: Settings > Security had no list of credentials at all (the inventory route had no
screen), nothing said how a credential came in, phone secrets were not listed, a platform's
key was called an "AI provider key", add-on sign-ins left no record and could not be removed
from Friday, and there was no one place to remove a credential. Now each row says what it is
for, where it came from and when, where to replace it and what removing it does; Remove
(a press on this PC) runs that kind's own removal, revoking at the provider where the
provider allows it. Synthetic values only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.services import keystore as ks
from tests.desk_press_support import desk_headers

FAKE_KEY = "not-a-real-key-" + "5" * 20  # pragma: allowlist secret
FAKE_PHONE = "synthetic" + "5" * 24  # pragma: allowlist secret
ROOT = Path(__file__).resolve().parents[2]


def _cl():
    try:
        from agent_friday.services import credential_lifecycle
    except ImportError:
        raise AssertionError("no credential lifecycle on this tree (services/credential_lifecycle)")
    return credential_lifecycle


@pytest.fixture
def store(tmp_path, monkeypatch):
    from agent_friday import core
    from agent_friday import mcp_oauth
    home = tmp_path / ".friday"
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE", home / "vault" / ".vault_config.json")
    monkeypatch.setattr(cs, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(cs, "_CRED_AUDIT_LOG", home / "security" / "credential_audit.jsonl")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", home / "providers" / "keys")
    monkeypatch.setattr(mcp_oauth, "OAUTH_DIR", home / "mcp_oauth")
    monkeypatch.setattr(ks, "KEYSTORE_PATH", home / "security" / "keystore.json")
    ks._reset_cache_for_tests()
    cs._USE.clear()
    cs._USE_FLUSHED.clear()
    yield home
    ks._reset_cache_for_tests()
    cs._USE.clear()
    cs._USE_FLUSHED.clear()


def _row(rows, name):
    got = next((r for r in rows if r["name"] == name), None)
    assert got is not None, "%s is not listed: %s" % (name, [r["name"] for r in rows])
    return got


def _rows(client):
    d = client.get("/api/security/credentials").get_json()
    assert d["ok"]
    return d["credentials"]


def test_each_credential_says_what_it_is_for_and_where_it_came_from(client, store):
    cs.set_provider_key("example", FAKE_KEY)
    row = _row(_rows(client), "providers/keys/example.key")
    assert row.get("for") == "example", "a credential does not say what it is for"
    assert row.get("came_from") == "saved in Friday" and row.get("first_set")
    assert "AI providers" in row.get("replace_in", "")
    assert row["remove"]["revokes_at_provider"] is False and "provider's site" in row["remove"]["note"]
    assert FAKE_KEY not in json.dumps(_rows(client))


def test_phone_secrets_are_listed_and_a_platform_key_is_not_called_an_ai_key(client, store):
    from agent_friday.phone import config as pc
    pc.set_secret("auth_token", FAKE_PHONE)
    cs.set_provider_key("platform_mastodon", FAKE_KEY)
    rows = _rows(client)
    phone = next((r for r in rows if r["name"].endswith("auth_token.bin")), None)
    assert phone is not None, "a phone secret is not in the list"
    assert phone["kind"] == "phone secret" and phone.get("came_from") == "saved in phone settings"
    assert "Twilio" in phone["remove"]["note"]
    plat = _row(rows, "providers/keys/platform_mastodon.key")
    assert plat["kind"] == "publishing platform key" and plat.get("for") == "mastodon"


def test_an_add_on_sign_in_is_recorded_and_can_be_removed(client, store):
    from agent_friday import mcp_oauth
    mcp_oauth._store_token_reply("calendar-tools", {}, {"access_token": "synthetic-access"})  # pragma: allowlist secret
    row = _row(_rows(client), "mcp_oauth/calendar-tools.oauth.enc")
    assert row.get("came_from") == "signed in to the add-on", "an add-on sign-in left no record"
    r = client.post("/api/security/credentials/remove", json={"name": row["name"]}, headers=desk_headers())
    assert r.status_code == 200, r.get_data(as_text=True)
    assert not (store / "mcp_oauth" / "calendar-tools.oauth.enc").exists()
    assert "cannot be revoked from here" in r.get_json()["message"]


def test_removing_a_provider_key_deletes_it_and_says_it_is_not_cancelled_at_the_provider(client, store):
    _cl()
    cs.set_provider_key("example", FAKE_KEY)
    r = client.post("/api/security/credentials/remove", json={"name": "providers/keys/example.key"},
                    headers=desk_headers())
    assert r.status_code == 200 and r.get_json()["removed"] is True
    assert cs.provider_key_status("example") != "connected"
    assert "provider's site" in r.get_json()["message"]
    events = [e["event"] for e in cs.read_audit(category="credential")]
    assert "removed" in events


def test_removing_a_google_account_goes_through_its_revoke(client, store, monkeypatch):
    cl = _cl()
    from agent_friday.services import google_accounts as ga
    seen = []
    monkeypatch.setattr(cl, "rows", lambda: [] if seen else [{
        "name": "google_accounts/tokens/acct0001.token.enc", "kind": "Google account sign-in",
        "for": "Personal", "remove": {"note": "Friday asks Google to revoke it."}}])
    monkeypatch.setattr(ga, "remove_account", lambda aid: seen.append(aid) or True)
    if hasattr(ga, "remove_account_detail"):
        monkeypatch.setattr(ga, "remove_account_detail",
                            lambda aid: seen.append(aid) or {"removed": aid, "revoked_at_google": True})
    out = cl.remove("google_accounts/tokens/acct0001.token.enc")
    assert seen == ["acct0001"] and out["removed"] is True


def test_removal_needs_a_press_on_this_pc_and_only_takes_a_listed_name(client, store):
    _cl()
    cs.set_provider_key("example", FAKE_KEY)
    r = client.post("/api/security/credentials/remove", json={"name": "providers/keys/example.key"})
    assert r.status_code == 403 and cs.provider_key_status("example") == "connected"
    for bad in ("../security/keystore.json", "providers/keys/../../security/keystore.json",
                "security/keystore.json", ""):
        r = client.post("/api/security/credentials/remove", json={"name": bad}, headers=desk_headers())
        assert r.status_code == 404, bad
    assert (store / "security" / "keystore.json").exists()


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_settings_lists_the_credentials_with_remove(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    assert "CredentialsPanel" in text and "/api/security/credentials/remove" in text, \
        "Settings has no list of stored credentials to replace or remove"

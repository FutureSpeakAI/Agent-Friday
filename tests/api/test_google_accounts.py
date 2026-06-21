"""Tests for secure multi-account Google integration.

Focus: the non-negotiable security guarantees (tokens encrypted at rest, never
in API responses, per-account isolation, audit trail, rate limiting) plus the
account lifecycle and legacy single-account migration.

No real Google network calls are made — credentials and per-account fetches are
faked/monkeypatched. The root conftest redirects HOME to a temp dir, so all
on-disk state lands in the isolated test home.
"""

import json
import shutil

import pytest

from services import google_accounts as ga
from services import credential_store as cs
from routes import google_accounts as ra


class FakeCreds:
    """Stand-in for a google.oauth2 Credentials object."""

    def __init__(self, email="user@example.com", scopes=None, valid=True,
                 expired=False, refresh_token="refresh-xyz", token="access-abc"):  # pragma: allowlist secret
        self.scopes = scopes or list(ga.GOOGLE_MULTI_SCOPES)
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token  # pragma: allowlist secret
        self.token = token

    def to_json(self):
        return json.dumps({
            "token": self.token,
            "refresh_token": self.refresh_token,
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": self.scopes,
        })


@pytest.fixture(autouse=True)
def clean_ga():
    """Isolate per-test: wipe the accounts store, reset module caches/limiters."""
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)
    ga._MIGRATION_DONE = False
    ra._RL_HITS.clear()
    ra._PENDING.clear()
    yield
    if ga.ACCOUNTS_DIR.exists():
        shutil.rmtree(ga.ACCOUNTS_DIR, ignore_errors=True)


# ── credential_store ─────────────────────────────────────────────────────────
class TestCredentialStore:
    def test_protect_roundtrip(self):
        data = b'{"token":"super-secret","refresh_token":"rt"}'
        blob, method = cs.protect(data)
        assert method in ("vault", "dpapi", "plaintext")
        assert cs.unprotect(blob) == data

    def test_encrypted_blob_is_not_plaintext(self):
        """When a real protection method is active, the secret must not appear
        verbatim in the blob."""
        if cs.protection_method() == "plaintext":
            pytest.skip("no encryption available on this host")
        secret = b"the-refresh-token-value"
        blob, _ = cs.protect(secret)
        assert secret not in blob

    def test_write_secret_file_is_encrypted(self, tmp_path):
        p = tmp_path / "x.enc"
        cs.write_secret(p, b'{"refresh_token":"abc123"}')
        raw = p.read_bytes()
        if cs.protection_method() != "plaintext":
            assert b"abc123" not in raw
        assert cs.read_secret(p) == b'{"refresh_token":"abc123"}'

    def test_audit_event_no_token(self):
        cs.audit_event("google_account", "connect", account_id="abcd", success=True)
        entries = cs.read_audit(category="google_account")
        assert entries and entries[-1]["event"] == "connect"
        assert "token" not in json.dumps(entries[-1])


# ── account lifecycle ────────────────────────────────────────────────────────
class TestAccountLifecycle:
    def test_upsert_and_list(self):
        rec = ga.upsert_account(FakeCreds(), label="Personal", email="a@example.com")
        assert rec["email"] == "a@example.com"
        assert rec["label"] == "Personal"
        accts = ga.list_accounts()
        assert len(accts) == 1
        assert accts[0]["id"] == rec["id"]

    def test_token_stored_encrypted_on_disk(self):
        rec = ga.upsert_account(FakeCreds(refresh_token="TOPSECRET"), email="a@example.com")  # pragma: allowlist secret
        token_file = ga._token_path(rec["id"])
        assert token_file.exists()
        raw = token_file.read_bytes()
        if cs.protection_method() != "plaintext":
            assert b"TOPSECRET" not in raw

    def test_public_record_has_no_secrets(self):
        ga.upsert_account(FakeCreds(refresh_token="rt-secret", token="at-secret"),  # pragma: allowlist secret
                          email="a@example.com")
        blob = json.dumps(ga.list_accounts())
        for leak in ("rt-secret", "at-secret", "refresh_token", "client_secret"):
            assert leak not in blob

    def test_second_account_distinct_color_and_id(self):
        r1 = ga.upsert_account(FakeCreds(), label="Personal", email="a@example.com")
        r2 = ga.upsert_account(FakeCreds(), label="Work", email="b@example.com")
        assert r1["id"] != r2["id"]
        assert r1["color"] != r2["color"]

    def test_reconnect_same_email_is_idempotent(self):
        r1 = ga.upsert_account(FakeCreds(), label="Personal", email="a@example.com")
        r2 = ga.upsert_account(FakeCreds(), label="Personal", email="A@Example.com")
        assert r1["id"] == r2["id"]
        assert len(ga.list_accounts()) == 1

    def test_set_services_and_label(self):
        rec = ga.upsert_account(FakeCreds(), email="a@example.com")
        upd = ga.set_services(rec["id"], {"drive": False})
        assert upd["services"]["drive"] is False
        assert upd["services"]["gmail"] is True
        ren = ga.set_label(rec["id"], "Renamed")
        assert ren["label"] == "Renamed"

    def test_remove_account(self, monkeypatch):
        monkeypatch.setattr(ga, "_revoke_remote", lambda aid: True)
        rec = ga.upsert_account(FakeCreds(), email="a@example.com")
        assert ga.remove_account(rec["id"]) is True
        assert ga.list_accounts() == []
        assert not ga._token_path(rec["id"]).exists()

    def test_remove_unknown_account(self):
        assert ga.remove_account("does-not-exist") is False


# ── per-account isolation ────────────────────────────────────────────────────
class TestIsolation:
    def test_one_failed_refresh_does_not_break_others(self, monkeypatch):
        good = ga.upsert_account(FakeCreds(), label="Good", email="good@example.com")
        bad = ga.upsert_account(FakeCreds(), label="Bad", email="bad@example.com")

        def fake_creds_for(aid):
            return object() if aid == good["id"] else None

        monkeypatch.setattr(ga, "credentials_for", fake_creds_for)
        monkeypatch.setattr(ga, "_gmail_for_creds",
                            lambda creds, limit: [{"subject": "hi", "timestamp": "2026"}])
        out = ga.merged_gmail()
        assert any(m["account_id"] == good["id"] for m in out["messages"])
        assert any(e["account_id"] == bad["id"] for e in out["errors"])


# ── merged views badge their source ──────────────────────────────────────────
class TestMergedViews:
    def test_merged_gmail_badges_accounts(self, monkeypatch):
        rec = ga.upsert_account(FakeCreds(), label="Work", email="w@example.com")
        monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
        monkeypatch.setattr(ga, "_gmail_for_creds",
                            lambda creds, limit: [{"subject": "x", "timestamp": "2026-01-01"}])
        out = ga.merged_gmail()
        assert out["messages"][0]["account_label"] == "Work"
        assert out["messages"][0]["account_id"] == rec["id"]

    def test_merged_calendar_colors_events(self, monkeypatch):
        rec = ga.upsert_account(FakeCreds(), label="Home", email="h@example.com")
        monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
        monkeypatch.setattr(ga, "_calendar_for_creds",
                            lambda creds, s, e: [{"title": "Standup", "start_time": "2026-01-01T09:00"}])
        out = ga.merged_calendar()
        assert out["events"][0]["account_color"] == rec["color"]


# ── legacy migration ─────────────────────────────────────────────────────────
class TestLegacyMigration:
    def test_migrates_legacy_token_and_removes_plaintext(self, monkeypatch):
        # Seed a legacy plaintext token file where the single-account code wrote it.
        legacy = ga.GOOGLE_TOKEN_PATH
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({
            "token": "legacy-at", "refresh_token": "legacy-rt",
            "client_id": "cid.apps.googleusercontent.com", "client_secret": "sec",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ga._LEGACY_SCOPES,
        }), encoding="utf-8")
        monkeypatch.setattr(ga, "_account_email", lambda creds: "legacy@example.com")

        assert ga.has_accounts() is True
        accts = ga.list_accounts()
        assert len(accts) == 1
        assert accts[0]["label"] == "Personal"
        assert accts[0]["email"] == "legacy@example.com"
        # Plaintext legacy token must be gone after a verified encrypted copy.
        assert not legacy.exists()

    def test_no_migration_when_no_legacy(self):
        assert ga.has_accounts() is False
        assert ga.list_accounts() == []


# ── HTTP routes ──────────────────────────────────────────────────────────────
class TestRoutes:
    def test_list_endpoint(self, client):
        ga.upsert_account(FakeCreds(), label="Personal", email="a@example.com")
        resp = client.get("/api/google/accounts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert "protection" in data
        # no secrets in the response
        assert "refresh_token" not in resp.get_data(as_text=True)

    def test_connect_without_client_errors(self, client):
        """No OAuth client configured in the temp home -> clear 400, not a crash."""
        resp = client.post("/api/google/accounts/connect", json={"label": "Work"})
        assert resp.status_code == 400
        assert resp.get_json()["status"] == "error"

    def test_connect_is_rate_limited(self, client):
        for _ in range(10):
            client.post("/api/google/accounts/connect", json={})
        resp = client.post("/api/google/accounts/connect", json={})
        assert resp.status_code == 429

    def test_services_toggle_route(self, client):
        rec = ga.upsert_account(FakeCreds(), email="a@example.com")
        resp = client.post(f"/api/google/accounts/{rec['id']}/services",
                           json={"services": {"gmail": False}})
        assert resp.status_code == 200
        assert resp.get_json()["account"]["services"]["gmail"] is False

    def test_remove_route(self, client, monkeypatch):
        monkeypatch.setattr(ga, "_revoke_remote", lambda aid: True)
        rec = ga.upsert_account(FakeCreds(), email="a@example.com")
        resp = client.post(f"/api/google/accounts/{rec['id']}/remove")
        assert resp.status_code == 200
        assert resp.get_json()["removed"] == rec["id"]

    def test_audit_route_no_secrets(self, client):
        ga.upsert_account(FakeCreds(refresh_token="rt-secret"), email="a@example.com")  # pragma: allowlist secret
        resp = client.get("/api/google/accounts/audit")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "rt-secret" not in body
        assert any(e["event"] == "connect" for e in resp.get_json()["entries"])

    def test_drive_unknown_account(self, client):
        resp = client.get("/api/google/accounts/nope/drive")
        assert resp.status_code == 400

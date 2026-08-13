"""PKCE verifier persistence across the OAuth start/callback legs (docs:
fix/toolcall-integrity-v5, 2026-08-13). OAuth consent now succeeds end to
end, but token exchange failed with (invalid_grant) "Missing code
verifier": authorization_url() auto-generates a code_verifier on the START
leg's Flow instance and sends its challenge to Google, but the CALLBACK leg
rebuilds a completely fresh Flow (which never called authorization_url())
and calls fetch_token with no verifier to replay — Google refuses.

Flow.fetch_token is mocked (not the network) so these tests prove exactly
the thing that matters — was flow.code_verifier, at the moment fetch_token
is called, the SAME value the start leg generated — without needing to
simulate oauthlib/requests internals. The mock still populates
oauth2session.token so flow.credentials (used by both real callbacks
afterward) works normally.
"""
from __future__ import annotations

import time

import pytest
from google_auth_oauthlib.flow import Flow

import agent_friday.routes.google as routes_g
import agent_friday.routes.google_accounts as routes_ga

_FAKE_WEB_CLIENT = {
    "web": {
        "client_id": "123456-abcdef.apps.googleusercontent.com",
        "client_secret": "fake-secret-not-real",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    routes_ga._RL_HITS.clear()
    yield
    routes_ga._RL_HITS.clear()


@pytest.fixture
def capture_fetch_token(monkeypatch):
    """Replace Flow.fetch_token with a fake that records self.code_verifier
    at call time (this IS the bug's exact mechanism) and populates
    oauth2session.token so flow.credentials still works downstream —
    without making any real network call or depending on oauthlib/PKCE
    internals we don't control."""
    captured = {"verifiers": []}

    def fake_fetch_token(self, **kwargs):
        captured["verifiers"].append(self.code_verifier)
        self.oauth2session.token = {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_at": time.time() + 3600,
            "scope": ["https://www.googleapis.com/auth/gmail.readonly"],
        }
        return self.oauth2session.token

    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)
    return captured


class TestMultiAccountPkceVerifierPersists:
    """routes/google_accounts.py — the multi-account /connect + /callback pair."""

    def test_callback_replays_the_verifier_the_start_leg_generated(
            self, client, monkeypatch, capture_fetch_token):
        monkeypatch.setattr(routes_ga.ga, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        monkeypatch.setattr(routes_ga.ga, "upsert_account",
                            lambda creds, label='': {"email": "a@b.com", "label": label})
        generated = []
        real_authorization_url = Flow.authorization_url

        def spy_authorization_url(self, *a, **kw):
            result = real_authorization_url(self, *a, **kw)
            generated.append(self.code_verifier)
            return result

        monkeypatch.setattr(Flow, "authorization_url", spy_authorization_url)

        start = client.post("/api/google/accounts/connect", json={"label": "t"})
        assert start.status_code == 200
        state = start.get_json()["state"]
        assert generated, "authorization_url() was never called"

        cb = client.get(f"/api/google/accounts/callback?state={state}&code=fake-code")
        assert cb.status_code == 200, cb.get_data(as_text=True)
        assert len(capture_fetch_token["verifiers"]) == 1
        # The actual bug: is the verifier used at exchange time the SAME one
        # the start leg's authorization_url() generated, not None (freshly
        # rebuilt flow, never called authorization_url()) and not some other
        # mismatched value?
        assert capture_fetch_token["verifiers"][0] == generated[0]

    def test_pending_record_carries_the_verifier_generated_at_start(
            self, client, monkeypatch):
        # More direct proof of the persistence half of the fix: read the
        # pending record BEFORE the callback consumes it, and compare
        # against the flow's own code_verifier at generation time.
        monkeypatch.setattr(routes_ga.ga, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        seen_verifiers = []
        real_authorization_url = Flow.authorization_url

        def spy_authorization_url(self, *a, **kw):
            result = real_authorization_url(self, *a, **kw)
            seen_verifiers.append(self.code_verifier)
            return result

        monkeypatch.setattr(Flow, "authorization_url", spy_authorization_url)

        start = client.post("/api/google/accounts/connect", json={"label": "t"})
        state = start.get_json()["state"]
        with routes_ga._PENDING_LOCK:
            pending = dict(routes_ga._PENDING.get(state) or {})
            routes_ga._PENDING.pop(state, None)  # clean up manually, didn't hit /callback
        assert seen_verifiers, "authorization_url() was never called"
        assert pending.get("verifier") == seen_verifiers[0]

    def test_state_miss_fails_honestly_never_attempts_exchange(
            self, client, monkeypatch, capture_fetch_token):
        monkeypatch.setattr(routes_ga.ga, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        resp = client.get(
            "/api/google/accounts/callback?state=unknown-never-issued&code=fake-code")
        assert resp.status_code == 400
        body = resp.get_data(as_text=True)
        assert "expired" in body or "already used" in body or "missing" in body.lower()
        # Must never have attempted the token exchange at all.
        assert capture_fetch_token["verifiers"] == []

    def test_pending_without_a_verifier_key_also_fails_honestly(
            self, client, monkeypatch, capture_fetch_token):
        # Defends the "never proceed without the verifier" rule even for an
        # already-pending record that's missing the key (e.g. one created by
        # code from before this fix, mid-upgrade).
        monkeypatch.setattr(routes_ga.ga, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        with routes_ga._PENDING_LOCK:
            routes_ga._PENDING["legacy-state-no-verifier"] = {"label": "x", "ts": time.time()}
        resp = client.get(
            "/api/google/accounts/callback?state=legacy-state-no-verifier&code=fake-code")
        assert resp.status_code == 400
        assert capture_fetch_token["verifiers"] == []


class TestSingleAccountPkceVerifierPersists:
    """routes/google.py — the legacy single-account /auth + /auth/callback pair."""

    def test_callback_replays_the_verifier_the_start_leg_generated(
            self, client, monkeypatch, capture_fetch_token):
        monkeypatch.setattr(routes_g, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        monkeypatch.setattr(routes_g, "_write_google_token", lambda creds: True)

        start = client.get("/api/google/auth")
        assert start.status_code == 200, start.get_data(as_text=True)

        cb = client.get("/api/google/auth/callback?state=whatever&code=fake-code")
        assert cb.status_code == 200, cb.get_data(as_text=True)
        assert len(capture_fetch_token["verifiers"]) == 1
        assert capture_fetch_token["verifiers"][0]  # non-empty: a real verifier was used

    def test_session_carries_the_verifier_generated_at_start(self, client, monkeypatch):
        monkeypatch.setattr(routes_g, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        seen_verifiers = []
        real_authorization_url = Flow.authorization_url

        def spy_authorization_url(self, *a, **kw):
            result = real_authorization_url(self, *a, **kw)
            seen_verifiers.append(self.code_verifier)
            return result

        monkeypatch.setattr(Flow, "authorization_url", spy_authorization_url)
        with client.session_transaction() as sess:
            pass  # ensure a session cookie exists before the request
        client.get("/api/google/auth")
        with client.session_transaction() as sess:
            assert seen_verifiers
            assert sess.get("google_oauth_verifier") == seen_verifiers[0]

    def test_missing_verifier_in_session_fails_honestly_never_attempts_exchange(
            self, client, monkeypatch, capture_fetch_token):
        monkeypatch.setattr(routes_g, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        # No prior /api/google/auth call in this session -> no state, no verifier.
        resp = client.get("/api/google/auth/callback?state=whatever&code=fake-code")
        assert resp.status_code == 400
        body = resp.get_data(as_text=True)
        assert "state" in body.lower() or "verifier" in body.lower()
        assert capture_fetch_token["verifiers"] == []

    def test_state_present_but_verifier_missing_still_fails_honestly(
            self, client, monkeypatch, capture_fetch_token):
        # Simulates a session that has state (e.g. from before this fix
        # shipped) but no verifier — must not silently proceed.
        monkeypatch.setattr(routes_g, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        with client.session_transaction() as sess:
            sess["google_oauth_state"] = "some-state"
        resp = client.get("/api/google/auth/callback?state=some-state&code=fake-code")
        assert resp.status_code == 400
        assert capture_fetch_token["verifiers"] == []

"""API-level test for the Google OAuth redirect_uri pin (docs:
toolcall-integrity-v5, 2026-08-13) — Stephen's exact reported scenario: a
request arriving with Host: agent.friday (his hosts-file alias) must still
produce the pinned http://localhost:3000/... redirect_uri, never a
Host-derived one, since Google's secure-response-handling policy rejects any
plain-HTTP non-loopback redirect_uri outright.
"""
from __future__ import annotations

from agent_friday.services import google_accounts as ga

_FAKE_WEB_CLIENT = {
    "web": {
        "client_id": "123456-abcdef.apps.googleusercontent.com",
        "client_secret": "fake-secret-not-real",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


class TestConnectEndpointHonorsHostHeader:
    def test_agent_friday_host_still_yields_localhost_redirect(self, client, monkeypatch):
        monkeypatch.setattr(ga, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        resp = client.post(
            "/api/google/accounts/connect",
            json={"label": "test"},
            headers={"Host": "agent.friday"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["redirect_uri"] == "http://localhost:3000/api/google/accounts/callback"
        assert "agent.friday" not in data["redirect_uri"]
        assert data["client_type"] == "web"
        # The auth_url Google will actually receive must carry the SAME
        # pinned redirect_uri, url-encoded.
        assert "localhost%3A3000" in data["auth_url"] or "localhost:3000" in data["auth_url"]
        assert "agent.friday" not in data["auth_url"]

    def test_localhost_host_yields_the_same_redirect(self, client, monkeypatch):
        # Sanity: the pin isn't just "reject agent.friday" — it's the SAME
        # answer regardless of Host, including the "normal" one.
        monkeypatch.setattr(ga, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        resp = client.post(
            "/api/google/accounts/connect",
            json={"label": "test"},
            headers={"Host": "localhost:3000"},
        )
        assert resp.get_json()["redirect_uri"] == "http://localhost:3000/api/google/accounts/callback"

    def test_web_client_warning_still_present(self, client, monkeypatch):
        monkeypatch.setattr(ga, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        resp = client.post(
            "/api/google/accounts/connect",
            json={"label": "test"},
            headers={"Host": "agent.friday"},
        )
        data = resp.get_json()
        assert "warning" in data
        assert "localhost:3000" in data["warning"]
        assert "Google Cloud Console" in data["warning"]


class TestListAccountsSurfacesRedirectUri:
    def test_oauth_info_present_when_a_client_is_configured(self, client, monkeypatch):
        monkeypatch.setattr(ga, "_google_client_config",
                            lambda: (_FAKE_WEB_CLIENT, "test"))
        resp = client.get("/api/google/accounts", headers={"Host": "agent.friday"})
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["oauth"]["redirect_uri"] == "http://localhost:3000/api/google/accounts/callback"
        assert data["oauth"]["client_type"] == "web"

    def test_no_client_configured_is_graceful_not_a_500(self, client, monkeypatch):
        monkeypatch.setattr(ga, "_google_client_config", lambda: (None, None))
        resp = client.get("/api/google/accounts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["oauth"] == {}

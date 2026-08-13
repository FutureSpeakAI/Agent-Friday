"""Google OAuth redirect_uri is pinned to loopback, not derived from the
request Host header (docs: toolcall-integrity-v5, 2026-08-13).

Stephen's consent attempt died with Error 400 invalid_request: he reaches
Friday via a hosts-file alias (http://agent.friday/), and both Google
connectors previously built their "web" client_type redirect_uri from
request.host_url — Google's secure-response-handling policy rejects ANY
plain-HTTP non-loopback redirect_uri outright (checked against the literal
URI; DNS/propagation never fixes it). Mirrors mcp_oauth.py's
http://127.0.0.1:{port}/callback pattern — no request-derived host, ever,
except through an explicit settings override for a genuine HTTPS-terminated
reverse-proxy setup.
"""
from __future__ import annotations

from agent_friday.services import calendar_engine as ce
from agent_friday.services import google_accounts as ga


class TestCalendarEngineRedirectUri:
    def test_installed_client_gets_pinned_loopback(self, monkeypatch):
        monkeypatch.setattr(ce, "_load_settings", lambda: {})
        assert ce._google_redirect_uri({}, "installed") == \
            "http://localhost:3000/api/google/auth/callback"

    def test_web_client_ALSO_gets_pinned_loopback_not_request_host(self, monkeypatch):
        # This is the actual regression: previously a "web" client_type
        # derived from request.host_url, which is what broke consent.
        monkeypatch.setattr(ce, "_load_settings", lambda: {})
        assert ce._google_redirect_uri({}, "web") == \
            "http://localhost:3000/api/google/auth/callback"

    def test_no_client_type_hint_still_pins_loopback(self, monkeypatch):
        monkeypatch.setattr(ce, "_load_settings", lambda: {})
        monkeypatch.setattr(ce, "_google_client_type", lambda cfg: "web")
        assert ce._google_redirect_uri({}) == \
            "http://localhost:3000/api/google/auth/callback"

    def test_settings_override_is_honored(self, monkeypatch):
        monkeypatch.setattr(
            ce, "_load_settings",
            lambda: {"google_oauth": {"redirect_base_override": "https://friday.example.com/"}})
        assert ce._google_redirect_uri({}, "web") == \
            "https://friday.example.com/api/google/auth/callback"

    def test_empty_override_falls_back_to_pinned_loopback(self, monkeypatch):
        monkeypatch.setattr(
            ce, "_load_settings",
            lambda: {"google_oauth": {"redirect_base_override": ""}})
        assert ce._google_redirect_uri({}, "web") == \
            "http://localhost:3000/api/google/auth/callback"

    def test_missing_google_oauth_settings_key_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(ce, "_load_settings", lambda: {})
        assert ce._google_redirect_uri({}, "web")  # must not raise


class TestGoogleAccountsMultiRedirectUri:
    def test_installed_client_gets_pinned_loopback(self, monkeypatch):
        monkeypatch.setattr(ga, "_load_settings", lambda: {})
        assert ga.multi_redirect_uri({}, "installed") == \
            "http://localhost:3000/api/google/accounts/callback"

    def test_web_client_ALSO_gets_pinned_loopback_not_request_host(self, monkeypatch):
        monkeypatch.setattr(ga, "_load_settings", lambda: {})
        assert ga.multi_redirect_uri({}, "web") == \
            "http://localhost:3000/api/google/accounts/callback"

    def test_settings_override_is_honored(self, monkeypatch):
        monkeypatch.setattr(
            ga, "_load_settings",
            lambda: {"google_oauth": {"redirect_base_override": "https://friday.example.com"}})
        assert ga.multi_redirect_uri({}, "web") == \
            "https://friday.example.com/api/google/accounts/callback"

    def test_override_trailing_slash_is_stripped(self, monkeypatch):
        monkeypatch.setattr(
            ga, "_load_settings",
            lambda: {"google_oauth": {"redirect_base_override": "https://friday.example.com/"}})
        assert ga.multi_redirect_uri({}, "web") == \
            "https://friday.example.com/api/google/accounts/callback"

    def test_calendar_and_accounts_callbacks_use_different_paths(self, monkeypatch):
        # The two connectors must never share a redirect_uri — each callback
        # route only knows how to handle its own path.
        monkeypatch.setattr(ce, "_load_settings", lambda: {})
        monkeypatch.setattr(ga, "_load_settings", lambda: {})
        single = ce._google_redirect_uri({}, "web")
        multi = ga.multi_redirect_uri({}, "web")
        assert single != multi
        assert single.endswith("/api/google/auth/callback")
        assert multi.endswith("/api/google/accounts/callback")

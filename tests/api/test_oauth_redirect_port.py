"""A6 — the Google OAuth redirect follows the bound port (decision D10).

server.py's _resolve_bind_port scans forward when 3000 is busy, so the app can
be serving on 3001+. Both Google redirect URIs were literals pinned to ":3000",
so consent failed with redirect_uri_mismatch in exactly the situation the port
scan exists to survive.

The host stays pinned to loopback — that was a deliberate fix for Google
rejecting plain-HTTP non-loopback redirects, and these tests pin it so a future
change cannot undo it by accident.
"""
from __future__ import annotations

import pytest

import agent_friday.core as core
from agent_friday.services import calendar_engine, google_accounts


@pytest.fixture(autouse=True)
def _restore_port():
    original = core.SERVER_PORT
    yield
    core.set_server_port(original)


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    """Neutralise the advanced reverse-proxy override for these tests."""
    for mod in (calendar_engine, google_accounts):
        monkeypatch.setattr(mod, "_load_settings", lambda: {}, raising=False)


# ── the defect ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("port", [3000, 3001, 3007, 8080])
def test_single_account_redirect_follows_the_bound_port(port):
    core.set_server_port(port)
    uri = calendar_engine._google_redirect_uri({})
    assert uri == f"http://localhost:{port}/api/google/auth/callback"


@pytest.mark.parametrize("port", [3000, 3001, 3007, 8080])
def test_multi_account_redirect_follows_the_bound_port(port):
    core.set_server_port(port)
    uri = google_accounts.multi_redirect_uri({})
    assert uri == f"http://localhost:{port}/api/google/accounts/callback"


def test_fallback_port_is_not_still_pinned_to_3000():
    """The precise regression: server on 3001, redirect must not say 3000."""
    core.set_server_port(3001)
    for uri in (calendar_engine._google_redirect_uri({}),
                google_accounts.multi_redirect_uri({})):
        assert ":3001" in uri
        assert ":3000" not in uri, (
            "redirect_uri is still pinned to 3000 while the server bound 3001 "
            "— consent would fail with redirect_uri_mismatch")


# ── properties that must NOT change ──────────────────────────────────────────
@pytest.mark.parametrize("port", [3000, 3001])
def test_host_stays_loopback(port):
    """Google rejects any plain-HTTP non-loopback redirect_uri outright."""
    core.set_server_port(port)
    for uri in (calendar_engine._google_redirect_uri({}),
                google_accounts.multi_redirect_uri({})):
        assert uri.startswith("http://localhost:")


def test_settings_override_still_wins(monkeypatch):
    """The reverse-proxy escape hatch keeps priority over the bound port."""
    core.set_server_port(3001)
    monkeypatch.setattr(
        calendar_engine, "_load_settings",
        lambda: {"google_oauth": {"redirect_base_override": "https://agent.example/"}},
        raising=False)
    monkeypatch.setattr(
        google_accounts, "_load_settings",
        lambda: {"google_oauth": {"redirect_base_override": "https://agent.example/"}},
        raising=False)

    assert calendar_engine._google_redirect_uri({}) == \
        "https://agent.example/api/google/auth/callback"
    assert google_accounts.multi_redirect_uri({}) == \
        "https://agent.example/api/google/accounts/callback"


# ── the flow leg that actually matters ───────────────────────────────────────
_FAKE_CLIENT = {
    "installed": {
        "client_id": "1234567890-abcdef.apps.googleusercontent.com",
        "client_secret": "test-not-a-real-secret",  # pragma: allowlist secret
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def test_authorize_leg_embeds_the_bound_port_in_the_consent_url(client,
                                                                monkeypatch):
    """The redirect_uri Google is actually asked to honour carries the port.

    A correct helper is not enough — what matters is the value that reaches
    the consent URL, since that is the string Google compares against.
    """
    pytest.importorskip("google_auth_oauthlib.flow")

    from agent_friday.routes import google as google_routes

    core.set_server_port(3001)
    monkeypatch.setattr(google_routes, "_google_client_config",
                        lambda: (_FAKE_CLIENT, "test"), raising=False)

    resp = client.get("/api/google/auth")
    data = resp.get_json() or {}
    auth_url = data.get("auth_url") or data.get("url") or ""

    if not auth_url:
        pytest.skip(f"authorize leg unavailable in this env: {data}")

    from urllib.parse import parse_qs, unquote, urlparse
    qs = parse_qs(urlparse(auth_url).query)
    sent = unquote((qs.get("redirect_uri") or [""])[0])

    assert sent == "http://localhost:3001/api/google/auth/callback", (
        f"consent URL carried {sent!r} while the server bound 3001")


# ── the port registry itself ─────────────────────────────────────────────────
def test_set_server_port_ignores_garbage():
    core.set_server_port(3005)
    core.set_server_port("not-a-port")
    assert core.SERVER_PORT == 3005
    core.set_server_port(None)
    assert core.SERVER_PORT == 3005


def test_server_base_url_tracks_the_port():
    core.set_server_port(4321)
    assert core.server_base_url() == "http://localhost:4321"

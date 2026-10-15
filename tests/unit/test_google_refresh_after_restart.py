"""Gmail does not say "needs reconnecting" because Google was unreachable.

Right after a restart (or a laptop resume) the network can trail the server by
a few seconds. The first token refresh then fails with a TRANSPORT error, and
credentials_for filed every refresh failure as needs_reauth: the owner was told
to reconnect an account that was fine (field install report, Part 2 #12), and
the status stayed wrong until a later call happened to succeed. A refresh that
succeeded but could not SAVE the new token (a concurrent reader holding the
file on Windows) was filed the same way.

Now: only Google refusing the token (RefreshError, not retryable) marks
needs_reauth; an unreachable Google leaves the stored status alone and the
account reads as "checking"; a save failure after a good refresh still returns
the credential. Red on origin/main: the transport failure marks needs_reauth.
"""
from __future__ import annotations

import pytest

from agent_friday.services import google_accounts as G


class TransportError(Exception):
    """Named like google.auth.exceptions.TransportError."""


class RefreshError(Exception):
    """Named like google.auth.exceptions.RefreshError."""


class _Expiring:
    refresh_token = "present"

    def __init__(self, exc=None):
        self.exc = exc
        self.expired = True
        self.valid = False

    def refresh(self, _request):
        if self.exc is not None:
            raise self.exc
        self.expired, self.valid = False, True


@pytest.fixture
def marks(monkeypatch):
    out = []
    monkeypatch.setattr(G, "_migrate_legacy_if_needed", lambda: None)
    monkeypatch.setattr(G, "_mark_status",
                        lambda aid, status, touch_sync=False: out.append(status))
    monkeypatch.setattr(G.cs, "audit_event", lambda *a, **k: None)
    monkeypatch.setattr(G, "_load_index_status", lambda _: "connected")
    problems = getattr(G, "_LAST_PROBLEM", None)
    if isinstance(problems, dict):
        problems.clear()
    return out


def test_an_unreachable_google_does_not_mark_the_account_for_reconnecting(monkeypatch, marks):
    monkeypatch.setattr(G, "_raw_credentials", lambda _: _Expiring(TransportError("no route to host")))
    assert G.credentials_for("a1") is None
    assert "needs_reauth" not in marks, "a network blip after a restart was filed as a revoked grant"
    assert marks == []
    assert G.last_problem("a1") == "unreachable"


def test_a_retryable_refresh_error_is_also_not_a_revoked_grant(monkeypatch, marks):
    err = RefreshError("server error")
    err.retryable = True
    monkeypatch.setattr(G, "_raw_credentials", lambda _: _Expiring(err))
    assert G.credentials_for("a1") is None
    assert marks == []


def test_google_refusing_the_token_still_says_reconnect(monkeypatch, marks):
    monkeypatch.setattr(G, "_raw_credentials", lambda _: _Expiring(RefreshError("invalid_grant")))
    assert G.credentials_for("a1") is None
    assert marks == ["needs_reauth"]
    assert G.last_problem("a1") == "needs_reauth"


def test_a_good_refresh_whose_save_fails_still_returns_the_credential(monkeypatch, marks):
    creds = _Expiring()
    monkeypatch.setattr(G, "_raw_credentials", lambda _: creds)

    def locked(*_a, **_k):
        raise PermissionError("file in use")
    monkeypatch.setattr(G, "_persist_token", locked)
    assert G.credentials_for("a1") is creds
    assert "needs_reauth" not in marks
    assert G.last_problem("a1") is None


def test_gmail_reads_say_checking_not_reconnect_while_google_is_unreachable(monkeypatch, marks):
    from agent_friday.services import gmail_read
    monkeypatch.setattr(G, "_raw_credentials", lambda _: _Expiring(TransportError("dns")))
    monkeypatch.setattr(G, "_accounts_with", lambda svc: [{"id": "a1", "label": "Personal"}])
    out = gmail_read.get_thread("t1")
    assert out["status"] == "error"
    assert out["kind"] != "auth"
    assert "reconnect" not in out["error"].lower()


def test_the_connector_card_reads_checking_while_google_is_unreachable(monkeypatch, marks):
    from agent_friday.services import connectors
    monkeypatch.setattr(G, "_raw_credentials", lambda _: _Expiring(TransportError("dns")))
    monkeypatch.setattr(G, "list_accounts", lambda: [
        {"id": "a1", "label": "Personal", "email": "a@example.test",
         "health": {"state": "connected", "summary": "Connected"}}])
    st = connectors._status_for_google({})
    assert st["accounts"][0]["status"] == "checking"
    assert st["status"] == "connecting", st["detail"]
    assert "reauthorisation" not in st["detail"] and "reconnect" not in st["detail"].lower()

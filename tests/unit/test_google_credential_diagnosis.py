"""A local key problem is not a revoked Google grant.

THE BUG. Reading a Google token means decrypting it with a key derived from
FRIDAY_PASSWORD. When the process that reads it did not derive the same key the
process that wrote it did, the vault raises `IntegrityError` - "GCM auth tag
mismatch, tampered ciphertext or wrong key". `credentials_for` caught every
exception and marked the account `needs_reauth`, so a local key fault was
recorded as Google having pulled the grant.

That diagnosis has the worst possible property: its remedy appears to work.
Reconnecting rewrites the token under whatever key the current process holds,
so the account comes back - until the next process with a different key touches
it. Measured in Stephen's audit log on 2026-09-19: 11,508 of these on Sept 4th,
9,517 on the 11th, 8,109 on the 17th, and a reconnect every single morning.

The second half was fail-open. `_accounts_with` gated fetches on
`status != "needs_reauth"`, a deny-list of exactly one value, so "error",
"disconnected", "revoked" and a missing status all read as usable - in a module
whose header says health is never inferred from a record existing.
"""
from __future__ import annotations

import pytest

from agent_friday.services import google_accounts as G


# ── classification ──────────────────────────────────────────────────────────

class _Integrity(Exception):
    pass


_Integrity.__name__ = "IntegrityError"


@pytest.mark.parametrize("exc, expected", [
    (_Integrity("GCM auth tag mismatch"), "unreadable"),
    (RuntimeError("credential is vault-encrypted but FRIDAY_PASSWORD is not set"),
     "unreadable"),
    (PermissionError("denied"), "unreadable"),
    (FileNotFoundError("gone"), "unreadable"),
    (ValueError("not json"), "needs_reauth"),
])
def test_a_storage_failure_is_not_a_revoked_grant(exc, expected):
    """THE CRUX. Only errors that really mean "Google said no" may send the
    user to reconnect; a key or disk fault names itself."""
    assert G._classify_credential_error(exc) == expected


def test_unreadable_is_presented_as_a_local_problem():
    h = G.account_health({"status": "unreadable", "last_sync": None})
    assert h["healthy"] is False
    assert h["actionable"] is True
    assert "reauth" not in (h["label"] or "").lower(), \
        "the label still tells the user to reconnect, which is the wrong remedy"
    assert "unreadable" in (h["label"] or "").lower()


# ── fail closed ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [
    "needs_reauth", "revoked", "error", "disconnected", "unreadable",
    None, "", "something-new-nobody-mapped",
])
def test_no_unhealthy_account_is_offered_to_a_fetch(status, monkeypatch):
    """Every one of these except needs_reauth passed the old check."""
    monkeypatch.setattr(G, "_load_index", lambda: {"accounts": [
        {"id": "a1", "email": "x@y.z", "status": status,
         "services": {"gmail": True}},
    ]})
    assert G._accounts_with("gmail") == []


def test_a_healthy_account_is_still_offered(monkeypatch):
    """The fail-closed rewrite has to keep the working case working."""
    monkeypatch.setattr(G, "_load_index", lambda: {"accounts": [
        {"id": "a1", "email": "x@y.z", "status": "connected",
         "services": {"gmail": True}},
    ]})
    assert [r["id"] for r in G._accounts_with("gmail")] == ["a1"]


def test_a_service_switched_off_is_still_respected(monkeypatch):
    monkeypatch.setattr(G, "_load_index", lambda: {"accounts": [
        {"id": "a1", "email": "x@y.z", "status": "connected",
         "services": {"gmail": False}},
    ]})
    assert G._accounts_with("gmail") == []


def test_a_stale_but_connected_account_is_still_usable(monkeypatch):
    """`stale` is a warning about freshness, not a verdict on the grant.
    Blocking on it would take a working account offline for being quiet."""
    monkeypatch.setattr(G, "_load_index", lambda: {"accounts": [
        {"id": "a1", "email": "x@y.z", "status": "connected",
         "last_sync": "2020-01-01T00:00:00+00:00", "services": {"gmail": True}},
    ]})
    assert [r["id"] for r in G._accounts_with("gmail")] == ["a1"]


# ── the status the index ends up holding ────────────────────────────────────

def _one_account(monkeypatch, marks):
    monkeypatch.setattr(G, "_migrate_legacy_if_needed", lambda: None)
    monkeypatch.setattr(G, "_mark_status",
                        lambda aid, status, touch_sync=False: marks.append(status))
    monkeypatch.setattr(G.cs, "audit_event", lambda *a, **k: None)


def test_a_decryption_failure_records_unreadable(monkeypatch):
    marks = []
    _one_account(monkeypatch, marks)

    def boom(_):
        raise _Integrity("GCM auth tag mismatch")
    monkeypatch.setattr(G, "_raw_credentials", boom)

    assert G.credentials_for("a1") is None
    assert marks == ["unreadable"], \
        "a key fault was still filed as the user's problem at Google"


def test_a_missing_token_is_not_left_claiming_connected(monkeypatch):
    """`_raw_credentials` returns None when there is no token file. This used
    to return None while leaving the stored status alone, which is how
    "connected" survived an account that could not produce a credential."""
    marks = []
    _one_account(monkeypatch, marks)
    monkeypatch.setattr(G, "_raw_credentials", lambda _: None)

    assert G.credentials_for("a1") is None
    assert marks == ["disconnected"]


def test_an_expired_credential_with_no_refresh_token_does_say_reconnect(monkeypatch):
    """The case where needs_reauth is the RIGHT answer. A fix that made
    everything "unreadable" would be the same bug pointing the other way."""
    marks = []
    _one_account(monkeypatch, marks)

    class Dead:
        refresh_token = None
        expired = True
        valid = False
    monkeypatch.setattr(G, "_raw_credentials", lambda _: Dead())

    assert G.credentials_for("a1") is None
    assert marks == ["needs_reauth"]


class _Good:
    refresh_token = "present"
    expired = False
    valid = True


def test_a_working_credential_is_returned_and_marks_nothing(monkeypatch):
    marks = []
    _one_account(monkeypatch, marks)
    monkeypatch.setattr(G, "_load_index_status", lambda _: "connected")
    good = _Good()
    monkeypatch.setattr(G, "_raw_credentials", lambda _: good)

    assert G.credentials_for("a1") is good
    assert marks == [], "a healthy account was rewritten for no reason"


def test_a_successful_read_clears_a_stale_failure(monkeypatch):
    """THE STICKY-STATUS BUG. Only a successful *refresh* used to write
    "connected" back, so an account marked bad by one transient failure stayed
    bad for as long as its token stayed valid - no refresh was due, so nothing
    ever said otherwise, and every fetch skipped it.

    Observed 2026-09-19 right after the keystore migration: both accounts
    reading fine, the audit log full of success=true, and the connectors page
    insisting they needed reauthorising. A verdict that can only ever get worse
    is not a health check.
    """
    marks = []
    _one_account(monkeypatch, marks)
    monkeypatch.setattr(G, "_load_index_status", lambda _: "needs_reauth")
    good = _Good()
    monkeypatch.setattr(G, "_raw_credentials", lambda _: good)

    assert G.credentials_for("a1") is good
    assert marks == ["connected"]


@pytest.mark.parametrize("stale", ["needs_reauth", "unreadable", "error",
                                   "disconnected", None])
def test_any_stale_failure_is_cleared_by_a_working_read(monkeypatch, stale):
    marks = []
    _one_account(monkeypatch, marks)
    monkeypatch.setattr(G, "_load_index_status", lambda _: stale)
    monkeypatch.setattr(G, "_raw_credentials", lambda _: _Good())
    G.credentials_for("a1")
    assert marks == ["connected"]

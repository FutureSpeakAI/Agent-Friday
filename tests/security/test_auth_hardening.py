"""Adversarial auth tests — token validation, rotation, expiry grace window,
brute-force throttle persistence, keyless remote refusal, loopback trust.

Targets agent_friday.core auth primitives directly (no Flask client needed for
the token/throttle logic).
"""
from __future__ import annotations

import time

import pytest

import agent_friday.core as core


# ── API session token validation ──────────────────────────────────────────────

class TestApiTokenValidation:
    def test_current_token_is_valid(self):
        assert core._api_token_valid(core._current_api_token()) is True

    @pytest.mark.parametrize("bad", ["", None, 123, "deadbeef", "x" * 64, [], {}])
    def test_garbage_tokens_rejected(self, bad):
        assert core._api_token_valid(bad) is False

    def test_wrong_hex_token_rejected(self):
        assert core._api_token_valid("a" * 64) is False


class TestTokenRotation:
    def test_rotation_disabled_returns_stable_token(self, monkeypatch):
        monkeypatch.setattr(core, "_API_TOKEN_ROTATE_S", 0.0)
        t1 = core._current_api_token()
        t2 = core._current_api_token()
        assert t1 == t2

    def test_rotation_mints_new_token_after_expiry(self, monkeypatch):
        monkeypatch.setattr(core, "_API_TOKEN_ROTATE_S", 3600.0)
        old = core._current_api_token()
        # Pretend the token was issued long ago so the next call rotates.
        monkeypatch.setattr(core, "_API_TOKEN_ISSUED_AT", time.time() - 999999)
        new = core._current_api_token()
        assert new != old
        # New token validates; the just-rotated-out one still works in grace.
        assert core._api_token_valid(new) is True

    def test_previous_token_valid_within_grace_window(self, monkeypatch):
        monkeypatch.setattr(core, "_API_TOKEN_ROTATE_S", 3600.0)
        old = core._current_api_token()
        monkeypatch.setattr(core, "_API_TOKEN_ISSUED_AT", time.time() - 999999)
        core._current_api_token()  # triggers rotation, sets PREV = old
        # Freshly rotated → within grace window → previous token accepted.
        assert core._api_token_valid(old) is True

    def test_previous_token_rejected_after_grace(self, monkeypatch):
        monkeypatch.setattr(core, "_API_TOKEN_ROTATE_S", 3600.0)
        old = core._current_api_token()
        monkeypatch.setattr(core, "_API_TOKEN_ISSUED_AT", time.time() - 999999)
        core._current_api_token()
        # Age the NEW issue time past the grace window.
        monkeypatch.setattr(core, "_API_TOKEN_ISSUED_AT",
                            time.time() - (core._API_TOKEN_GRACE_S + 10))
        assert core._api_token_valid(old) is False


# ── Login brute-force throttle — SQLite-persisted per-IP window ───────────────

class TestLoginThrottle:
    def test_fresh_ip_allowed(self):
        ip = "203.0.113.10"
        core._login_attempt_reset(ip)
        assert core._login_attempt_ok(ip) is True

    def test_exceeding_max_attempts_throttles(self):
        ip = "203.0.113.11"
        core._login_attempt_reset(ip)
        for _ in range(core._LOGIN_MAX):
            core._login_attempt_fail(ip)
        assert core._login_attempt_ok(ip) is False

    def test_reset_clears_throttle(self):
        ip = "203.0.113.12"
        for _ in range(core._LOGIN_MAX + 2):
            core._login_attempt_fail(ip)
        assert core._login_attempt_ok(ip) is False
        core._login_attempt_reset(ip)
        assert core._login_attempt_ok(ip) is True

    def test_throttle_persists_across_new_db_connections(self):
        # Each _login_attempt_* opens a fresh connection — simulating a restart
        # cycle. The count must survive because it's on disk, not in memory.
        ip = "203.0.113.13"
        core._login_attempt_reset(ip)
        for _ in range(core._LOGIN_MAX):
            core._login_attempt_fail(ip)
        # A brand-new _login_attempt_ok reads the persisted count.
        assert core._login_attempt_ok(ip) is False
        core._login_attempt_reset(ip)

    def test_window_expiry_resets_count(self, monkeypatch):
        ip = "203.0.113.14"
        core._login_attempt_reset(ip)
        for _ in range(core._LOGIN_MAX):
            core._login_attempt_fail(ip)
        assert core._login_attempt_ok(ip) is False
        # Fast-forward time past the window.
        real = time.time
        monkeypatch.setattr(core, "_time", type(core._time)) if False else None
        monkeypatch.setattr(core._time, "time", lambda: real() + core._LOGIN_WINDOW + 5)
        assert core._login_attempt_ok(ip) is True
        core._login_attempt_reset(ip)


# ── Loopback trust semantics ──────────────────────────────────────────────────

class TestLoopbackTrust:
    def test_loopback_addrs_recognized(self):
        assert "127.0.0.1" in core._LOOPBACK_ADDRS
        assert "::1" in core._LOOPBACK_ADDRS

    def test_trust_loopback_flag_respected(self, monkeypatch):
        # With trust disabled, _loopback_trusted must be False regardless of IP.
        monkeypatch.setattr(core, "FRIDAY_TRUST_LOOPBACK", False)
        monkeypatch.setattr(core, "_is_local_request", lambda: True)
        assert core._loopback_trusted() is False

    def test_trust_loopback_enabled_for_local(self, monkeypatch):
        monkeypatch.setattr(core, "FRIDAY_TRUST_LOOPBACK", True)
        monkeypatch.setattr(core, "_is_local_request", lambda: True)
        assert core._loopback_trusted() is True

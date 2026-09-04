"""Gauntlet finding F21: the voice websockets (/ws/voice-local, /ws/live)
did not enforce the same fail-closed-when-unconfigured auth posture
core.login_required() already established for HTTP routes.

`core.login_required()`'s own fail-closed branch: when `_HTTP_AUTH_KEY` is
empty (no FRIDAY_REMOTE_KEY/FRIDAY_PASSWORD configured), a non-loopback
caller is DENIED (403) rather than let through -- per its own comment,
"a NON-loopback caller with no key configured is denied (fail-closed)
rather than allowed through." Both voice websockets' inline auth blocks
instead gated on bare `FRIDAY_PASSWORD` directly: `if FRIDAY_PASSWORD and
not authenticated and not loopback and not ui_tok: deny`. With no password
configured at all, that whole expression short-circuited False and the
deny block was skipped ENTIRELY -- not even checking loopback -- so the
socket accepted ANY connection unconditionally, non-loopback included.

Fixed by extracting `_ws_auth_ok(ui_tok_ok)`, a real module-level function
both websockets now call, that mirrors login_required()'s actual branch
structure using `_HTTP_AUTH_KEY` (not bare FRIDAY_PASSWORD, so a
FRIDAY_REMOTE_KEY-only configuration is covered too). Being an
independently-callable function (unlike the two closures that call it),
this is a real behavioral test of the auth decision itself, plus a
source-position check that both `ws_voice_local` and `ws_live` actually
call it at their gate (not just that the helper exists and is correct in
isolation).
"""
from __future__ import annotations

import inspect

import agent_friday.routes.voice as vr


class _FakeSession(dict):
    """Minimal stand-in for Flask's `session` proxy — `.get()` is all
    `_ws_auth_ok` needs."""


class TestWsAuthOkMirrorsLoginRequired:
    """Direct behavioral coverage of `_ws_auth_ok`, matching
    `core.login_required()`'s branch structure case for case."""

    def test_no_key_configured_and_loopback_is_allowed(self, monkeypatch):
        monkeypatch.setattr(vr, "_HTTP_AUTH_KEY", "")
        monkeypatch.setattr(vr, "_loopback_trusted", lambda: True)
        monkeypatch.setattr(vr, "session", _FakeSession())
        assert vr._ws_auth_ok(ui_tok_ok=False) is True

    def test_no_key_configured_and_not_loopback_is_denied(self, monkeypatch):
        """THE regression this finding is about: before the fix, this exact
        case (no password/remote-key set at all, caller not on loopback)
        was accepted unconditionally because the old gate's condition
        short-circuited False and skipped the deny block without even
        checking loopback."""
        monkeypatch.setattr(vr, "_HTTP_AUTH_KEY", "")
        monkeypatch.setattr(vr, "_loopback_trusted", lambda: False)
        monkeypatch.setattr(vr, "session", _FakeSession())
        assert vr._ws_auth_ok(ui_tok_ok=False) is False

    def test_no_key_configured_ephemeral_ui_token_does_not_bypass_it(self, monkeypatch):
        """login_required()'s own fail-closed branch does not consult the
        ephemeral UI token either -- only loopback. `_ws_auth_ok` must match
        that exactly, not invent a looser rule for websockets."""
        monkeypatch.setattr(vr, "_HTTP_AUTH_KEY", "")
        monkeypatch.setattr(vr, "_loopback_trusted", lambda: False)
        monkeypatch.setattr(vr, "session", _FakeSession())
        assert vr._ws_auth_ok(ui_tok_ok=True) is False

    def test_key_configured_not_loopback_no_session_no_token_is_denied(self, monkeypatch):
        monkeypatch.setattr(vr, "_HTTP_AUTH_KEY", "some-remote-key")
        monkeypatch.setattr(vr, "_loopback_trusted", lambda: False)
        monkeypatch.setattr(vr, "session", _FakeSession())
        assert vr._ws_auth_ok(ui_tok_ok=False) is False

    def test_key_configured_valid_ui_token_is_allowed(self, monkeypatch):
        monkeypatch.setattr(vr, "_HTTP_AUTH_KEY", "some-remote-key")
        monkeypatch.setattr(vr, "_loopback_trusted", lambda: False)
        monkeypatch.setattr(vr, "session", _FakeSession())
        assert vr._ws_auth_ok(ui_tok_ok=True) is True

    def test_key_configured_authenticated_session_is_allowed(self, monkeypatch):
        monkeypatch.setattr(vr, "_HTTP_AUTH_KEY", "some-remote-key")
        monkeypatch.setattr(vr, "_loopback_trusted", lambda: False)
        monkeypatch.setattr(vr, "session", _FakeSession(authenticated=True))
        assert vr._ws_auth_ok(ui_tok_ok=False) is True

    def test_key_configured_and_loopback_is_allowed_regardless(self, monkeypatch):
        monkeypatch.setattr(vr, "_HTTP_AUTH_KEY", "some-remote-key")
        monkeypatch.setattr(vr, "_loopback_trusted", lambda: True)
        monkeypatch.setattr(vr, "session", _FakeSession())
        assert vr._ws_auth_ok(ui_tok_ok=False) is True


def _body_of(fn_name: str) -> str:
    src = inspect.getsource(vr)
    i_def = src.index(f"def {fn_name}(")
    body = src[i_def:]
    i_next_def = body.index("\n    def ", 1) if "\n    def " in body[1:] else len(body)
    return body[:i_next_def]


class TestBothWebsocketsActuallyCallTheFixedGate:
    """The behavioral tests above prove `_ws_auth_ok` itself is correct.
    This proves both websocket routes actually call it at their gate,
    rather than the fixed helper existing unused while the closures still
    run their own (broken) inline check -- the exact class of gap this
    audit's own precedent (test_ws_live_respects_local_only.py) warns
    about: pin the fix's own call, not just that a helper with the right
    name exists somewhere in the file."""

    def test_ws_voice_local_calls_ws_auth_ok_at_its_gate(self):
        body = _body_of("ws_voice_local")
        assert "if not _ws_auth_ok(_ui_tok_ok):" in body, (
            "ws_voice_local's auth gate does not call the fixed _ws_auth_ok "
            "helper -- it may still be running the old fail-open-when-"
            "unconfigured inline check"
        )

    def test_ws_live_calls_ws_auth_ok_at_its_gate(self):
        body = _body_of("ws_live")
        assert "if not _ws_auth_ok(_ui_tok_ok):" in body, (
            "ws_live's auth gate does not call the fixed _ws_auth_ok helper "
            "-- it may still be running the old fail-open-when-unconfigured "
            "inline check"
        )

    def test_bare_friday_password_gate_condition_is_gone(self):
        """The specific broken condition shape this finding names must not
        exist anywhere in the file any more -- guards against a partial
        revert that restores the old inline check at one site while the
        helper stays defined but unused."""
        src = inspect.getsource(vr)
        assert "if (FRIDAY_PASSWORD and not session.get(\"authenticated\")" not in src

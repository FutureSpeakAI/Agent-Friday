"""
Fable 5 adversarial-review regression tests.

Covers the two fail-OPEN security holes patched during the review:

  1. Egress gate errors must FAIL CLOSED — if seal_outbound() raises, the cloud
     provider call must be BLOCKED, never sent with the un-sealed payload.
  2. Remote (non-loopback) requests must be DENIED when no FRIDAY_REMOTE_KEY /
     FRIDAY_PASSWORD is configured — an unset key must not open the whole API.
"""
import os
import sys
import types
import pathlib

import pytest

# Make src importable without an editable install.
_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("FRIDAY_TESTING", "1")


# ── 1. Egress gate fail-closed on error ───────────────────────────────────────
class _FakeMessages:
    def __init__(self):
        self.called = False

    def create(self, **kwargs):
        self.called = True
        raise AssertionError(
            "cloud send happened despite egress gate error — FAIL-OPEN leak"
        )


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_call_claude_fails_closed_when_gate_raises(monkeypatch):
    from agent_friday.services import model_router as mr

    fake = _FakeClient()
    monkeypatch.setattr(mr, "get_anthropic_client", lambda: fake)
    monkeypatch.setattr(mr, "_load_settings", lambda: {"orchestrator_model": "claude-x"})

    # Force the gate to raise, simulating an import/classifier failure.
    import agent_friday.services.egress_gate as gate

    def _boom(payload, provider, *a, **k):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(gate, "seal_outbound", _boom)

    with pytest.raises(RuntimeError) as ei:
        mr._call_claude([{"role": "user", "content": "hi"}], system="s")

    assert "egress gate" in str(ei.value).lower() or "cloud send blocked" in str(ei.value).lower()
    # The critical invariant: the network create() was NEVER reached.
    assert fake.messages.called is False


def test_call_claude_sends_when_gate_ok(monkeypatch):
    """Sanity: a working gate does NOT block the (stubbed) send."""
    from agent_friday.services import model_router as mr

    class _OKMessages:
        def create(self, **kwargs):
            msg = types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="pong")],
                usage=None,
            )
            return msg

    fake = types.SimpleNamespace(messages=_OKMessages())
    monkeypatch.setattr(mr, "get_anthropic_client", lambda: fake)
    monkeypatch.setattr(mr, "_load_settings", lambda: {"orchestrator_model": "claude-x"})

    import agent_friday.services.egress_gate as gate
    monkeypatch.setattr(gate, "seal_outbound", lambda payload, provider, *a, **k: payload)

    out = mr._call_claude([{"role": "user", "content": "ping"}], system="s")
    assert out == "pong"


# ── 2. Remote auth fail-closed when no key configured ─────────────────────────
def test_remote_request_denied_without_key(monkeypatch):
    import agent_friday.core as core

    # Simulate: no remote key, request is NOT loopback.
    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "", raising=False)
    monkeypatch.setattr(core, "_loopback_trusted", lambda: False)

    app = core.app
    with app.test_request_context("/api/health", method="GET",
                                  headers={"Accept": "application/json"}):
        rv = core.check_auth()
    assert rv is not None, "check_auth must NOT allow a keyless remote request through"
    # A 403 (or 401) response object/tuple — never None (which means 'allow').
    status = rv[1] if isinstance(rv, tuple) else getattr(rv, "status_code", None)
    assert status in (401, 403)


def test_loopback_still_trusted_without_key(monkeypatch):
    import agent_friday.core as core

    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "", raising=False)
    monkeypatch.setattr(core, "_loopback_trusted", lambda: True)

    app = core.app
    with app.test_request_context("/api/health", method="GET"):
        rv = core.check_auth()
    # Loopback trusted → returns None (allow).
    assert rv is None

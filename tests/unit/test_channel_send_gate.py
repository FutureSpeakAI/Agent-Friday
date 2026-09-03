"""Unit tests for security-boundary.md §19 row 6: channel adapters' public
`send()` had no gate call — only the reply path (`manager.handle_incoming`
-> `gate_reply` -> `self.send()`) was gated. Any OTHER caller of
`adapter.send()` bypassed the gate entirely; `manager.test_channel()` (wired
to `routes/channels.py`'s `/test` endpoint, docstring in
discord_bridge.py:96 literally says "used by the /test endpoint") is exactly
that other caller — it passes a caller-supplied `text` straight to
`a.send()`.

The fix moves the gate into `ChannelAdapter.send()` itself (base.py), which
now gates and dispatches to each adapter's `_send_raw()`. This closes the
`test_channel` bypass without route-level changes, and makes any future
adapter safe by construction.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.services.channels.base import ChannelAdapter
from agent_friday.services import egress_gate

TIER2_SECRET = "my custody hearing is on the 14th, my SSN is 123-45-6789"  # pragma: allowlist secret


class _FakeAdapter(ChannelAdapter):
    name = "fake"

    def __init__(self):
        super().__init__()
        self.sent = []

    def _poll_once(self):
        pass

    def _send_raw(self, chat_id: str, text: str):
        self.sent.append((chat_id, text))
        return {"ok": True}


def test_send_gates_before_dispatching_to_the_adapter(monkeypatch):
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda *a, **k: (_ for _ in ()).throw(
                            egress_gate.NeverSendBlocked("blocked for a test")))
    a = _FakeAdapter()
    a.send("12345", TIER2_SECRET)
    assert a.sent, "send() should still dispatch something (a withheld notice), not silently drop"
    assert TIER2_SECRET not in a.sent[0][1]


def test_send_calls_the_real_gate(monkeypatch):
    calls = []
    monkeypatch.setattr(
        egress_gate, "_gate_text",
        lambda text, provider, field, log_path=None:
            calls.append((text, provider, field)) or text)
    a = _FakeAdapter()
    a.send("12345", "hello there")
    assert calls, "send() must actually call the gate, not merely pass through"
    text, provider, field = calls[0]
    assert text == "hello there"
    assert provider == "channel_fake"


def test_benign_reply_still_reaches_the_adapter(monkeypatch):
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda text, provider, field, log_path=None: text)
    a = _FakeAdapter()
    a.send("12345", "your event starts at 3pm")
    assert a.sent == [("12345", "your event starts at 3pm")]


def test_direct_send_bypass_reproduction_is_falsifiable(monkeypatch):
    """Reproduce the pre-fix shape: an adapter's send() dispatched straight
    to the network call with no gate call in between — the exact shape of
    manager.test_channel()'s `a.send(str(chat_id), text)`."""
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda *a, **k: (_ for _ in ()).throw(
                            egress_gate.NeverSendBlocked("would have blocked")))

    class _OldStyleAdapter:
        def __init__(self):
            self.sent = []

        def send(self, chat_id, text):
            # pre-fix: no gate call anywhere in this method
            self.sent.append((chat_id, text))
            return {"ok": True}

    old = _OldStyleAdapter()
    old.send("12345", TIER2_SECRET)
    assert old.sent and TIER2_SECRET in old.sent[0][1], (
        "the reproduction of the pre-fix adapter should leak the secret; it "
        "did not, so this comparison is not meaningful"
    )

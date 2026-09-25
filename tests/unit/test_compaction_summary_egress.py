"""A compaction summary written by Claude leaves through the same gate as a round.

The Anthropic loop's summarizer called client.messages.create directly: no
egress seal, no spending cap, no metering. A raw SSN in the middle of a
transcript reached Anthropic through the summary request while the same text
on a normal round arrived scrubbed.
"""
from __future__ import annotations

import types

import pytest

from agent_friday.services import compaction

SSN = "123-45-6789"  # pragma: allowlist secret
TEXT = "user: my SSN is %s and my bank account number is 987654321\nassistant: noted" % SSN  # pragma: allowlist secret


class _Client:
    def __init__(self):
        self.sent = []
        self.messages = self

    def create(self, **kw):
        self.sent.append(kw)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="GOAL: keep going")],
            usage=types.SimpleNamespace(input_tokens=900, output_tokens=40))


def test_the_summary_request_is_sealed_by_the_egress_gate():
    client = _Client()
    out = compaction.claude_summarizer(client, "claude-opus-5-5")(TEXT)
    assert out == "GOAL: keep going"
    assert client.sent, "no request was sent"
    assert SSN not in repr(client.sent[0])


def test_the_summary_request_goes_through_the_shared_chokepoint(monkeypatch):
    from agent_friday.services import model_router as mr
    seen = []

    def spy(payload, provider):
        seen.append(provider)
        return payload
    monkeypatch.setattr(mr, "_seal_or_block", spy)
    compaction.claude_summarizer(_Client(), "claude-opus-5-5")(TEXT)
    assert seen == ["anthropic"]


def test_a_blocked_send_is_no_summary_and_nothing_is_sent(monkeypatch):
    """The spending cap and the fail-closed gate both raise in the chokepoint."""
    from agent_friday.services import model_router as mr

    def blocked(payload, provider):
        raise RuntimeError("spending cap reached")
    monkeypatch.setattr(mr, "_seal_or_block", blocked)
    client = _Client()
    assert compaction.claude_summarizer(client, "claude-opus-5-5")(TEXT) == ""
    assert client.sent == []


def test_the_summary_call_is_metered_and_charged(monkeypatch):
    from agent_friday.services import cost_meter
    metered, charged = [], []
    monkeypatch.setattr(cost_meter, "meter",
                        lambda provider, model, usage, **kw: metered.append(
                            (provider, model, usage.input_tokens, kw.get("kind"))) or 0.02)
    summarize = compaction.claude_summarizer(
        _Client(), "claude-opus-5-5", on_cost=lambda usd: charged.append(usd))
    summarize(TEXT)
    assert metered == [("anthropic", "claude-opus-5-5", 900, "compaction")]
    assert charged == [0.02]

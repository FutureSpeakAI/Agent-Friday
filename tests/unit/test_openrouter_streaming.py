"""Streaming over the OpenAI-compatible transport (OpenRouter).

Streaming is a property of the WIRE. These tests pin the one thing that makes
that safe: the reassembled dict is shape-identical to a blocking response, so
the agentic loop, the cost ledger and per-message attribution above the
transport never learn the difference.
"""
from __future__ import annotations

import json

from agent_friday.services.model_router import _consume_sse_completion


class _FakeResp:
    """Minimal stand-in for a streamed requests.Response."""

    def __init__(self, lines):
        self._lines = lines
        self.headers = {"Content-Type": "text/event-stream"}

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)


def _sse(*chunks):
    return ["data: " + json.dumps(c) for c in chunks] + ["data: [DONE]"]


def test_content_deltas_assemble_in_order_and_fire_callback():
    seen = []
    r = _FakeResp(_sse(
        {"model": "z-ai/glm-5.3", "choices": [{"delta": {"content": "Hel"}}]},
        {"model": "z-ai/glm-5.3", "choices": [{"delta": {"content": "lo, "}}]},
        {"model": "z-ai/glm-5.3",
         "choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]},
    ))
    out = _consume_sse_completion(r, on_delta=seen.append)
    assert out["choices"][0]["message"]["content"] == "Hello, world"
    assert out["choices"][0]["finish_reason"] == "stop"
    # Arrived in pieces — this is the whole point of the requirement.
    assert seen == ["Hel", "lo, ", "world"]


def test_keepalive_comments_do_not_break_the_stream():
    """OpenRouter emits ': OPENROUTER PROCESSING' while waiting upstream."""
    lines = [": OPENROUTER PROCESSING", ""] + _sse(
        {"choices": [{"delta": {"content": "ok"}}]})
    out = _consume_sse_completion(_FakeResp(lines))
    assert out["choices"][0]["message"]["content"] == "ok"


def test_fragmented_tool_calls_reassemble():
    r = _FakeResp(_sse(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1",
             "function": {"name": "get_weather", "arguments": '{"ci'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'ty":"Oslo"}'}}]},
            "finish_reason": "tool_calls"}]},
    ))
    out = _consume_sse_completion(r)
    tc = out["choices"][0]["message"]["tool_calls"]
    assert len(tc) == 1
    assert tc[0]["id"] == "call_1"
    assert tc[0]["function"]["name"] == "get_weather"
    assert json.loads(tc[0]["function"]["arguments"]) == {"city": "Oslo"}


def test_streamed_model_is_the_ROUTED_model_not_the_requested_one():
    """The attribution guarantee for `openrouter/auto`.

    We send `openrouter/auto`; the chunks name the model that actually
    answered. If the transport dropped it, the cost ledger would book every
    auto-routed turn to one opaque bucket and the per-model breakdown — the
    most valuable panel in Cost & Usage — would go blank the moment auto
    routing is switched on.
    """
    r = _FakeResp(_sse(
        {"model": "anthropic/claude-sonnet-5",
         "choices": [{"delta": {"content": "hi"}}]},
        {"model": "anthropic/claude-sonnet-5", "choices": [{"delta": {}}],
         "usage": {"prompt_tokens": 11, "completion_tokens": 3}},
    ))
    out = _consume_sse_completion(r)
    assert out["model"] == "anthropic/claude-sonnet-5"
    assert out["usage"]["prompt_tokens"] == 11


def test_malformed_chunk_is_skipped_not_fatal():
    lines = ["data: {not json", ""] + _sse({"choices": [{"delta": {"content": "x"}}]})
    out = _consume_sse_completion(_FakeResp(lines))
    assert out["choices"][0]["message"]["content"] == "x"

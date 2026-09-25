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


# ── Reasoning deltas ─────────────────────────────────────────────────────────
#
# A reasoning seat splits its output across `content` and `reasoning_content`,
# and `max_tokens` is spent on both. A reassembler that reads only `content`
# turns a turn that spent its whole budget thinking into an EMPTY message — and
# every layer above calls it empty, then "Max iters", then silently serves an
# un-curated Front Page. A bonsai2:27b llama-server seat emits 16 content
# deltas and 49 reasoning_content deltas for one short prompt.

def test_reasoning_deltas_are_kept_and_not_mixed_into_content():
    r = _FakeResp(_sse(
        {"choices": [{"delta": {"reasoning_content": "They want "}}]},
        {"choices": [{"delta": {"reasoning_content": "only JSON."}}]},
        {"choices": [{"delta": {"content": '{"ok":'}}]},
        {"choices": [{"delta": {"content": " 1}"}, "finish_reason": "stop"}]},
    ))
    msg = _consume_sse_completion(r)["choices"][0]["message"]
    # The scratchpad survives the transport …
    assert msg["reasoning_content"] == "They want only JSON."
    # … and stays out of the answer, which is what reaches chat bubbles.
    assert msg["content"] == '{"ok": 1}'


def test_openrouter_spells_it_reasoning_and_that_is_kept_too():
    r = _FakeResp(_sse(
        {"choices": [{"delta": {"reasoning": "thinking…"}}]},
        {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]},
    ))
    msg = _consume_sse_completion(r)["choices"][0]["message"]
    assert msg["reasoning_content"] == "thinking…"
    assert msg["content"] == "hi"


def test_a_turn_that_only_thought_is_distinguishable_from_a_blank_one():
    """The Front Page failure shape: budget spent thinking, no answer.

    Without the reasoning field this is byte-identical to a model that said
    nothing at all, so the failure reads as 'empty'."""
    thought_only = _consume_sse_completion(_FakeResp(_sse(
        {"choices": [{"delta": {"reasoning_content": "a" * 400}}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
    )))["choices"][0]
    truly_blank = _consume_sse_completion(_FakeResp(_sse(
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
    )))["choices"][0]

    assert thought_only["message"]["content"] == ""
    assert truly_blank["message"]["content"] == ""
    assert thought_only["finish_reason"] == truly_blank["finish_reason"] == "length"
    # The only thing that tells them apart — and now it is there.
    assert thought_only["message"].get("reasoning_content")
    assert "reasoning_content" not in truly_blank["message"]


def test_no_reasoning_means_no_key_so_existing_callers_see_no_change():
    r = _FakeResp(_sse(
        {"choices": [{"delta": {"content": "plain"}, "finish_reason": "stop"}]},
    ))
    assert _consume_sse_completion(r)["choices"][0]["message"] == {
        "role": "assistant", "content": "plain"}

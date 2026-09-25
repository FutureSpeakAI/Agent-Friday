"""The local seat decides and acts, and its token counter tells the truth.

Two findings from the tail of Stephen's 19-minute resume turn, 2026-09-25.

THE REASONING WAS SOUND AND NEVER LANDED. The plan was right — Higgsfield image,
`save_output`, an HTML resume in the creations folder, a relative path, offer
PDF, and a correct refusal to open files without permission. Then it kept going:
"Actually… Hmm… wait… Let me reconsider", on whether to base64-embed a
background image or link it, whether to offer PDF export, whether a brand stamp
applied. It never emitted a tool call. Every one of those is a small, reversible
choice with a reasonable default; the deliberation cost more than it bought.

THE COUNTER READ "0/0 tk". Not a lie — llama-server omits `usage` from an SSE
stream unless asked, so there was nothing to count.
"""

import json

import pytest

from agent_friday.services import turn_budget as tb


# ── (b) bounded deliberation ────────────────────────────────────────────────

def test_the_nudge_targets_re_deciding_not_thinking():
    """It must not read as 'think less' — a hard problem still earns hard
    thinking. It names the specific trap instead."""
    n = tb.DELIBERATION_NUDGE
    low = n.lower()
    assert "as hard as the problem genuinely deserves" in low
    for trap in ("actually", "wait", "reconsider"):
        assert trap in low, "the nudge does not name the trap: %r" % trap
    assert "reversible" in low


def test_the_nudge_says_what_to_do_instead():
    low = tb.DELIBERATION_NUDGE.lower()
    assert "say which you chose in one line" in low
    assert "act" in low
    assert "after the work exists" in low
    assert "tool call or the answer" in low


def test_the_nudge_does_not_forbid_deliberation_outright():
    """A blanket 'stop thinking' would cost exactly the capability that makes
    this seat worth having."""
    low = tb.DELIBERATION_NUDGE.lower()
    for banned in ("do not think", "don't think", "never think",
                   "stop thinking", "no reasoning"):
        assert banned not in low, "the nudge suppresses reasoning: %r" % banned


def test_a_reasoning_seat_gets_the_nudge():
    import inspect
    from agent_friday.services import model_router as mr
    src = inspect.getsource(mr._call_ollama)
    assert "DELIBERATION_NUDGE" in src
    assert "looks_like_reasoning_model" in src


def test_the_nudge_rides_the_seat_notice_so_both_transports_see_it():
    """A local model may be served by Ollama or by a llama-server we own. The
    notice is the one thing both paths carry."""
    import inspect
    from agent_friday.services import model_router as mr
    src = inspect.getsource(mr._call_ollama)
    i = src.index("DELIBERATION_NUDGE")
    assert "_seat_notice" in src[max(0, i - 400):i + 80]


def test_an_ordinary_local_seat_does_not_get_it(monkeypatch):
    """gemma3:4b does not deliberate itself into silence; the nudge is prompt
    budget it would pay for nothing."""
    assert tb.looks_like_reasoning_model("gemma3:4b") is False


# ── (c) the token counter ───────────────────────────────────────────────────

def _sse(chunks):
    """A fake streamed response, shaped like requests' iter_lines output."""
    class _R:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}

        def iter_lines(self, *a, **k):
            # The consumer asks for decoded lines, so hand back str when it
            # does -- the real requests object honours decode_unicode here.
            dec = k.get("decode_unicode") or (a[1] if len(a) > 1 else False)
            for c in chunks:
                line = "data: " + json.dumps(c)
                yield line if dec else line.encode("utf-8")
            yield "data: [DONE]" if dec else b"data: [DONE]"
    return _R()


def _delta(text):
    return {"choices": [{"delta": {"content": text}, "index": 0}]}


def test_usage_is_taken_from_the_stream_when_the_server_sends_it():
    from agent_friday.services.model_router import _consume_sse_completion
    out = _consume_sse_completion(_sse([
        _delta("hello"),
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 11, "completion_tokens": 7}},
    ]))
    assert out["usage"]["prompt_tokens"] == 11
    assert out["usage"]["completion_tokens"] == 7


def test_llama_cpp_timings_become_usage_when_no_usage_arrives():
    """THE 0/0 BUG. llama-server omits `usage` from a stream unless asked, but
    it always reports its own counts as `timings` on the last chunk."""
    from agent_friday.services.model_router import _consume_sse_completion
    out = _consume_sse_completion(_sse([
        _delta("hello"),
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "timings": {"prompt_n": 10709, "predicted_n": 4096,
                     "prompt_ms": 400.0}},
    ]))
    assert out["usage"]["prompt_tokens"] == 10709
    assert out["usage"]["completion_tokens"] == 4096
    assert out["usage"]["total_tokens"] == 10709 + 4096
    assert "timings" in out["usage"]["_source"]


def test_a_real_usage_block_is_not_overwritten_by_timings():
    from agent_friday.services.model_router import _consume_sse_completion
    out = _consume_sse_completion(_sse([
        _delta("x"),
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 2},
         "timings": {"prompt_n": 999, "predicted_n": 999}},
    ]))
    assert out["usage"]["prompt_tokens"] == 1
    assert out["usage"].get("_source") is None


def test_no_usage_and_no_timings_still_returns_a_reply():
    """A server that reports neither must not break the turn."""
    from agent_friday.services.model_router import _consume_sse_completion
    out = _consume_sse_completion(_sse([
        _delta("hi"), {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]))
    assert out["choices"][0]["message"]["content"] == "hi"
    assert "usage" not in out


@pytest.mark.parametrize("partial", [
    {"prompt_n": 42}, {"predicted_n": 7},
])
def test_half_a_timings_block_is_still_better_than_zero(partial):
    from agent_friday.services.model_router import _consume_sse_completion
    out = _consume_sse_completion(_sse([
        _delta("x"),
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "timings": partial},
    ]))
    tot = out["usage"]["prompt_tokens"] + out["usage"]["completion_tokens"]
    assert tot == sum(partial.values())


def test_the_stream_asks_for_usage():
    """`stream_options.include_usage` is the OpenAI-standard way to ask, and
    asking is what was missing."""
    import inspect
    from agent_friday.services import model_router as mr
    src = inspect.getsource(mr._call_openai)
    assert 'setdefault("stream_options", {})["include_usage"] = True' in src
    i = src.index("include_usage")
    assert 'payload.get("stream")' in src[max(0, i - 300):i], (
        "usage is requested even when not streaming")

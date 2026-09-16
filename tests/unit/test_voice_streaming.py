"""Streaming from the seat into the voice turn (clean-sheet §4.2 mind contract,
§4.4 prefill verification).

* `_consume_sse_completion` forwards content deltas and now carries the
  seat's `timings` (llama-server `prompt_n`) on the assembled response.
* `_generate_agent(on_text_delta=...)` scopes the delta sink to the call.
* The final round streams deltas; a tool-call round streams only its
  announcement — the markup is what the session's DeltaFilter removes.
"""
import json

from agent_friday.services import model_router as mr


class _SSE:
    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


def _chunk(content=None, tool=None, finish=None, timings=None, usage=None):
    d = {"choices": [{"delta": {}, "finish_reason": finish}], "model": "seat"}
    if content is not None:
        d["choices"][0]["delta"]["content"] = content
    if tool is not None:
        d["choices"][0]["delta"]["tool_calls"] = [tool]
    if timings:
        d["timings"] = timings
    if usage:
        d["usage"] = usage
    return "data: " + json.dumps(d)


def test_sse_consumer_forwards_deltas_and_carries_timings():
    got = []
    resp = _SSE([_chunk("Pulling "), _chunk("that up now."), ": keepalive",
                 _chunk(finish="stop", timings={"prompt_n": 412, "predicted_n": 5},
                        usage={"prompt_tokens": 412, "completion_tokens": 5}),
                 "data: [DONE]"])
    out = mr._consume_sse_completion(resp, on_delta=got.append)
    assert got == ["Pulling ", "that up now."]
    assert out["choices"][0]["message"]["content"] == "Pulling that up now."
    assert out["timings"]["prompt_n"] == 412
    assert out["usage"]["prompt_tokens"] == 412


def test_streaming_final_round_only():
    """A tool round streams its announcement and nothing after the call; the
    final round streams its deltas. Rendered through the same consumer the
    real transport uses, with the session's DeltaFilter on the far end."""
    from agent_friday.services.voice_session import DeltaFilter
    f = DeltaFilter()
    heard = []
    # round 1: announcement, then the channel-format tool call in the text
    r1 = _SSE([_chunk("Pulling that up now. "),
               _chunk("<|tool_call>call:query_calendar{}<tool_call|>"),
               _chunk(finish="stop"), "data: [DONE]"])
    mr._consume_sse_completion(r1, on_delta=lambda d: heard.append(f.feed(d)))
    # round 2: the final answer
    r2 = _SSE([_chunk("Your morning "), _chunk("is clear."), _chunk(finish="stop"),
               "data: [DONE]"])
    mr._consume_sse_completion(r2, on_delta=lambda d: heard.append(f.feed(d)))
    heard.append(f.flush())
    text = "".join(heard)
    assert text == "Pulling that up now. Your morning is clear."
    assert "tool_call" not in text


def test_generate_agent_scopes_the_delta_sink_to_the_call(monkeypatch):
    """on_text_delta reaches the transport through DELTA_SINK for the duration
    of the call and is gone afterwards."""
    import agent_friday.services.agent as ag
    seen = {"inside": None}

    def fake_ollama(messages, system=None, model=None, **kw):
        sink = mr.DELTA_SINK.get()
        seen["inside"] = sink
        if sink:
            sink("hel")
            sink("lo")
        return "hello", []
    monkeypatch.setattr(ag, "_call_ollama", fake_ollama)
    monkeypatch.setattr(ag, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})

    class _R:
        def route(self, messages, task_context=None):
            return {"provider": "local", "model": "seat-x"}
    monkeypatch.setattr("agent_friday.routing.model_router.get_router", lambda cfg=None: _R())
    monkeypatch.setattr("agent_friday.services.demo_mode.is_demo", lambda: False)
    got = []
    text, trace = ag._generate_agent([{"role": "user", "content": "hi"}],
                                     system="s", on_text_delta=got.append)
    assert text == "hello"
    assert got == ["hel", "lo"]
    assert seen["inside"] == got.append           # bound methods compare, not `is`
    assert mr.DELTA_SINK.get() is None            # scoped: nothing leaks


def test_timings_sink_receives_the_seats_timings():
    """The transport publishes `timings` to TIMINGS_SINK; the voice session
    records prompt_n as prefill_tokens (the §4.4 acceptance number)."""
    got = []
    tok = mr.TIMINGS_SINK.set(got.append)
    try:
        resp = mr._consume_sse_completion(
            _SSE([_chunk("ok"), _chunk(finish="stop", timings={"prompt_n": 1500}),
                  "data: [DONE]"]))
        sink = mr.TIMINGS_SINK.get()
        if sink and resp.get("timings"):
            sink(resp["timings"])
    finally:
        mr.TIMINGS_SINK.reset(tok)
    assert got == [{"prompt_n": 1500}]
    assert mr.TIMINGS_SINK.get() is None

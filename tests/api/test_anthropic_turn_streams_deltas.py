"""An Anthropic chat turn streams its text to the UI as it is written.

The local and OpenAI transports publish every text delta to
`model_router.DELTA_SINK`, and /api/chat/stream carries the deltas to the
browser. The Anthropic tool loop called `messages.create` and showed nothing
until the whole answer was back. It now opens `messages.stream`, pushes each
text delta to the sink as it arrives, and takes the final message from the
stream, so everything after the call (usage, trace, signed thinking blocks,
tool rounds) sees the same response object it always did.

Rules:
  * only text deltas reach the sink; thinking never does;
  * a client whose `messages` has no `stream` takes `create` and emits nothing;
  * a sink that raises cannot break the turn.
"""
from __future__ import annotations

import types

import agent_friday.services.agent as ag
import agent_friday.services.model_router as smr


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=5)


class _Delta:
    """One `content_block_delta` event, shaped like the SDK's."""

    def __init__(self, kind, **fields):
        self.type = "content_block_delta"
        self.delta = types.SimpleNamespace(type=kind, **fields)


def _text(piece):
    return _Delta("text_delta", text=piece)


def _thinking(piece):
    return _Delta("thinking_delta", thinking=piece)


class _FakeStream:
    """What `client.messages.stream(**kw)` returns: a context manager that
    iterates events and hands back the assembled message."""

    def __init__(self, events, final):
        self._events = events
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


class _StreamingMessages:
    """`messages` with both `create` and `stream`; `rounds` is a list of
    (events, final_response) consumed one per loop iteration."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append("create")
        _events, final = self.rounds.pop(0)
        return final

    def stream(self, **kwargs):
        self.calls.append("stream")
        assert "stream" not in kwargs, "the SDK's stream() rejects stream=True"
        events, final = self.rounds.pop(0)
        return _FakeStream(events, final)


class _CreateOnlyMessages:
    def __init__(self, final):
        self.final = final
        self.calls = []

    def create(self, **kwargs):
        self.calls.append("create")
        return self.final


def _turn(monkeypatch, messages, sink):
    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=messages))
    token = smr.DELTA_SINK.set(sink)
    try:
        text, _trace = ag._call_claude_agent(
            [{"role": "user", "content": "hi"}], system="s",
            session_ctx={"authenticated": True, "is_background_task": True})
    finally:
        smr.DELTA_SINK.reset(token)
    return text


def test_text_deltas_reach_the_sink_as_they_arrive(monkeypatch):
    seen = []
    messages = _StreamingMessages([
        ([_text("Hel"), _text("lo")], _Resp([_Block(type="text", text="Hello")])),
    ])
    text = _turn(monkeypatch, messages, seen.append)
    assert text == "Hello"
    assert seen == ["Hel", "lo"], "the Anthropic turn did not stream"
    assert messages.calls == ["stream"]


def test_a_client_without_stream_takes_create_and_emits_nothing(monkeypatch):
    seen = []
    messages = _CreateOnlyMessages(_Resp([_Block(type="text", text="Hello")]))
    text = _turn(monkeypatch, messages, seen.append)
    assert text == "Hello"
    assert seen == []
    assert messages.calls == ["create"]


def test_thinking_deltas_never_reach_the_sink(monkeypatch):
    seen = []
    messages = _StreamingMessages([
        ([_thinking("let me see"), _text("Hel"), _thinking("more"), _text("lo")],
         _Resp([_Block(type="thinking", thinking="let me see more", signature="sig"),
                _Block(type="text", text="Hello")])),
    ])
    text = _turn(monkeypatch, messages, seen.append)
    assert text == "Hello"
    assert seen == ["Hel", "lo"]


def test_a_sink_that_raises_does_not_break_the_turn(monkeypatch):
    def _bad_sink(piece):
        raise RuntimeError("client went away")

    messages = _StreamingMessages([
        ([_text("Hel"), _text("lo")], _Resp([_Block(type="text", text="Hello")])),
    ])
    text = _turn(monkeypatch, messages, _bad_sink)
    assert text == "Hello"


def test_a_tool_round_still_runs_through_the_stream(monkeypatch):
    """The final message comes from the stream, so the tool loop must see the
    same tool_use blocks it did from create(), and each round's text streams."""
    seen = []
    messages = _StreamingMessages([
        ([_text("Looking.")],
         _Resp([_Block(type="text", text="Looking."),
                _Block(type="tool_use", id="tu1", name="search_wiki",
                       input={"query": "nothing"})], "tool_use")),
        ([_text("Nothing "), _text("found.")],
         _Resp([_Block(type="text", text="Nothing found.")])),
    ])
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "search_wiki", lambda inp: "no hits")
    text = _turn(monkeypatch, messages, seen.append)
    assert text == "Nothing found."
    assert messages.calls == ["stream", "stream"]
    assert seen == ["Looking.", "Nothing ", "found."]

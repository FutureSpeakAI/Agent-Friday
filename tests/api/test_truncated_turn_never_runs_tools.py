"""A turn cut off at its output limit is a failure, and its tool calls never run.

A reply that stopped because it ran out of output budget (finish_reason
"length", or "max_tokens") may carry a tool call whose arguments were still
being written. Running it would act on half an instruction. The shared
OpenAI-format loop (local seats and OpenAI-compatible cloud) refuses every call
from such a turn, whether it arrived as native ``tool_calls`` or was parsed out
of the reply's text, and says that the reply was cut off.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import agent as ag

TOOLS = [{"type": "function", "function": {"name": "search_web", "parameters": {"type": "object"}}}]


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake_execute(name, args, *a, **k):
        calls.append((name, args))
        return "ok"
    monkeypatch.setattr(ag, "_execute_tool", fake_execute)
    return calls


def _send_once(message, finish):
    rounds = []

    def send(convo, tools, **kw):
        rounds.append(1)
        if len(rounds) == 1:
            return {"choices": [{"message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    return send, rounds


def _native_call():
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "search_web", "arguments": json.dumps({"q": "weather"})}}]}


@pytest.mark.parametrize("finish", ["length", "max_tokens"])
def test_a_native_tool_call_from_a_cut_off_turn_never_runs(ran, finish):
    send, _ = _send_once(_native_call(), finish)
    text, _trace = ag._oai_agentic_loop([{"role": "user", "content": "go"}], TOOLS, send,
                                        provider="local", model="fixture-seat")
    assert ran == [], "a tool call from a truncated turn was executed"
    assert "cut off" in text.lower()


def test_a_tool_call_parsed_from_cut_off_text_never_runs(ran):
    msg = {"role": "assistant", "content": "<|tool_call>call:search_web{q:weather}<tool_call|>"}
    send, _ = _send_once(msg, "length")
    text, _trace = ag._oai_agentic_loop([{"role": "user", "content": "go"}], TOOLS, send,
                                        provider="local", model="fixture-seat")
    assert ran == [], "a channel-format call from a truncated turn was executed"
    assert "cut off" in text.lower()


def test_a_complete_turn_still_runs_its_tool(ran):
    send, _ = _send_once(_native_call(), "stop")
    ag._oai_agentic_loop([{"role": "user", "content": "go"}], TOOLS, send,
                         provider="local", model="fixture-seat")
    assert [n for n, _ in ran] == ["search_web"]

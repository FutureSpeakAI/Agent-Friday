"""A tool call whose arguments do not fit its schema is never executed.

Before this rule, arguments that failed to parse became ``{}`` and the tool
ran with nothing, answering "nothing found" to a question it was never asked.
Now the model gets a precise error naming the field and what it sent, the
tool does not run, and the schema arrives for the next round so the retry is
well-formed.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import agent as ag

SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": SCHEMA}}]


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake_execute(name, args, *a, **k):
        calls.append((name, args))
        return "ok"
    monkeypatch.setattr(ag, "_execute_tool", fake_execute)
    return calls


def _send_with(arguments):
    rounds = []
    tool_messages = []

    def send(convo, tools, **kw):
        rounds.append(1)
        if len(rounds) == 1:
            return {"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "read_file", "arguments": arguments}}]},
                "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        tool_messages.extend(m for m in convo if m.get("role") == "tool")
        return {"choices": [{"message": {"role": "assistant", "content": "done"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    return send, tool_messages


def test_unparseable_arguments_never_run_the_tool(ran):
    send, tool_msgs = _send_with('{"path": "notes.txt"')   # missing brace
    ag._oai_agentic_loop([{"role": "user", "content": "go"}], TOOLS, send,
                         provider="local", model="fixture-seat")
    assert ran == [], "a tool ran on arguments that failed to parse"
    assert tool_msgs, "the model was told nothing"
    body = tool_msgs[-1]["content"]
    assert "INVALID ARGUMENTS" in body and "did not run" in body
    assert '{"path": "notes.txt"' in body, "the error must show what was sent"


def test_a_missing_required_field_never_runs_the_tool(ran):
    send, tool_msgs = _send_with(json.dumps({"limit": 3}))
    ag._oai_agentic_loop([{"role": "user", "content": "go"}], TOOLS, send,
                         provider="local", model="fixture-seat")
    assert ran == []
    body = tool_msgs[-1]["content"]
    assert "'path' is required" in body and "did not run" in body


def test_repairable_arguments_run_with_the_repair(ran):
    # The object arrived JSON-encoded twice: the meaning is clear, so it runs.
    send, _ = _send_with(json.dumps(json.dumps({"path": "notes.txt"})))
    ag._oai_agentic_loop([{"role": "user", "content": "go"}], TOOLS, send,
                         provider="local", model="fixture-seat")
    assert ran == [("read_file", {"path": "notes.txt"})]


def test_good_arguments_still_run(ran):
    send, _ = _send_with(json.dumps({"path": "notes.txt"}))
    ag._oai_agentic_loop([{"role": "user", "content": "go"}], TOOLS, send,
                         provider="local", model="fixture-seat")
    assert ran == [("read_file", {"path": "notes.txt"})]


def test_execute_tool_refuses_arguments_that_do_not_fit_the_registry_schema(monkeypatch):
    seen = []
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "fixture_tool", lambda inp: seen.append(inp) or "ran")
    monkeypatch.setitem(ag.TOOL_RINGS, "fixture_tool", 0)   # declared read-only (undeclared tools are denied)
    monkeypatch.setattr(ag, "CLAUDE_TOOLS", list(ag.CLAUDE_TOOLS) + [{
        "name": "fixture_tool", "description": "fixture",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}])
    out = ag._execute_tool("fixture_tool", {"limit": 2})
    assert seen == [], "the handler ran on invalid arguments"
    assert "INVALID ARGUMENTS for fixture_tool" in out and "'q' is required" in out


def test_execute_tool_runs_when_the_arguments_fit(monkeypatch):
    seen = []
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "fixture_tool", lambda inp: seen.append(inp) or "ran")
    monkeypatch.setitem(ag.TOOL_RINGS, "fixture_tool", 0)   # declared read-only (undeclared tools are denied)
    from agent_friday.governance import action_gate as _gate   # and internal (an outward tool cards)
    monkeypatch.setattr(_gate, "INTERNAL_TOOLS", frozenset(_gate.INTERNAL_TOOLS) | {"fixture_tool"})
    monkeypatch.setattr(ag, "CLAUDE_TOOLS", list(ag.CLAUDE_TOOLS) + [{
        "name": "fixture_tool", "description": "fixture",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}])
    out = ag._execute_tool("fixture_tool", {"q": "x"})
    assert seen == [{"q": "x"}] and "ran" in out

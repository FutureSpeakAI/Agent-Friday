"""A tool call a seat writes into its reply as text either runs or is reported.

Field report: in a Calendar chat the agent wrote
'<invoke name="load_tools"> <parameter name="query">spawn background task</parameter> </invoke>'
(and a read_file one) into the chat four times. Nothing ran, no error came
back, and the words said "I'm loading it". Reproduced here through the real
shared OpenAI-format loop (the path a local llama-server seat takes) with a
scripted seat that answers exactly that way.

Red on the old loop: the markup is returned as the reply and no tool runs.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent as ag

TOOLS = [
    {"type": "function", "function": {"name": "load_tools", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_file", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]
FIELD = ('I\'m loading the background task tool now.\n'
         '<invoke name="load_tools"> <parameter name="query">spawn background task</parameter> </invoke>')


def _reply(content, finish="stop"):
    return {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake_execute(name, args, *a, **k):
        calls.append((name, args))
        return "loaded: spawn_task"
    monkeypatch.setattr(ag, "_execute_tool", fake_execute)
    return calls


def _scripted(*replies):
    seen = []

    def send(convo, tools, **kw):
        seen.append([dict(m) for m in convo])
        return replies[min(len(seen) - 1, len(replies) - 1)]
    return send, seen


def test_the_field_load_tools_text_call_is_routed_to_the_loader(ran):
    send, seen = _scripted(_reply(FIELD), _reply("It's loaded; starting the task."))
    text, trace = ag._oai_agentic_loop([{"role": "user", "content": "spawn it"}], TOOLS, send,
                                       provider="openai", model="bonsai2:27b", seat="local")
    # load_tools is the loop's own loader (never _execute_tool): it ran as a step.
    assert [t["name"] for t in trace] == ["load_tools"]
    assert len(seen) == 2, "the seat was never given the loader's answer"
    assert "<invoke" not in text
    # The assistant turn echoed to the seat carries the call, not the markup.
    echoed = [m for m in seen[-1] if m.get("role") == "assistant"]
    assert echoed and echoed[0].get("tool_calls")
    assert "<invoke" not in (echoed[0].get("content") or "")


def test_a_text_read_file_call_runs_through_the_normal_tool_path(ran):
    send, _ = _scripted(_reply('Reading it.\n<invoke name="read_file"><parameter name="path">notes.md</parameter></invoke>'),
                        _reply("Here is what it says."))
    text, _ = ag._oai_agentic_loop([{"role": "user", "content": "read notes"}], TOOLS, send,
                                   provider="openai", model="bonsai2:27b", seat="local")
    assert ran == [("read_file", {"path": "notes.md"})]
    assert text == "Here is what it says."


def test_a_text_call_that_cannot_run_is_reported_with_model_and_seat(ran):
    send, _ = _scripted(_reply(FIELD))
    text, _ = ag._oai_agentic_loop([{"role": "user", "content": "spawn it"}], None, send,
                                   provider="openai", model="bonsai2:27b", seat="local")
    assert ran == []
    assert "<invoke" not in text and "<parameter" not in text
    assert "Nothing ran" in text and "bonsai2:27b" in text and "local seat" in text


def test_a_call_to_a_tool_not_offered_never_runs(ran):
    send, _ = _scripted(_reply('<invoke name="send_email"><parameter name="to">a@example.com</parameter></invoke>'))
    text, _ = ag._oai_agentic_loop([{"role": "user", "content": "x"}], TOOLS, send,
                                   provider="openai", model="fixture-seat", seat="local")
    assert ran == []
    assert "Nothing ran" in text and "send_email" in text


def test_loading_claim_without_a_tool_is_owned_up_to(ran):
    send, _ = _scripted(_reply("I've scheduled the weekly email task for every Monday."))
    text, _ = ag._oai_agentic_loop([{"role": "user", "content": "x"}], TOOLS, send,
                                   provider="openai", model="fixture-seat", seat="local")
    assert "nothing was actually" in text


def test_the_cloud_loop_guard_names_its_model():
    out = ag._guard_final_reply(FIELD, model="claude-x", seat="cloud", provider="anthropic",
                                tool_trace=[], tools_offered=True)
    assert "Nothing ran" in out and "claude-x" in out and "cloud seat" in out

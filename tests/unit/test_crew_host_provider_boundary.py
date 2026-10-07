"""A delayed host delegation is refused before provider-response/tool instrumentation."""
import json
from types import SimpleNamespace

import pytest

from agent_friday.services import agent, crew_runtime, off_record


@pytest.fixture
def host(monkeypatch):
    from agent_friday.services import compaction, cost_meter, tool_catalogue
    state = {"private": True, "generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    origin = crew_runtime.capture_host_origin()
    monkeypatch.setattr(compaction, "maybe_compact", lambda convo, **kw: convo)
    monkeypatch.setattr(agent, "_get_vault_control", lambda: None)
    monkeypatch.setattr(agent, "_seal_or_block", lambda payload, provider: payload)
    monkeypatch.setattr(agent, "_register_agent_orb", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_ledger_model_invocation", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "get_behavioral_monitor", lambda: None)
    monkeypatch.setattr(cost_meter, "meter", lambda *a, **kw: 0)
    monkeypatch.setattr(tool_catalogue, "enabled", lambda: False)
    monkeypatch.setattr(agent, "_execute_tool", lambda *a, **kw:
                        pytest.fail("Private Crew arguments must not reach execution"))
    monkeypatch.setattr(agent, "_task_log_tool", lambda *a, **kw:
                        pytest.fail("Private Crew arguments must not reach tool logs"))
    monkeypatch.setattr(agent, "_orb_tool_trace", lambda *a, **kw:
                        pytest.fail("Private Crew arguments must not reach result traces"))
    return state, {"_crew_host_origin": origin}


@pytest.mark.parametrize("wire", ["native", "channel"])
def test_openai_host_refuses_private_delegation_before_response_logging(host, monkeypatch, wire):
    state, session = host
    tools = [{"type": "function", "function": {"name": "ask_crew", "parameters": {
        "type": "object", "properties": {"agent": {"type": "string"}, "request": {"type": "string"}}}}}]
    monkeypatch.setattr(agent._rtrace, "after_oai_round", lambda *a, **kw:
                        pytest.fail("Denied Crew response must be checked before it enters traces"))
    def send(*a, **kw):
        state.update(private=False, generation=9)
        message = {"role": "assistant", "content": ""}
        if wire == "native":
            message["tool_calls"] = [{"id": "private-call", "type": "function", "function": {
                "name": "ask_crew", "arguments": json.dumps({"agent": "Reviewer", "request": "Private marker"})}}]
        else:
            message["content"] = "<|tool_call>call:ask_crew{agent:Reviewer,request:Private marker}<tool_call|>"
        return {"choices": [{"message": message, "finish_reason": "tool_calls"}], "usage": {}}
    text, trace = agent._oai_agentic_loop(
        [{"role": "user", "content": "Synthetic request"}], tools, send,
        provider="openai", model="fixture", session_ctx=session, max_iters=1)
    assert "CREW DENY" in text and "Private marker" not in text
    assert trace == []


def test_anthropic_host_refuses_private_delegation_before_response_logging(host, monkeypatch):
    state, session = host
    monkeypatch.setattr(agent._rtrace, "after_anthropic_response", lambda *a, **kw:
                        pytest.fail("Denied Crew response must be checked before it enters traces"))
    def create(**kwargs):
        state.update(private=False, generation=9)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="private-call", name="ask_crew",
            input={"agent": "Reviewer", "request": "Private marker"})], stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    monkeypatch.setattr(agent, "get_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    text, trace = agent._call_claude_agent(
        [{"role": "user", "content": "Synthetic request"}], system="Fixture", model="fixture",
        session_ctx=session, max_iters=1, tools=[tool for tool in agent.CLAUDE_TOOLS if tool["name"] == "ask_crew"])
    assert "CREW DENY" in text and "Private marker" not in text
    assert trace == []

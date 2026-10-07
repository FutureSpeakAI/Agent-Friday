"""Crew host admission survives multi-call responses and delayed tool hooks."""
import json
from types import SimpleNamespace

import pytest

from agent_friday.services import agent, crew_runtime, off_record, tool_hooks


MARKER = "late-host-private-marker"
NAMES = ["ask_crew", "ask-crew"]


@pytest.fixture
def public_host(monkeypatch):
    state = {"generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: False)
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    session = {"_crew_host_origin": crew_runtime.capture_host_origin(),
               "conversation_id": "fixture-conversation"}
    return state, session


@pytest.fixture
def provider_host(public_host, monkeypatch):
    from agent_friday.services import compaction, cost_meter, tool_catalogue
    state, session = public_host
    sinks = []

    def capture(label, name, data):
        if state["generation"] != 8 and name in NAMES:
            sinks.append((label, name, data))

    monkeypatch.setattr(compaction, "maybe_compact", lambda convo, **kw: convo)
    monkeypatch.setattr(agent, "_seal_or_block", lambda payload, provider: payload)
    monkeypatch.setattr(agent, "_register_agent_orb", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_ledger_model_invocation", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "get_behavioral_monitor", lambda: None)
    monkeypatch.setattr(cost_meter, "meter", lambda *a, **kw: 0)
    monkeypatch.setattr(tool_catalogue, "enabled", lambda: False)
    monkeypatch.setattr(agent._rtrace, "after_anthropic_response", lambda *a, **kw: None)
    monkeypatch.setattr(agent._rtrace, "after_oai_round", lambda *a, **kw: None)
    monkeypatch.setattr(agent._rtrace, "tool_finished",
                        lambda name, inp, result: capture("reasoning", name, inp))
    monkeypatch.setattr(agent, "_task_log_tool",
                        lambda _session, name, inp: capture("task", name, inp))
    monkeypatch.setattr(agent, "_orb_tool_trace",
                        lambda _orb, name, inp, *_a: capture("orb", name, inp))

    def vault_check(_provider, name, data, **_kw):
        capture("vault", name, data)
        return True, "", None

    monkeypatch.setattr(agent, "VaultAccessControl", True)
    monkeypatch.setattr(agent, "_get_vault_control",
                        lambda: SimpleNamespace(check_action=vault_check))
    return state, session, sinks


def run_provider(monkeypatch, session, wire, name, *, first=False, malformed=False):
    arguments = {"agent": "Reviewer", "request": MARKER}
    tools = [tool for tool in agent.CLAUDE_TOOLS if tool["name"] in ("ask_crew", "list_crew")]
    if wire == "anthropic":
        calls = [SimpleNamespace(type="tool_use", id="crew-call", name=name, input=arguments)]
        if first:
            calls.insert(0, SimpleNamespace(type="tool_use", id="first-call", name="list_crew", input={}))
        response = SimpleNamespace(content=calls, stop_reason="tool_use",
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        monkeypatch.setattr(agent, "get_anthropic_client", lambda: SimpleNamespace(
            messages=SimpleNamespace(create=lambda **_kw: response)))
        return agent._call_claude_agent(
            [{"role": "user", "content": "Synthetic request"}], system="Fixture", model="fixture",
            session_ctx=session, max_iters=1, tools=tools)
    args = ("{" + MARKER if malformed else
            arguments if wire == "object" else json.dumps(arguments))
    calls = [{"id": "crew-call", "type": "function",
              "function": {"name": name, "arguments": args}}]
    if first:
        calls.insert(0, {"id": "first-call", "type": "function",
                         "function": {"name": "list_crew", "arguments": "{}"}})
    message = {"role": "assistant", "content": "", "tool_calls": calls}
    response = {"choices": [{"message": message, "finish_reason": "tool_calls"}], "usage": {}}
    oai_tools = [{"type": "function", "function": {"name": tool["name"],
                   "parameters": tool["input_schema"]}} for tool in tools]
    return agent._oai_agentic_loop(
        [{"role": "user", "content": "Synthetic request"}], oai_tools,
        lambda *a, **kw: response, provider="openai", model="fixture",
        session_ctx=session, max_iters=1)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("wire,malformed", [("anthropic", False), ("string", False),
                                           ("object", False), ("string", True)])
def test_second_call_rechecks_before_arguments(provider_host, monkeypatch, name, wire, malformed):
    state, session, sinks = provider_host
    executed = []

    def execute(tool, _arguments, **_kw):
        executed.append(tool)
        if tool == "list_crew":
            state["generation"] += 1
        return "finished"

    monkeypatch.setattr(agent, "_execute_tool", execute)
    text, trace = run_provider(monkeypatch, session, wire, name, first=True, malformed=malformed)
    assert "CREW DENY" in text and MARKER not in text
    assert executed == ["list_crew"], executed
    assert sinks == [], sinks
    assert MARKER not in json.dumps(trace)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("wire", ["anthropic", "string"])
def test_completion_rechecks_before_result_instrumentation(provider_host, monkeypatch, name, wire):
    state, session, sinks = provider_host

    def execute(_tool, _arguments, **_kw):
        state["generation"] += 1
        return "[CREW DENY] Original session ended."

    monkeypatch.setattr(agent, "_execute_tool", execute)
    text, trace = run_provider(monkeypatch, session, wire, name)
    assert "CREW DENY" in text and MARKER not in text
    assert sinks == [], sinks
    assert trace == []


@pytest.mark.parametrize("wire", ["anthropic", "string"])
def test_current_public_provider_keeps_successful_tool_trace(provider_host, monkeypatch, wire):
    _state, session, sinks = provider_host
    executed = []
    monkeypatch.setattr(agent, "_execute_tool", lambda name, inp, **kw:
                        executed.append((name, inp)) or "accepted")
    text, trace = run_provider(monkeypatch, session, wire, "ask_crew")
    assert "CREW DENY" not in text
    assert executed == [("ask_crew", {"agent": "Reviewer", "request": MARKER})]
    assert trace == [{"name": "ask_crew", "input": executed[0][1], "result": "accepted"}]
    assert sinks == []


@pytest.mark.parametrize("wire", ["anthropic", "string"])
@pytest.mark.parametrize("allowed", [False, True])
def test_vault_wait_rechecks_before_denial_or_task_log(provider_host, monkeypatch, wire, allowed):
    state, session, sinks = provider_host

    def vault_check(*_args, **_kw):
        state["generation"] += 1
        return allowed, MARKER, None

    monkeypatch.setattr(agent, "_get_vault_control",
                        lambda: SimpleNamespace(check_action=vault_check))
    monkeypatch.setattr(agent, "_execute_tool", lambda *a, **kw:
                        pytest.fail("Expired vault admission reached execution"))
    text, trace = run_provider(monkeypatch, session, wire, "ask_crew")
    assert "CREW DENY" in text and MARKER not in text
    assert trace == [] and sinks == []


@pytest.fixture
def executor_host(public_host, monkeypatch):
    from agent_friday.services import model_router
    state, session = public_host
    sinks = []
    monkeypatch.setattr(agent._receipts, "record", lambda *a, **kw: sinks.append(("receipt", a, kw)))
    monkeypatch.setattr(agent._tool_output, "clip_result", lambda name, result: result)
    monkeypatch.setattr(model_router, "announce_tool", lambda *a, **kw: sinks.append(("announce", a, kw)))
    monkeypatch.setattr(agent.traceback, "print_exc", lambda: sinks.append(("traceback",)))
    monkeypatch.setattr(tool_hooks, "run_post_hooks", lambda ctx, result:
                        sinks.append(("post", ctx.input, result)) or result)
    monkeypatch.setattr(tool_hooks, "run_pre_hooks", lambda ctx: tool_hooks.ALLOW)
    return state, session, sinks


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("deny", [False, True])
def test_pre_hook_expiry_precedes_narration_and_denial_receipt(executor_host, monkeypatch, name, deny):
    state, session, sinks = executor_host

    def pre(_ctx):
        state["generation"] += 1
        return tool_hooks.DENY(MARKER) if deny else tool_hooks.ALLOW

    monkeypatch.setattr(tool_hooks, "run_pre_hooks", pre)
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "ask_crew",
                        lambda _inp: pytest.fail("Expired pre-hook admission reached the handler"))
    result = agent._execute_tool(name, {"agent": "Reviewer", "request": MARKER}, session_ctx=session)
    assert "CREW DENY" in result and MARKER not in result
    assert sinks == [], sinks


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("raises", [False, True])
def test_handler_expiry_precedes_receipts_and_post_hooks(executor_host, monkeypatch, name, raises):
    state, session, sinks = executor_host

    def handler(_inp):
        sinks.clear()  # Narration happened while admission was still current.
        state["generation"] += 1
        if raises:
            raise RuntimeError(MARKER)
        return MARKER

    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "ask_crew", handler)
    result = agent._execute_tool(name, {"agent": "Reviewer", "request": MARKER}, session_ctx=session)
    assert "CREW DENY" in result and MARKER not in result
    assert sinks == [], sinks


@pytest.mark.parametrize("name", NAMES)
def test_current_public_executor_keeps_origin_and_conversation(executor_host, monkeypatch, name):
    _state, session, sinks = executor_host
    seen = []

    def handler(inp):
        seen.append((crew_runtime.HOST_ORIGIN.get(), agent._CURRENT_CONVERSATION.get(), inp))
        return "accepted"

    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "ask_crew", handler)
    result = agent._execute_tool(name, {"agent": "Reviewer", "request": MARKER}, session_ctx=session)
    assert result == "accepted"
    assert seen == [(session["_crew_host_origin"], session["conversation_id"],
                     {"agent": "Reviewer", "request": MARKER})]
    assert any(row[0] == "post" for row in sinks)


def test_unrelated_executor_keeps_behavior_after_generation_change(executor_host):
    state, session, sinks = executor_host
    state["generation"] += 1
    result = agent._execute_tool("fixture_unrelated_tool", {}, session_ctx=session,
                                 handler=lambda _inp: "unrelated result")
    assert result == "unrelated result"
    assert any(row[0] == "post" for row in sinks)


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_executor_installs_admission_between_real_hooks(public_host, monkeypatch, phase):
    from agent_friday.services import model_router
    state, session = public_host
    seen = []
    monkeypatch.setattr(tool_hooks, "_PRE_HOOKS", [])
    monkeypatch.setattr(tool_hooks, "_POST_HOOKS", [])
    monkeypatch.setattr(tool_hooks, "hook_enabled", lambda *a: True)
    monkeypatch.setattr(agent._receipts, "record", lambda *a, **kw: None)
    monkeypatch.setattr(agent._tool_output, "clip_result", lambda _name, result: result)
    monkeypatch.setattr(model_router, "announce_tool", lambda *a, **kw: None)
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "ask_crew", lambda _inp: "accepted")

    def expires(*_args):
        state["generation"] += 1
        return tool_hooks.ALLOW if phase == "pre" else "accepted"

    def captures(ctx, *_args):
        seen.append(ctx.input)
        return tool_hooks.ALLOW if phase == "pre" else "accepted"

    register = tool_hooks.register_pre_hook if phase == "pre" else tool_hooks.register_post_hook
    register(expires, name="expires", priority=1)
    register(captures, name="captures", priority=2)
    result = agent._execute_tool("ask-crew", {"agent": "Reviewer", "request": MARKER}, session_ctx=session)
    assert "CREW DENY" in result and MARKER not in result
    assert seen == []


@pytest.mark.parametrize("phase", ["pre", "post"])
@pytest.mark.parametrize("raises", [False, True])
def test_hook_chain_stops_before_next_hook_or_exception_log(public_host, monkeypatch, phase, raises):
    state, session = public_host
    seen = []
    monkeypatch.setattr(tool_hooks, "_PRE_HOOKS", [])
    monkeypatch.setattr(tool_hooks, "_POST_HOOKS", [])
    monkeypatch.setattr(tool_hooks, "hook_enabled", lambda *a: True)
    monkeypatch.setattr(tool_hooks, "_log_hook_error", lambda *a: seen.append(("exception", a)))

    def expires(*_args):
        state["generation"] += 1
        if raises:
            raise RuntimeError(MARKER)
        return tool_hooks.ALLOW if phase == "pre" else MARKER

    def captures(*_args):
        seen.append(("later-hook", MARKER))
        return tool_hooks.ALLOW if phase == "pre" else MARKER

    register = tool_hooks.register_pre_hook if phase == "pre" else tool_hooks.register_post_hook
    register(expires, name="expires", priority=1)
    register(captures, name="captures", priority=2)
    ctx = tool_hooks.HookContext(tool_name="ask_crew", input={"request": MARKER}, session_ctx=session)
    ctx.admission = lambda: agent._crew_delegation_denial("ask_crew", session)
    result = (tool_hooks.run_pre_hooks(ctx) if phase == "pre"
              else tool_hooks.run_post_hooks(ctx, MARKER))
    if phase == "pre":
        assert result.action == "deny", (result, seen)
    text = result.reason if phase == "pre" else result
    assert "CREW DENY" in text and MARKER not in text
    assert seen == [], seen


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_unrelated_hooks_keep_default_behavior(public_host, monkeypatch, phase):
    state, session = public_host
    seen = []
    monkeypatch.setattr(tool_hooks, "_PRE_HOOKS", [])
    monkeypatch.setattr(tool_hooks, "_POST_HOOKS", [])
    monkeypatch.setattr(tool_hooks, "hook_enabled", lambda *a: True)

    def first(*_args):
        state["generation"] += 1
        return tool_hooks.ALLOW if phase == "pre" else "first"

    def second(*_args):
        seen.append("second")
        return tool_hooks.ALLOW if phase == "pre" else "second"

    register = tool_hooks.register_pre_hook if phase == "pre" else tool_hooks.register_post_hook
    register(first, name="first", priority=1)
    register(second, name="second", priority=2)
    ctx = tool_hooks.HookContext(tool_name="unrelated", input={}, session_ctx=session)
    result = tool_hooks.run_pre_hooks(ctx) if phase == "pre" else tool_hooks.run_post_hooks(ctx, "initial")
    assert seen == ["second"]
    assert (result.action == "allow") if phase == "pre" else result == "second"

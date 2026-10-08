"""Sites host authority is checked before arguments and after delayed work."""
import json
from types import SimpleNamespace

import pytest

from agent_friday.services import agent, crew_runtime, off_record, tool_hooks


MARKER = "synthetic-sites-boundary-marker"


@pytest.fixture(params=["site_action", "site-action", "domain_action", "domain-action"])
def action(request):
    name = request.param
    canonical = name.replace("-", "_")
    return name, canonical, {"action": "inspect", "operation_id": MARKER}


@pytest.fixture
def host(monkeypatch):
    from agent_friday.services import compaction, cost_meter, tool_catalogue
    state = {"private": False, "generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    session = {"_crew_host_origin": crew_runtime.capture_host_origin(),
               "conversation_id": "fixture-sites-chat"}
    sinks = []

    def capture(label, *args, **kwargs):
        if state["generation"] != 8:
            sinks.append((label, args, kwargs))

    monkeypatch.setattr(compaction, "maybe_compact", lambda convo, **kw: convo)
    monkeypatch.setattr(agent, "_get_vault_control", lambda: None)
    monkeypatch.setattr(agent, "_seal_or_block", lambda payload, provider: payload)
    monkeypatch.setattr(agent, "_register_agent_orb", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_ledger_model_invocation", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "get_behavioral_monitor", lambda: None)
    monkeypatch.setattr(cost_meter, "meter", lambda *a, **kw: 0)
    monkeypatch.setattr(tool_catalogue, "enabled", lambda: False)
    for target, name in ((agent._rtrace, "after_anthropic_response"),
                         (agent._rtrace, "after_oai_round"),
                         (agent._rtrace, "tool_finished"),
                         (agent, "_task_log_tool"), (agent, "_orb_tool_trace")):
        monkeypatch.setattr(target, name,
                            lambda *a, _name=name, **kw: capture(_name, *a, **kw))
    return state, session, sinks


def provider_call(monkeypatch, session, action, wire, *, before_response=None, first=False):
    name, canonical, arguments = action
    tools = [tool for tool in agent.CLAUDE_TOOLS
             if tool["name"] in (canonical, "list_crew")]
    if wire == "anthropic":
        calls = [SimpleNamespace(type="tool_use", id="sites-call", name=name, input=arguments)]
        if first:
            calls.insert(0, SimpleNamespace(type="tool_use", id="first-call", name="list_crew", input={}))

        def create(**kwargs):
            if before_response:
                before_response()
            return SimpleNamespace(content=calls, stop_reason="tool_use",
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))

        monkeypatch.setattr(agent, "get_anthropic_client", lambda: SimpleNamespace(
            messages=SimpleNamespace(create=create)))
        return agent._call_claude_agent(
            [{"role": "user", "content": "Synthetic request"}], system="Fixture", model="fixture",
            session_ctx=session, max_iters=1, tools=tools)

    def send(*args, **kwargs):
        if before_response:
            before_response()
        message = {"role": "assistant", "content": ""}
        if wire == "channel":
            message["content"] = ("<|tool_call>call:" + name +
                                  "{action:inspect,operation_id:" + MARKER + "}<tool_call|>")
        else:
            encoded = (arguments if wire == "object" else
                       "{" + MARKER if wire == "malformed" else json.dumps(arguments))
            message["tool_calls"] = [{"id": "sites-call", "type": "function",
                                      "function": {"name": name, "arguments": encoded}}]
            if first:
                message["tool_calls"].insert(0, {"id": "first-call", "type": "function",
                    "function": {"name": "list_crew", "arguments": "{}"}})
        return {"choices": [{"message": message, "finish_reason": "tool_calls"}], "usage": {}}

    oai_tools = [{"type": "function", "function": {
        "name": tool["name"], "parameters": tool["input_schema"]}} for tool in tools]
    return agent._oai_agentic_loop(
        [{"role": "user", "content": "Synthetic request"}], oai_tools, send,
        provider="openai", model="fixture", session_ctx=session, max_iters=1)


@pytest.mark.parametrize("wire", ["anthropic", "string", "object", "malformed", "channel"])
@pytest.mark.parametrize("origin_kind", ["private-ended", "public-changed", "missing", "json-shaped"])
def test_response_admission_precedes_argument_instrumentation(host, action, monkeypatch, wire, origin_kind):
    state, session, sinks = host
    if origin_kind == "private-ended":
        state["private"] = True
        session["_crew_host_origin"] = crew_runtime.capture_host_origin()
    elif origin_kind == "missing":
        session.pop("_crew_host_origin")
    elif origin_kind == "json-shaped":
        session["_crew_host_origin"] = {"private": False, "generation": 8}
    monkeypatch.setattr(agent, "_execute_tool", lambda *a, **kw:
                        pytest.fail("Stale Sites arguments reached execution"))
    text, trace = provider_call(monkeypatch, session, action, wire,
                               before_response=lambda: state.update(private=False, generation=9))
    assert "SITES DENY" in text and MARKER not in text
    assert sinks == [] and trace == []


@pytest.mark.parametrize("wire", ["anthropic", "string", "object", "malformed"])
def test_second_call_rechecks_original_generation(host, action, monkeypatch, wire):
    state, session, sinks = host
    executed = []

    def execute(name, arguments, **kwargs):
        executed.append(name)
        state["generation"] = 9
        return "completed unrelated read"

    monkeypatch.setattr(agent, "_execute_tool", execute)
    text, trace = provider_call(monkeypatch, session, action, wire, first=True)
    assert "SITES DENY" in text and MARKER not in text
    assert executed == ["list_crew"]
    assert MARKER not in json.dumps(trace)
    assert all(MARKER not in repr(row) for row in sinks)


@pytest.mark.parametrize("wire", ["anthropic", "string"])
def test_expired_completion_does_not_record_original_arguments(host, action, monkeypatch, wire):
    state, session, sinks = host

    def execute(name, arguments, **kwargs):
        state["generation"] = 9
        return MARKER

    monkeypatch.setattr(agent, "_execute_tool", execute)
    text, trace = provider_call(monkeypatch, session, action, wire)
    assert "SITES DENY" in text and MARKER not in text
    assert sinks == [] and trace == []


@pytest.mark.parametrize("wire", ["anthropic", "string", "object", "channel"])
def test_current_public_provider_keeps_successful_trace(host, action, monkeypatch, wire):
    _state, session, sinks = host
    executed = []
    monkeypatch.setattr(agent, "_execute_tool", lambda name, inp, **kw:
                        executed.append((name, inp, kw.get("session_ctx"))) or "accepted")
    text, trace = provider_call(monkeypatch, session, action, wire)
    assert "SITES DENY" not in text
    assert len(executed) == 1 and executed[0][0] == action[0]
    assert executed[0][1] == action[2] and executed[0][2] is session
    assert trace == [{"name": action[0], "input": action[2], "result": "accepted"}]
    assert sinks == []


@pytest.mark.parametrize("wire", ["anthropic", "string"])
@pytest.mark.parametrize("allowed", [False, True])
def test_vault_wait_rechecks_before_tool_instrumentation(host, action, monkeypatch, wire, allowed):
    state, session, sinks = host

    def vault_check(*args, **kwargs):
        state["generation"] = 9
        return allowed, MARKER, None

    monkeypatch.setattr(agent, "VaultAccessControl", True)
    monkeypatch.setattr(agent, "_get_vault_control", lambda: SimpleNamespace(check_action=vault_check))
    monkeypatch.setattr(agent, "_execute_tool", lambda *a, **kw:
                        pytest.fail("Expired vault admission reached execution"))
    text, trace = provider_call(monkeypatch, session, action, wire)
    assert "SITES DENY" in text and MARKER not in text
    assert sinks == [] and trace == []


@pytest.fixture
def executor(host, monkeypatch):
    from agent_friday.services import model_router
    state, session, _ = host
    events = []
    monkeypatch.setattr(agent._receipts, "record", lambda *a, **kw: events.append(("receipt", a, kw)))
    monkeypatch.setattr(agent._tool_output, "clip_result", lambda name, result: result)
    monkeypatch.setattr(model_router, "announce_tool", lambda *a, **kw: events.append(("announce", a, kw)))
    monkeypatch.setattr(agent.traceback, "print_exc", lambda: events.append(("traceback",)))
    monkeypatch.setattr(tool_hooks, "run_post_hooks", lambda ctx, result:
                        events.append(("post", ctx.input, result)) or result)
    monkeypatch.setattr(tool_hooks, "run_pre_hooks", lambda ctx: tool_hooks.ALLOW)
    return state, session, events


def test_public_executor_installs_exact_origin_and_owner(executor, action, monkeypatch):
    _state, session, events = executor
    name, canonical, arguments = action
    seen = []

    def handler(inp):
        seen.append((crew_runtime.HOST_ORIGIN.get(), agent._CURRENT_CONVERSATION.get(), inp))
        return "accepted"

    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, canonical, handler)
    result = agent._execute_tool(name, arguments, session_ctx=session)
    assert result == "accepted"
    assert seen == [(session["_crew_host_origin"], session["conversation_id"], arguments)]
    assert any(row[0] == "post" for row in events)


@pytest.mark.parametrize("phase", ["pre-hook", "handler", "handler-exception"])
def test_executor_drops_expired_arguments_before_completion_hooks(executor, action, monkeypatch, phase):
    state, session, events = executor
    name, canonical, arguments = action

    def handler(inp):
        if phase == "pre-hook":
            pytest.fail("Expired hook admission reached handler")
        events.clear()
        state["generation"] = 9
        if phase == "handler-exception":
            raise RuntimeError(MARKER)
        return MARKER

    if phase == "pre-hook":
        def before(ctx):
            state["generation"] = 9
            return tool_hooks.ALLOW
        monkeypatch.setattr(tool_hooks, "run_pre_hooks", before)
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, canonical, handler)
    result = agent._execute_tool(name, arguments, session_ctx=session)
    assert "SITES DENY" in result and MARKER not in result
    assert events == []

@pytest.mark.parametrize("restore_conversation", [False, True])
def test_deferred_crew_scope_reaches_workflow_handler_context(executor, action, monkeypatch, restore_conversation):
    from agent_friday.services import approvals, conversations, crew_access, subagents
    from agent_friday.governance import action_gate
    _state, session, _events = executor
    name, canonical, arguments = action
    task_id, project_id = "fixture-crew-task", "fixture-project"
    saved = {"task_id": task_id, "crew_agent_id": "fixture-agent", "crew_revision": 4,
             "project_id": project_id, "crew_started": agent._time.monotonic()}
    binding = {"agent_id": saved["crew_agent_id"], "revision": saved["crew_revision"], "project_id": project_id}
    monkeypatch.setitem(agent.TASKS, task_id, {"task_id": task_id, "status": "running",
        "created": agent._time.time(), "crew_context": binding, "crew_tool_calls": 0})
    session["approved_card"] = "fixture-approval"
    monkeypatch.setattr(approvals, "get_approval", lambda aid: {"payload": {"crew_context": saved}})
    monkeypatch.setattr(crew_access, "validate_dispatch", lambda *a: {"time_budget_s": 60, "max_steps": 5})
    monkeypatch.setattr(crew_access, "authorize_tool", lambda *a: (True, "fixture scope"))
    monkeypatch.setattr(action_gate, "_receipt", lambda *a: None)
    monkeypatch.setattr(agent, "_journal", lambda: SimpleNamespace(
        stop_requested=lambda tid: False, resolve_task_id=lambda ctx: ctx.get("task_id")))
    monkeypatch.setattr(subagents, "get_task_scope", lambda tid: None)
    monkeypatch.setattr(conversations, "load", lambda cid: {"project": project_id})
    original_origin = session["_crew_host_origin"]
    prior_context, prior_origin = agent._CURRENT_TOOL_CONTEXT.get(), crew_runtime.HOST_ORIGIN.get()
    seen = []

    def before(ctx):
        verdict = agent._hook_crew_access(ctx)
        assert verdict.action != "deny" and ctx.session_ctx is not session
        if restore_conversation:
            ctx.session_ctx = dict(ctx.session_ctx, conversation_id="fixture-restored-chat")
        assert ctx.admission is not None and ctx.admission() is None
        return verdict

    def handler(inp):
        effective = agent._CURRENT_TOOL_CONTEXT.get()
        assert all(effective[key] == value for key, value in saved.items())
        assert crew_runtime.HOST_ORIGIN.get() is original_origin
        owner = agent._workflow_caller_context()
        seen.append(owner)
        assert owner["nested_execution"] is True and owner["task_id"] == task_id
        assert owner["project_id"] == project_id
        assert agent._CURRENT_CONVERSATION.get() == owner["conversation_id"]
        return "accepted"

    monkeypatch.setattr(tool_hooks, "run_pre_hooks", before)
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, canonical, handler)
    assert agent._execute_tool(name, arguments, session_ctx=session) == "accepted"
    assert len(seen) == 1
    assert seen[0]["conversation_id"] == ("fixture-restored-chat" if restore_conversation else session["conversation_id"])
    assert "task_id" not in session
    assert agent._CURRENT_TOOL_CONTEXT.get() is prior_context
    assert crew_runtime.HOST_ORIGIN.get() is prior_origin


def test_hook_scope_replacement_cannot_replace_original_host_origin(executor, action, monkeypatch):
    state, session, events = executor
    name, canonical, arguments = action
    original_origin, denials = session["_crew_host_origin"], []

    def before(ctx):
        state["generation"] = 9
        ctx.session_ctx = dict(ctx.session_ctx, task_id="fixture-restored-task",
                               _crew_host_origin=crew_runtime.capture_host_origin())
        denials.append(ctx.admission())
        return tool_hooks.ALLOW

    monkeypatch.setattr(tool_hooks, "run_pre_hooks", before)
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, canonical,
                        lambda inp: pytest.fail("A hook cannot renew the captured host origin"))
    result = agent._execute_tool(name, arguments, session_ctx=session)
    assert "SITES DENY" in result and "SITES DENY" in denials[0]
    assert session["_crew_host_origin"] is original_origin
    assert events == []

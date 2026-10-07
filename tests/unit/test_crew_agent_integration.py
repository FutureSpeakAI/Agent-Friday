"""Crew uses one explicit provider and its task-bound capabilities."""
from types import SimpleNamespace
import time

import pytest

from agent_friday.services import agent
from agent_friday.services import crew_access


@pytest.fixture
def bound(monkeypatch):
    profile = {"id": "crew-" + "a" * 16, "revision": 1, "status": "active",
               "provider": "test-cloud", "model": "model-test", "allowed_tools": ["read_file"],
               "max_steps": 2, "time_budget_s": 60}
    monkeypatch.setattr(crew_access, "validate_dispatch", lambda *a, **k: profile)
    monkeypatch.setattr(agent, "_load_settings", lambda: {})
    from agent_friday.services import provider_registry
    descriptor = {"name": "test-cloud", "type": "openai-compatible", "adapter": "openai-compatible",
                  "classification": "cloud", "enabled": True, "base_url": "https://example.invalid/v1"}
    monkeypatch.setattr(provider_registry, "get_provider_registry", lambda: SimpleNamespace(get_provider=lambda name: descriptor))
    session = {"crew_agent_id": profile["id"], "crew_revision": 1, "project_id": None,
               "crew_binding": {"provider": profile["provider"], "model": profile["model"]},
               "task_id": "crew-test-task", "crew_started": time.monotonic()}
    return profile, session, descriptor


def test_exact_provider_failure_does_not_try_any_fallback(bound, monkeypatch):
    _profile, session, _descriptor = bound
    calls = []
    def selected(*a, **kw):
        calls.append((kw["provider"], kw["model"]))
        raise RuntimeError("selected service unavailable")
    monkeypatch.setattr(agent, "_call_openai", selected)
    monkeypatch.setattr(agent, "_call_claude_agent", lambda *a, **kw: pytest.fail("native fallback must not run"))
    monkeypatch.setattr(agent, "_call_ollama", lambda *a, **kw: pytest.fail("local fallback must not run"))
    with pytest.raises(RuntimeError, match="selected service unavailable"):
        agent._generate_agent_untraced([], system="bounded", session_ctx=session, tools=[])
    assert calls == [("test-cloud", "model-test")]


def test_empty_tool_override_stays_empty_and_binding_overrides_global_choice(bound, monkeypatch):
    _profile, session, _descriptor = bound
    calls = []
    monkeypatch.setattr(agent, "_call_openai", lambda *a, **kw: calls.append(kw) or ("answer", []))
    assert agent._generate_agent_untraced([], system="bounded", model="wrong-global", session_ctx=session, tools=[]) == ("answer", [])
    assert calls[0]["tools"] == []
    assert calls[0]["model"] == "model-test"
    assert calls[0]["fallback_models"] is None


def test_profile_is_upper_bound_even_if_caller_passes_full_registry(bound, monkeypatch):
    _profile, session, _descriptor = bound
    calls = []
    monkeypatch.setattr(agent, "_call_openai", lambda *a, **kw: calls.append(kw) or ("answer", []))
    agent._generate_agent_untraced([], system="bounded", session_ctx=session, tools=agent.CLAUDE_TOOLS)
    assert [t["name"] for t in calls[0]["tools"]] == ["read_file"]


def test_empty_profile_permissions_never_fall_back_to_registry(bound, monkeypatch):
    profile, session, _descriptor = bound
    profile["allowed_tools"] = []
    calls = []
    monkeypatch.setattr(agent, "_call_openai", lambda *a, **kw: calls.append(kw) or ("answer", []))
    agent._generate_agent_untraced([], system="bounded", session_ctx=session,
                                  tools=agent.CLAUDE_TOOLS)
    assert calls[0]["tools"] == []


def test_local_only_blocks_cloud_crew_before_provider_calls(bound, monkeypatch):
    _profile, session, _descriptor = bound
    monkeypatch.setattr(agent, "_call_openai", lambda *a, **kw: pytest.fail("cloud must not run"))
    monkeypatch.setattr(agent, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})
    with pytest.raises(RuntimeError, match="Local-only"):
        agent._generate_agent_untraced([], system="bounded", session_ctx=session, tools=[])
    monkeypatch.setattr(agent, "_load_settings", lambda: {})
    from agent_friday.services.local_only_guard import local_only, CloudRefused
    with local_only("test local job"), pytest.raises(CloudRefused):
        agent._generate_agent_untraced([], system="bounded", session_ctx=session, tools=[])


def test_native_claude_missing_key_does_not_use_one_key_fallback(monkeypatch):
    monkeypatch.setattr(agent, "get_anthropic_client", lambda: None)
    monkeypatch.setattr(agent, "_guest_client_for_turn", lambda client, sc: (client, None))
    from agent_friday.services import one_key
    monkeypatch.setattr(one_key, "openrouter_instead", lambda *a, **kw: pytest.fail("another provider must not be selected"))
    with pytest.raises(RuntimeError, match="No other provider"):
        agent._call_claude_agent_run([], model="native-test", tools=[],
            session_ctx={"crew_binding": {"provider": "anthropic", "model": "native-test"}})


def test_spawn_refuses_inherited_cloud_restrictions_before_task_creation(bound, monkeypatch):
    from agent_friday.services.local_only_guard import local_only, cloud_pinned
    from agent_friday.user_errors import UserFacingPermissionError
    profile, _session, _descriptor = bound
    binding = {"agent_id": profile["id"], "revision": 1, "project_id": None}
    monkeypatch.setattr(agent.uuid, "uuid4", lambda: pytest.fail("must refuse before creating a task ID"))
    with local_only("local schedule"), pytest.raises(UserFacingPermissionError, match="cloud restrictions"):
        agent._spawn_task("Crew", "request", runner=lambda tid: {}, crew_context=binding)
    profile["provider"] = "openrouter"
    with cloud_pinned("different-model", "pinned schedule"), pytest.raises(UserFacingPermissionError, match="model pin"):
        agent._spawn_task("Crew", "request", runner=lambda tid: {}, crew_context=binding)


@pytest.mark.parametrize("record", [
    {"cloud_pin": {"model": "model-test", "label": "pinned job"}},
    {"local_only": {"label": "local job"}},
])
def test_runner_restores_spawning_thread_restrictions(monkeypatch, record):
    import contextlib
    from agent_friday.services import reasoning_trace, local_only_guard
    monkeypatch.setitem(agent.TASKS, "scoped-runner-test", record)
    monkeypatch.setattr(reasoning_trace, "scope", lambda *a, **kw: contextlib.nullcontext())
    observed = []
    monkeypatch.setattr(agent, "_runner_task_worker_untraced", lambda *a, **kw:
                        observed.append((local_only_guard.pin_snapshot(), local_only_guard.local_only_snapshot())))
    agent._runner_task_worker("scoped-runner-test", lambda tid: {})
    assert observed == [(record.get("cloud_pin"), record.get("local_only"))]
    assert local_only_guard.pin_snapshot() is None
    assert local_only_guard.local_only_snapshot() is None


def test_runner_trace_covers_restriction_setup_failure(monkeypatch):
    import contextlib
    from agent_friday.services import reasoning_trace

    events = []

    @contextlib.contextmanager
    def trace(*args, **kwargs):
        events.append("started")
        try:
            yield
        except RuntimeError as exc:
            events.append(str(exc))
            raise
        finally:
            events.append("finished")

    def fail_restriction(*args):
        raise RuntimeError("saved restriction unavailable")

    monkeypatch.setitem(agent.TASKS, "restriction-failure-test", {})
    monkeypatch.setattr(reasoning_trace, "scope", trace)
    monkeypatch.setattr(agent, "_task_local_only_label", fail_restriction)
    monkeypatch.setattr(agent, "_runner_task_worker_untraced",
                        lambda *a, **kw: pytest.fail("runner must not start"))
    with pytest.raises(RuntimeError, match="saved restriction unavailable"):
        agent._runner_task_worker("restriction-failure-test", lambda tid: {})
    assert events == ["started", "saved restriction unavailable", "finished"]


def _hook_setup(bound, monkeypatch):
    profile, session, _descriptor = bound
    task = {"status": "running", "crew_tool_calls": 0,
            "crew_context": {"agent_id": profile["id"], "revision": 1, "project_id": None}}
    monkeypatch.setitem(agent.TASKS, session["task_id"], task)
    monkeypatch.setattr(agent, "_journal", lambda: SimpleNamespace(stop_requested=lambda tid: False))
    monkeypatch.setattr(crew_access, "authorize_tool", lambda *a, **kw: (True, ""))
    from agent_friday.governance import action_gate
    receipts = []
    monkeypatch.setattr(action_gate, "_receipt", lambda rec: receipts.append(rec))
    ctx = agent._hooks.HookContext("read_file", {"path": "synthetic"}, session_ctx=session)
    return task, ctx, receipts


def test_critical_hook_counts_tool_steps_and_receipts_denial(bound, monkeypatch):
    task, ctx, receipts = _hook_setup(bound, monkeypatch)
    assert agent._hook_crew_access(ctx).action == "allow"
    assert agent._hook_crew_access(ctx).action == "allow"
    assert agent._hook_crew_access(ctx).action == "deny"
    assert task["crew_tool_calls"] == 2
    assert receipts[-1]["decision"] == "deny"


def test_critical_hook_denies_missing_identity_cancel_and_timeout(bound, monkeypatch):
    task, ctx, _receipts = _hook_setup(bound, monkeypatch)
    ctx.session_ctx["crew_agent_id"] = "crew-" + "b" * 16
    assert agent._hook_crew_access(ctx).action == "deny"
    ctx.session_ctx["crew_agent_id"] = bound[0]["id"]
    task["status"] = "cancelled"
    assert agent._hook_crew_access(ctx).action == "deny"
    task["status"] = "running"
    ctx.session_ctx["crew_started"] = time.monotonic() - 100
    assert agent._hook_crew_access(ctx).action == "deny"


@pytest.mark.parametrize("private,generation", [(True, 4), (False, 5), (True, 5)])
def test_critical_hook_refuses_tools_after_crew_privacy_boundary(bound, monkeypatch, private, generation):
    from agent_friday.services import off_record
    task, ctx, receipts = _hook_setup(bound, monkeypatch)
    task["crew_context"].update(off_record=False, off_record_generation=4)
    monkeypatch.setattr(off_record, "active", lambda: private)
    monkeypatch.setattr(off_record, "generation", lambda: generation)
    monkeypatch.setattr(crew_access, "authorize_tool", lambda *a, **kw:
                        pytest.fail("Expired Crew work must not reach tool authorization"))
    assert agent._hook_crew_access(ctx).action == "deny"
    assert task["crew_tool_calls"] == 0
    assert receipts[-1]["decision"] == "deny"


def test_interrupted_or_old_persisted_crew_approval_cannot_restart_work(bound, monkeypatch):
    task, ctx, _receipts = _hook_setup(bound, monkeypatch)
    task["status"] = "interrupted"
    assert agent._hook_crew_access(ctx).action == "deny"
    task["status"] = "complete"
    task["created"] = time.time() - 1000
    assert agent._hook_crew_access(ctx).action == "deny"
    task["created"] = time.time()
    ctx.session_ctx["crew_started"] = time.monotonic() + 1000
    assert agent._hook_crew_access(ctx).action == "deny"


def test_deferred_approval_rechecks_original_crew_revision_and_task(bound, monkeypatch):
    from agent_friday.services import approvals
    from agent_friday.user_errors import UserFacingPermissionError
    task, original, _receipts = _hook_setup(bound, monkeypatch)
    payload = agent._approval_tool_payload(original.tool_name, original.input, original.session_ctx)
    monkeypatch.setattr(approvals, "get_approval", lambda aid: {"payload": payload})
    def deferred():
        return agent._hooks.HookContext("read_file", {"path": "synthetic"},
            session_ctx={"authenticated": True, "approved_card": "test-approval"})
    assert agent._hook_crew_access(deferred()).action == "allow"
    payload["crew_context"]["crew_started"] = time.monotonic() - 100
    assert agent._hook_crew_access(deferred()).action == "deny"
    payload["crew_context"]["crew_started"] = time.monotonic()
    task["status"] = "cancelled"
    assert agent._hook_crew_access(deferred()).action == "deny"
    task["status"] = "running"
    def revoked(*a, **kw):
        raise UserFacingPermissionError("The profile revision changed.")
    monkeypatch.setattr(crew_access, "validate_dispatch", revoked)
    assert agent._hook_crew_access(deferred()).action == "deny"
    monkeypatch.delitem(agent.TASKS, original.session_ctx["task_id"])
    assert agent._hook_crew_access(deferred()).action == "deny"


def test_both_approval_builders_preserve_only_trusted_crew_context(bound, monkeypatch):
    from agent_friday.services import approvals
    _task, ctx, _receipts = _hook_setup(bound, monkeypatch)
    ctx.session_ctx["conversation_id"] = "conv-crew-test"
    ctx.session_ctx["unrelated"] = "must not persist"
    captured = []
    monkeypatch.setattr(approvals, "create_approval", lambda **kw:
                        captured.append(kw) or {"approval_id": "test-approval", "status": "pending"})
    monkeypatch.setattr(approvals, "find_for_subject", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_gate_policy_class", lambda *a: "outward")
    monkeypatch.setitem(agent._PENDING_CONFIRMATIONS, "crew-session-test", {"fp": {}})
    agent._escalate_confirmation("crew-session-test", "read_file", ctx.input, "fp", "Read?",
                                 session_ctx=ctx.session_ctx)
    decision = SimpleNamespace(warn=[], flags=[])
    assert agent._taint_card(ctx, decision, "crew-test-key").action == "deny"
    assert len(captured) == 2
    for card in captured:
        saved = card["payload"]["crew_context"]
        assert saved == {key: ctx.session_ctx[key] for key in agent._CREW_CARD_FIELDS}
        assert "unrelated" not in saved
        assert card["payload"]["conversation_id"] == "conv-crew-test"


def test_host_tools_use_active_conversation_and_no_model_supplied_request_id(monkeypatch):
    from agent_friday.services import crew_runtime, off_record
    monkeypatch.setattr(off_record, "active", lambda: False)
    monkeypatch.setattr(off_record, "generation", lambda: 7)
    calls = []
    monkeypatch.setattr(crew_runtime, "ask", lambda *a, **kw: calls.append((a, kw)) or {"task_id": "real-task"})
    state = agent._CURRENT_CONVERSATION.set("conv-test")
    origin_token = crew_runtime.HOST_ORIGIN.set(crew_runtime.capture_host_origin())
    try:
        assert agent._tool_ask_crew({"agent": "Researcher", "request": "Check evidence", "request_id": "spoofed"})["task_id"] == "real-task"
    finally:
        crew_runtime.HOST_ORIGIN.reset(origin_token)
        agent._CURRENT_CONVERSATION.reset(state)
    assert calls == [(("conv-test", "Researcher", "Check evidence"), {})]


@pytest.mark.parametrize("private_at_start", [False, True])
@pytest.mark.parametrize("tool_name", ["ask_crew", "ask-crew"])
def test_late_private_host_delegation_refuses_before_tool_parsing_or_hooks(monkeypatch, private_at_start, tool_name):
    from agent_friday.services import crew_runtime, off_record
    state = {"private": private_at_start, "generation": 3}
    monkeypatch.setattr(off_record, "active", lambda: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    origin = crew_runtime.capture_host_origin()
    state.update(private=False, generation=4)
    monkeypatch.setattr(agent._tool_args, "check", lambda *a, **kw:
                        pytest.fail("Private input must be refused before parsing or logging"))
    monkeypatch.setattr(agent._hooks, "run_pre_hooks", lambda *a, **kw:
                        pytest.fail("Private input must not reach ordinary hooks"))
    result = agent._execute_tool(tool_name, {"request": "Private host material"},
                                session_ctx={"_crew_host_origin": origin})
    assert "CREW DENY" in result and "Private host material" not in result


def test_crew_host_origin_cannot_be_forged_by_json_fields(monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda: False)
    monkeypatch.setattr(off_record, "generation", lambda: 3)
    result = agent._execute_tool("ask_crew", {"agent": "Reviewer", "request": "Work"},
        session_ctx={"_crew_host_origin": {"off_record": False, "generation": 3}})
    assert "original privacy state was not recorded" in result


def test_host_origin_captures_generation_before_private_mode_probe(monkeypatch):
    from agent_friday.services import crew_runtime, off_record
    generation = [3]
    monkeypatch.setattr(off_record, "generation", lambda: generation[0])
    def ended_during_probe():
        generation[0] = 4
        return False
    monkeypatch.setattr(off_record, "active", ended_during_probe)
    origin = crew_runtime.capture_host_origin()
    assert origin.generation == 3
    with pytest.raises(crew_runtime.CrewRoomError):
        crew_runtime.require_public_host_origin(origin)


@pytest.mark.parametrize("binding", [{"agent_id": "crew-" + "a" * 16}, {}])
def test_crew_cannot_enter_generic_task_resume(monkeypatch, binding):
    from agent_friday.services import task_resume
    monkeypatch.setattr(task_resume, "_journal", lambda: SimpleNamespace(
        read_state=lambda tid: {"crew_context": binding}))
    monkeypatch.setattr(task_resume, "_checkpoint_resumability", lambda tid:
                        pytest.fail("Crew must refuse before reading a generic checkpoint"))
    verdict = task_resume.resumability("crew-resume-test")
    assert verdict["resumable"] is False
    assert "fresh Crew turn" in verdict["reason"]
    with pytest.raises(task_resume.ResumeRefused, match="fresh Crew turn"):
        task_resume.resume("crew-resume-test", confirm_pending=True)


def test_unreadable_task_authority_cannot_resume_as_unscoped(monkeypatch):
    from agent_friday.services import task_resume
    monkeypatch.setattr(task_resume, "_journal", lambda: SimpleNamespace(
        read_state=lambda tid: None, blob_exists=lambda *a: True))
    monkeypatch.setattr(task_resume, "_checkpoint_resumability", lambda tid:
                        pytest.fail("Unknown task authority must not resume"))
    assert task_resume.resumability("unreadable-state")["resumable"] is False

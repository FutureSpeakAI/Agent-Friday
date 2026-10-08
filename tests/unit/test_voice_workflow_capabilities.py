"""Capability parity at the schemas and governed call boundary actually used."""
import json

import pytest

from agent_friday.services import agent, voice_engine as voice, workflow_tools as tools


def test_workflow_is_present_after_actual_live_schema_conversion():
    types = pytest.importorskip("google.genai").types
    declarations = {d.name: d for bundle in voice._build_voice_live_tools(types)
                    for d in bundle.function_declarations}
    assert {"workflow_action", "discover_capabilities", "read_skill", "voice_preferences"} <= declarations.keys()
    spec = declarations["workflow_action"].parameters
    assert "action" in spec.required
    assert {"create", "update", "inspect", "repeat", "learn", "restore", "delete", "retry_delivery"} <= set(spec.properties["action"].enum)
    assert spec.properties["unschedule"].type.value == "BOOLEAN"
    assert "prompt" in spec.properties["steps"].items.required
    assert spec.properties["steps"].items.properties["prompt"].type.value == "STRING"
    assert spec.properties["when"].properties["spec"].properties["weekdays"].items.type.value == "INTEGER"
    assert spec.properties["output"].properties["kind"].enum == ["reply", "artifact", "file", "code"]
    assert declarations["voice_preferences"].parameters.properties["pace"].enum == ["adaptive", "measured", "natural", "brisk"]
    local = {entry["function"]["name"]: entry["function"]
             for entry in voice.build_voice_tool_contract()["tools"]}
    assert local["workflow_action"]["parameters"]["properties"]["steps"]["items"]["required"] == ["name", "prompt"]


def test_spoken_workflow_uses_governed_executor_and_restores_conversation(monkeypatch):
    calls = []
    def execute(name, args, **kwargs):
        calls.append((name, args, agent._CURRENT_CONVERSATION.get(), kwargs["session_ctx"]))
        return "queued"
    monkeypatch.setattr(agent, "_execute_tool", execute)
    token = agent._CURRENT_CONVERSATION.set("conv-before")
    try:
        reply = voice._voice_tool_run("workflow_action", {"action": "run", "slug": "sample"},
                                      lambda frame: None, {"engine": "local", "conversation_id": "conv-spoken"})
        assert reply == "queued"
        assert calls[0][:3] == ("workflow_action", {"action": "run", "slug": "sample"}, "conv-spoken")
        assert agent._CURRENT_CONVERSATION.get() == "conv-before"
        assert calls[0][3]["is_voice"] is True
    finally:
        agent._CURRENT_CONVERSATION.reset(token)


def test_workflow_wrapper_uses_verified_origin_and_rejects_spoofed_owner(monkeypatch):
    from agent_friday.services import conversations, workflow_operations
    monkeypatch.setattr(conversations, "load", lambda cid: {"id": cid, "project": "project-one"})
    called = []
    monkeypatch.setattr(workflow_operations, "execute", lambda action, args, context: called.append((action, args, context)) or {"status": "ok"})
    token = agent._CURRENT_CONVERSATION.set("conv-one")
    try:
        tools.workflow_action({"action": "run", "slug": "sample"})
        assert called[0][2]["conversation_id"] == "conv-one"
        assert called[0][2]["project_id"] == "project-one"
        tools.workflow_action({"action": "update", "slug": "sample", "unschedule": True})
        assert called[-1][1]["when"] is None
        with pytest.raises(ValueError, match="originating"):
            tools.workflow_action({"action": "run", "project_id": "project-other"})
        with pytest.raises(ValueError, match="originating"):
            tools.workflow_action({"action": "update", "project_id": None})
        with pytest.raises(ValueError, match="nested draft"):
            tools.workflow_action({"action": "create", "draft": {
                "conversation_id": "conv-other", "project_id": "project-other"}})
        assert len(called) == 2
    finally:
        agent._CURRENT_CONVERSATION.reset(token)


def test_actual_draft_maps_the_spoken_request_without_saving(monkeypatch):
    from agent_friday.services import workflow_overview
    monkeypatch.setattr(tools, "_context", lambda: {})
    monkeypatch.setattr(workflow_overview, "save", lambda *a, **k: pytest.fail("Draft must not save"))
    result = json.loads(tools.workflow_action({"action": "draft", "request": "Every weekday at 9 am summarize the project notes"}))
    assert result["status"] == "ok"
    assert result["draft"]["steps"]
    assert result["draft"]["when"]["trigger"] == "weekly"
    assert "nothing has been saved" in result["note"]


def test_trusted_worker_authority_is_not_dropped_by_capability_adapter(monkeypatch):
    from agent_friday.services import conversations
    trusted = {"is_background_task": True, "task_id": "task-example", "schedule_id": "schedule-example", "agent_id": "profile-example"}
    monkeypatch.setattr(agent, "_workflow_caller_context", lambda: dict(trusted))
    monkeypatch.setattr(conversations, "load", lambda cid: {"project": "project-one"})
    token = agent._CURRENT_CONVERSATION.set("conv-one")
    try:
        context = tools._context()
        assert all(context[key] == value for key, value in trusted.items())
        assert context["conversation_id"] == "conv-one"
        assert context["project_id"] == "project-one"
    finally:
        agent._CURRENT_CONVERSATION.reset(token)


def test_full_skill_body_includes_late_verification_and_scope(monkeypatch):
    from agent_friday import skill_registry as registry
    body = "Preparation. " * 300 + "Verify the artifact and never publish without the user's instruction."
    skill = registry.Skill(name="sample", description="Synthetic procedure", body=body,
                           triggers=["sample"], success_criteria=["Verified artifact"])
    monkeypatch.setattr(registry, "load_skills", lambda dirs=None: [skill])
    index = registry.build_injection("sample")
    assert "read_skill" in index and "Preparation." not in index
    result = json.loads(tools.read_skill({"name": "sample"}))
    assert result["procedure"] == body
    assert result["success_criteria"] == ["Verified artifact"]


def test_discovery_never_calls_registered_unchecked_tool_ready(monkeypatch):
    monkeypatch.setattr(tools, "_registry", lambda: {"sample_tool": {
        "name": "sample_tool", "description": "Needs a connection. Do this after setup.",
        "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}}}})
    result = json.loads(tools.discover_capabilities({"name": "sample_tool"}))
    assert result["registered"] is True and "not yet checked" in result["availability"]
    assert result["instructions"]["description"].endswith("Do this after setup.")
    assert json.loads(tools.discover_capabilities({"name": "missing"}))["registered"] is False


def test_scoped_discovery_exposes_public_schema_but_not_private_workflows_or_skills(monkeypatch):
    from agent_friday import skill_registry
    from agent_friday.services import workflow_operations
    monkeypatch.setattr(agent, "_workflow_caller_context", lambda: {"nested_execution": True, "task_id": "task-example"})
    monkeypatch.setattr(tools, "_registry", lambda: {s["name"]: s for s in tools.TOOL_SCHEMAS})
    monkeypatch.setattr(skill_registry, "load_skills", lambda *a, **k: pytest.fail("Scoped caller read private skill registry"))
    monkeypatch.setattr(workflow_operations, "execute", lambda *a, **k: pytest.fail("Scoped caller read global workflow state"))
    listing = json.loads(tools.discover_capabilities({}))
    assert "skills" not in listing and "unavailable" in listing["skills_status"]
    detail = json.loads(tools.discover_capabilities({"name": "workflow_action"}))
    assert detail["instructions"]["name"] == "workflow_action"
    assert "live_status" not in detail and "unavailable" in detail["availability"]
    with pytest.raises(ValueError, match="scoped background"):
        tools.read_skill({"name": "other-project-procedure"})


def test_scoped_task_cannot_persist_voice_preferences_but_may_inspect_them(monkeypatch):
    from agent_friday import core
    from agent_friday.services import voice_delivery
    monkeypatch.setattr(agent, "_workflow_caller_context", lambda: {"nested_execution": True, "task_id": "task-example"})
    writes = []
    monkeypatch.setattr(core, "_save_settings", lambda data: writes.append(data))
    monkeypatch.setattr(voice_delivery, "settings_snapshot", lambda: {"voice_speaking_pace": "natural"})
    for action in ("set", "reset"):
        with pytest.raises(ValueError, match="scoped background.*lasting voice preferences"):
            tools.voice_preferences({"action": action, "scope": "default", "pace": "measured"})
    assert not writes
    result = json.loads(tools.voice_preferences({"action": "inspect", "scope": "default"}))
    assert result["pace"] == "natural" and not writes


@pytest.mark.parametrize("allowed", [False, True])
def test_live_voice_preferences_wait_for_governance_and_keep_call_binding(monkeypatch, allowed):
    from agent_friday import core
    from agent_friday.services import model_router, voice_delivery as delivery
    events = []
    defaults = {"voice_response_depth": "adaptive", "voice_speaking_pace": "natural"}
    session = {"conversation_id": "voice-synthetic", "engine": "gemini"}
    delivery.initialize_session_preferences(session, defaults)
    original_action = delivery.preference_action

    def preference_action(inp, bound_session):
        events.append("handler")
        assert bound_session is session
        return original_action(inp, bound_session)

    def pre(ctx):
        events.append("gate")
        assert ctx.tool_name == "voice_preferences"
        assert ctx.session_ctx["conversation_id"] == session["conversation_id"]
        return agent._hooks.ALLOW if allowed else agent._hooks.DENY("Synthetic preference denial")

    monkeypatch.setattr(delivery, "preference_action", preference_action)
    monkeypatch.setattr(delivery, "settings_snapshot", lambda: dict(defaults))
    monkeypatch.setattr(agent._hooks, "run_pre_hooks", pre)
    monkeypatch.setattr(agent._hooks, "run_post_hooks", lambda ctx, result: result)
    monkeypatch.setattr(agent._receipts, "record", lambda *a, **kw: None)
    monkeypatch.setattr(agent._tool_output, "clip_result", lambda name, result: result)
    monkeypatch.setattr(model_router, "announce_tool", lambda *a, **kw: None)
    monkeypatch.setattr(core, "_save_settings", lambda data: pytest.fail("Call preference persisted"))
    reply = voice._voice_tool_run("voice_preferences", {
        "action": "set", "scope": "session", "depth": "detailed", "pace": "measured"},
        lambda frame: None, session)
    if allowed:
        assert events == ["gate", "handler"]
        assert json.loads(reply)["scope"] == "session"
        assert delivery.session_preferences(session) == {
            "voice_response_depth": "detailed", "voice_speaking_pace": "measured"}
    else:
        assert events == ["gate"]
        assert reply == "Synthetic preference denial"
        assert delivery.session_preferences(session) == defaults

"""Repository and domain actions retain scope, exact navigation and voice parity."""
import copy
import json

import pytest

from agent_friday.services import agent, sites_tools as tools, voice_engine as voice


@pytest.fixture
def caller(monkeypatch):
    from agent_friday.services import sites_privacy
    context = {"conversation_id": "chat-sample", "project_id": "project-sample", "nested_execution": False,
               "_sites_origin": "synthetic-trusted-origin"}
    monkeypatch.setattr(agent, "_workflow_caller_context", lambda: copy.deepcopy(context))
    monkeypatch.setattr(sites_privacy, "require_tool_origin", lambda: "synthetic-trusted-origin")
    monkeypatch.setattr(sites_privacy, "admit", lambda context: 1)
    return context


@pytest.mark.parametrize("name,service_name", [("site_action", "sites_operations"), ("domain_action", "domain_operations")])
def test_registered_actions_use_actual_caller_and_reject_spoofed_authority(monkeypatch, caller, name, service_name):
    from agent_friday import services
    from importlib import import_module
    service = import_module("agent_friday.services." + service_name)
    calls = []
    monkeypatch.setattr(service, "execute", lambda action, args, context: calls.append((action, args, context)) or {"status": "ok"})
    handler = tools.TOOL_HANDLERS[name]
    assert json.loads(handler({"action": "inspect"}))["status"] == "ok"
    assert calls[0][2] == caller
    for key in ("conversation_id", "project_id", "surface", "nested_execution", "approval_id", "token", "username"):
        with pytest.raises(ValueError, match="Unsupported"):
            handler({"action": "inspect", key: "injected"})
    assert len(calls) == 1
    assert getattr(services, service_name) is service
    assert agent.CLAUDE_TOOL_HANDLERS[name] is handler


@pytest.mark.parametrize("name", ["site_action", "domain_action"])
@pytest.mark.parametrize("context", [{}, {"conversation_id": "chat-sample", "nested_execution": True}])
def test_scoped_or_missing_callers_never_reach_service(monkeypatch, name, context):
    from agent_friday.services import sites_operations, domain_operations
    monkeypatch.setattr(agent, "_workflow_caller_context", lambda: context)
    for service in (sites_operations, domain_operations):
        monkeypatch.setattr(service, "execute", lambda *args: pytest.fail("Unauthorized service call"))
    with pytest.raises(ValueError):
        tools.TOOL_HANDLERS[name]({"action": "inspect"})


def test_domains_do_not_accept_credentials_inside_nested_records(monkeypatch, caller):
    # The service, not only the model schema, rejects unknown nested record keys.
    from agent_friday.services import namecom
    with pytest.raises(ValueError):
        namecom.record_payload({"host": "www", "type": "A", "answer": "192.0.2.1", "ttl": 300, "token": "not-a-secret"})


@pytest.mark.parametrize("ack,status", [({"opened": True, "matched": True}, "opened"),
                                       ({"opened": True, "matched": False}, "unconfirmed"),
                                       ({"opened": True}, "unconfirmed"),
                                       ({"opened": False}, "not_opened")])
def test_exact_site_navigation_needs_matching_desktop_ack(monkeypatch, caller, ack, status):
    from agent_friday.services import desktop_bus, sites_operations
    inspected, sent = [], []
    monkeypatch.setattr(sites_operations, "execute", lambda action, args, context:
                        inspected.append((action, args, context)) or {"status": "ok", "site": {"site_id": "site-accepted"}})
    monkeypatch.setattr(desktop_bus, "send", lambda actions, **kwargs:
                        sent.append((actions, kwargs)) or {"delivered": True, "acked": True, "ack": ack})
    result = json.loads(tools.site_action({"action": "open", "site_id": "site-input"}))
    assert inspected == [("inspect", {"site_id": "site-input"}, caller)]
    assert sent[0][0] == [{"type": "navigate", "workspace": "futurespeak", "site_id": "site-accepted"}]
    assert sent[0][1]["verify"]["value"] == "site-accepted"
    assert result["status"] == status


def test_denied_site_never_navigates(monkeypatch, caller):
    from agent_friday.services import desktop_bus, sites_operations
    def refuse(*args):
        raise ValueError("This site belongs to another chat.")
    monkeypatch.setattr(sites_operations, "execute", refuse)
    monkeypatch.setattr(desktop_bus, "send", lambda *a, **k: pytest.fail("Denied site opened"))
    with pytest.raises(ValueError, match="another chat"):
        tools.site_action({"action": "open", "site_id": "foreign-site"})


def test_preview_navigates_exact_build_without_returning_a_capability(monkeypatch, caller):
    from agent_friday.services import desktop_bus, sites_operations, site_previews
    calls, sends = [], []
    monkeypatch.setattr(sites_operations, "execute", lambda action, args, context:
                        calls.append((action, args, context)) or
                        {"status": "ok", "site_id": "site-accepted", "site_revision": 2, "build_id": "build-accepted"})
    ticket = "n" + "1" * 48
    tickets = []
    monkeypatch.setattr(site_previews, "prepare_navigation", lambda sid, revision, bid, **kwargs:
                        tickets.append((sid, revision, bid, kwargs)) or ticket)
    monkeypatch.setattr(desktop_bus, "send", lambda actions, **kwargs:
                        sends.append((actions, kwargs)) or
                        {"delivered": True, "acked": True, "ack": {"opened": True, "matched": True}})
    result = json.loads(tools.site_action({"action": "preview", "site_id": "site-accepted", "build_id": "build-accepted"}))
    assert calls == [("preview", {"site_id": "site-accepted", "build_id": "build-accepted"}, caller)]
    assert tickets == [("site-accepted", 2, "build-accepted", {"origin": caller["_sites_origin"]})]
    assert sends[0][0] == [{"type": "navigate", "workspace": "futurespeak", "site_id": "site-accepted", "preview_build_id": "build-accepted", "preview_request_id": ticket}]
    assert sends[0][1]["verify"] == {"workspace": "futurespeak", "key": "preview_build_id", "value": "build-accepted"}
    assert result["status"] == "opened"
    assert "not been verified" in result["message"]
    assert "preview_url" not in json.dumps(result)
    assert "preview_request_id" not in json.dumps(result) and ticket not in json.dumps(result)


def test_domain_navigation_requires_exact_selected_account_inventory(monkeypatch, caller):
    from agent_friday.services import desktop_bus, domain_operations
    caller["project_id"] = None
    calls, sends = [], []
    monkeypatch.setattr(domain_operations, "execute", lambda action, args, context:
                        calls.append((action, args, context)) or {"status": "ok", "inventory": [{"domain": "sample.example"}]})
    monkeypatch.setattr(desktop_bus, "send", lambda actions, **kwargs:
                        sends.append((actions, kwargs)) or {"delivered": True, "acked": False})
    result = json.loads(tools.domain_action({"action": "open", "account_id": "account-sample", "domain": "sample.example"}))
    assert calls[0][:2] == ("inventory", {"account_id": "account-sample"})
    assert result["status"] == "unconfirmed"
    assert sends[0][0][0]["account_id"] == "account-sample"
    assert sends[0][1]["verify"] == {"workspace": "futurespeak", "key": "domain_ref", "value": "account-sample/sample.example"}
    with pytest.raises(ValueError, match="not in the selected account"):
        tools.domain_action({"action": "open", "account_id": "account-sample", "domain": "missing.example"})
    assert len(sends) == 1


def test_unexpected_provider_errors_are_not_returned_to_chat(monkeypatch, caller):
    from agent_friday.services import domain_operations
    def fail(*args):
        raise RuntimeError("credential-echo-sentinel")
    monkeypatch.setattr(domain_operations, "execute", fail)
    with pytest.raises(ValueError) as exc:
        tools.domain_action({"action": "sync", "account_id": "account-sample"})
    assert "credential-echo-sentinel" not in str(exc.value)
    assert "could not be confirmed" in str(exc.value)


def test_sites_tools_survive_actual_live_and_local_schema_conversion():
    types = pytest.importorskip("google.genai").types
    declarations = {item.name: item for bundle in voice._build_voice_live_tools(types) for item in bundle.function_declarations}
    assert {"site_action", "domain_action"} <= declarations.keys()
    assert declarations["site_action"].parameters.properties["hosting"].nullable is True
    assert declarations["site_action"].parameters.properties["domain"].nullable is True
    domain = declarations["domain_action"].parameters
    assert "verify" in domain.properties["action"].enum
    assert "open" in domain.properties["action"].enum
    assert domain.properties["record"].properties["type"].enum == tools.RECORD_SCHEMA["properties"]["type"]["enum"]
    assert domain.properties["records"].items.required == ["host", "type", "answer", "ttl"]
    local = {entry["function"]["name"]: entry["function"] for entry in voice.build_voice_tool_contract()["tools"]}
    for name in ("site_action", "domain_action"):
        expected = tools._schema(name)
        assert local[name]["parameters"]["properties"]["action"]["enum"] == expected["properties"]["action"]["enum"]
        assert "review" in local[name]["description"]
        assert "discover_capabilities" in local[name]["description"]
    assert "checkout" in local["domain_action"]["description"]
    assert "uncertain" in local["domain_action"]["description"]
    assert "saved model, permissions and voice" in local["ask_crew"]["description"]
    assert "never impersonate" in local["ask_crew"]["description"]
    assert "unsaved" in local["propose_crew_agent"]["description"]
    assert "NAV_OK" in local["navigate_to"]["description"]


@pytest.mark.parametrize("name", ["site_action", "domain_action"])
def test_spoken_sites_action_runs_governance_in_spoken_conversation(monkeypatch, name):
    calls = []
    def execute(tool, args, **kwargs):
        calls.append((tool, args, agent._CURRENT_CONVERSATION.get(), kwargs["session_ctx"]))
        return "inspected"
    monkeypatch.setattr(agent, "_execute_tool", execute)
    token = agent._CURRENT_CONVERSATION.set("chat-before")
    try:
        reply = voice._voice_tool_run(name, {"action": "inspect"}, lambda frame: None,
                                      {"engine": "local", "conversation_id": "chat-spoken"})
        assert reply == "inspected"
        assert calls[0][:3] == (name, {"action": "inspect"}, "chat-spoken")
        assert calls[0][3]["is_voice"] is True
        assert agent._CURRENT_CONVERSATION.get() == "chat-before"
    finally:
        agent._CURRENT_CONVERSATION.reset(token)


def test_discovery_exposes_full_procedure_and_honest_limits(monkeypatch):
    from agent_friday.services import workflow_tools
    monkeypatch.setattr(workflow_tools, "_registry", lambda: {item["name"]: item for item in tools.TOOL_SCHEMAS})
    for name, guide in (("site_action", tools.SITE_GUIDE), ("domain_action", tools.DOMAIN_GUIDE)):
        result = json.loads(workflow_tools.discover_capabilities({"name": name}))
        assert result["guide"] == guide
        assert "not yet checked" in result["availability"]


@pytest.mark.parametrize("name", ["site_action", "domain_action", "site action", "domain action"])
@pytest.mark.parametrize("private,generation", [(True, 9), (False, 8)])
def test_ended_private_origin_is_refused_before_tool_instrumentation(monkeypatch, name, private, generation):
    from agent_friday.services import crew_runtime, off_record
    monkeypatch.setattr(off_record, "active", lambda *args, **kwargs: False)
    monkeypatch.setattr(off_record, "generation", lambda: 9)
    monkeypatch.setattr(agent._hooks, "HookContext", lambda *a, **k: pytest.fail("Private action entered instrumentation"))
    monkeypatch.setattr(agent._receipts, "record", lambda *a, **k: pytest.fail("Private arguments reached receipt logging"))
    result = agent._execute_tool(name, {"action": "inspect"},
                                 session_ctx={"_crew_host_origin": crew_runtime.CrewHostOrigin(private, generation)})
    assert result.startswith("[SITES DENY]")


def test_direct_handler_cannot_drop_original_private_origin(monkeypatch):
    from agent_friday.services import crew_runtime, off_record, sites_operations
    monkeypatch.setattr(agent, "_workflow_caller_context", lambda: {"conversation_id": "chat-sample"})
    monkeypatch.setattr(off_record, "active", lambda *args, **kwargs: False)
    monkeypatch.setattr(off_record, "generation", lambda: 9)
    monkeypatch.setattr(sites_operations, "execute", lambda *a, **k: pytest.fail("Private origin reached Sites"))
    token = crew_runtime.HOST_ORIGIN.set(crew_runtime.CrewHostOrigin(True, 8))
    try:
        with pytest.raises(ValueError):
            tools.site_action({"action": "list"})
    finally:
        crew_runtime.HOST_ORIGIN.reset(token)


@pytest.mark.parametrize("name,extra", [("site_action", {"build_command": "unexpected"}),
                                      ("domain_action", {"record_id": 1})])
def test_open_rejects_unused_action_fields_before_access(monkeypatch, caller, name, extra):
    from agent_friday.services import sites_operations, domain_operations
    for service in (sites_operations, domain_operations):
        monkeypatch.setattr(service, "execute", lambda *a: pytest.fail("Invalid open reached service"))
    with pytest.raises(ValueError, match="Unsupported inputs"):
        tools.TOOL_HANDLERS[name]({"action": "open", **extra})


@pytest.mark.parametrize("transition", ["inspect", "navigation"])
def test_navigation_rechecks_origin_before_dispatch_and_after_ack(monkeypatch, caller, transition):
    from agent_friday.services import desktop_bus, sites_operations, sites_privacy
    stale, sends = [], []

    def admit(context):
        if stale:
            raise ValueError("This request has no current public privacy context.")
        return 1

    def inspect(*args):
        if transition == "inspect":
            stale.append(True)
        return {"site": {"site_id": "site-sample"}}

    def navigate(*args, **kwargs):
        sends.append(True)
        stale.append(True)
        return {"delivered": True, "acked": True, "ack": {"opened": True, "matched": True}}

    monkeypatch.setattr(sites_privacy, "admit", admit)
    monkeypatch.setattr(sites_operations, "execute", inspect)
    monkeypatch.setattr(desktop_bus, "send", navigate)
    with pytest.raises(ValueError, match="privacy context"):
        tools.site_action({"action": "open", "site_id": "site-sample"})
    assert len(sends) == (1 if transition == "navigation" else 0)

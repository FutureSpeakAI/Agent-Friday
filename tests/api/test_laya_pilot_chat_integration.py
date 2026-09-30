"""One advisory pilot ticket spans a chat turn, including retries and failures."""
from collections import Counter
from types import SimpleNamespace

import pytest

from agent_friday.routes import chat as chat_mod


class Ticket:
    arm = "assisted"
    def __init__(self):
        self.events = []
        self.counts = Counter()
        self.finished = []
        self.prepared = False
        self.execution = set()
    def plan(self):
        return "knowledge"
    def mark_prepared(self):
        self.prepared = True
        self.events.append("prepared")
    def increment(self, name, amount=1):
        self.counts[name] += amount
    def observe_execution(self, execution):
        self.execution.add(execution)
    def finish(self, **kwargs):
        self.finished.append(kwargs)


@pytest.fixture
def pilot(monkeypatch, patch_app):
    import agent_friday.services as services
    ticket = Ticket()
    starts = []
    def start(message, settings=None):
        if (settings or {}).get("off_record"):
            return None
        starts.append(message)
        ticket.events.append("started")
        return ticket
    monkeypatch.setattr(services, "laya_pilot", SimpleNamespace(start=start), raising=False)
    def context(*a, **kw):
        ticket.events.append("context")
        assert kw.get("pilot") is ticket
        return "Fixture context", []
    patch_app("_build_context_prompt", context)
    patch_app("_build_memory_context_block", lambda *a, **k: "")
    patch_app("_index_chat_turn", lambda *a, **k: None)
    return ticket, starts


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/send", "/api/chat/stream"])
def test_ticket_starts_before_context_and_survives_integrity_retry(client, monkeypatch, pilot, endpoint):
    from agent_friday.routing.model_router import ModelRouter
    from agent_friday.services import tool_catalogue
    ticket, starts = pilot
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {
        "provider": "local", "model": "fixture-local", "is_local": True,
        "vault_allowed": True, "vault_access": False, "scrub_pii": False,
        "refuse": False, "warning": None,
    })
    monkeypatch.setattr(tool_catalogue, "enabled", lambda: True)
    base_opening = tool_catalogue.opening_set
    prepared = []
    def opening(tools, pilot=None):
        prepared.append(pilot)
        return base_opening(tools)
    monkeypatch.setattr(tool_catalogue, "opening_set", opening)
    calls = []
    def dispatch(messages, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return "[query_calendar] shows an appointment.", []
        return "No calendar search was performed.", []
    monkeypatch.setattr(chat_mod, "_generate_agent" if endpoint == "/api/chat/send" else "_call_ollama", dispatch)
    response = client.post(endpoint, json={"message": "Use my wiki to explain this"})
    response.get_data()  # finish the worker on the streaming surface
    assert response.status_code == 200
    assert starts == ["Use my wiki to explain this"]
    assert ticket.events.index("started") < ticket.events.index("context")
    assert len(calls) == 2
    assert all(c["session_ctx"]["_laya_pilot"] is ticket for c in calls)
    assert len(ticket.finished) == 1
    if endpoint != "/api/chat/send":
        assert prepared == [ticket]


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/send"])
def test_context_failure_still_finishes_ticket(client, patch_app, pilot, endpoint):
    ticket, starts = pilot
    def fail(*a, **k):
        raise RuntimeError("fixture failure")
    patch_app("_build_context_prompt", fail)
    client.post(endpoint, json={"message": "Explain my notes"})
    assert len(starts) == 1
    assert len(ticket.finished) == 1
    assert ticket.finished[0].get("error") is True


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/send"])
def test_off_record_does_not_start_a_pilot(client, monkeypatch, patch_app, pilot, endpoint):
    ticket, starts = pilot
    settings = dict(chat_mod._load_settings(), off_record=True)
    patch_app("_load_settings", lambda: settings)
    patch_app("_build_context_prompt", lambda *a, **k: ("Fixture context", []))
    client.post(endpoint, json={"message": "Explain this"})
    assert starts == []
    assert ticket.finished == []


def test_stream_off_record_is_captured_before_worker(client, monkeypatch, patch_app, pilot):
    ticket, starts = pilot
    settings = dict(chat_mod._load_settings(), off_record=True)
    patch_app("_load_settings", lambda: settings)
    patch_app("_build_context_prompt", lambda *a, **k: ("Fixture context", []))
    real_thread = chat_mod.threading.Thread
    class DeferredThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            settings["off_record"] = False
            real_thread(target=self.target, daemon=True).start()
    monkeypatch.setattr(chat_mod.threading, "Thread", DeferredThread)
    response = client.post("/api/chat/stream", json={"message": "Explain this"})
    response.get_data()
    assert starts == []
    assert ticket.finished == []


@pytest.fixture
def loop_setup(monkeypatch):
    from agent_friday.services import agent as ag, compaction, tool_hooks
    monkeypatch.setattr(ag, "_get_vault_control", lambda: None)
    monkeypatch.setattr(ag, "_seal_or_block", lambda payload, provider: payload)
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    monkeypatch.setattr(tool_hooks, "run_pre_hooks", lambda ctx: tool_hooks.ALLOW)
    monkeypatch.setattr(tool_hooks, "run_post_hooks", lambda ctx, result: result)
    monkeypatch.setattr(compaction, "maybe_compact", lambda convo, **k: convo)
    monkeypatch.setattr(compaction, "compress_new_output", lambda convo, *a, **k: convo)
    return ag


def _reply(tool=None, arguments=None):
    message = {"role": "assistant", "content": "Done." if tool is None else ""}
    if tool:
        message["tool_calls"] = [{"id": "fixture-call", "type": "function", "function": {
            "name": tool, "arguments": arguments or {}}}]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if tool else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def test_oai_counts_model_attempts_loader_and_actual_handler_separately(monkeypatch, loop_setup):
    ag = loop_setup
    ticket = Ticket()
    handled = []
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "fixture_read", lambda args: handled.append(args) or "fixture result")
    responses = iter([_reply("load_tools", {"names": ["fixture_read"]}),
                      _reply("fixture_read", {"query": "fixture"}), _reply()])
    def send(convo, tools):
        assert ticket.prepared
        return next(responses)
    schema = {"name": "fixture_read", "description": "Read a fixture", "input_schema": {"type": "object", "properties": {}}}
    text, trace = ag._oai_agentic_loop(
        [{"role": "user", "content": "Read the fixture"}], [{"type": "function"}], send,
        provider="openai", seat="local", model="fixture-local", max_iters=4,
        catalogue_all=[schema], session_ctx={"_laya_pilot": ticket})
    assert text == "Done."
    assert ticket.counts == {"model_rounds": 3, "loader_calls": 1, "tool_calls": 1}
    assert ticket.execution == {"local"}
    assert len(handled) == 1
    assert [item["name"] for item in trace] == ["load_tools", "fixture_read"]
    assert ticket.finished == []  # only the owning route may finish


def test_denied_tool_is_neither_run_nor_counted(monkeypatch, loop_setup):
    from agent_friday.services import tool_hooks
    ag = loop_setup
    ticket = Ticket()
    handled = []
    monkeypatch.setattr(tool_hooks, "run_pre_hooks", lambda ctx: tool_hooks.DENY("fixture denied"))
    result = ag._execute_tool("fixture_read", {}, session_ctx={"_laya_pilot": ticket},
                              handler=lambda args: handled.append(args))
    assert result == "fixture denied"
    assert handled == []
    assert ticket.counts["tool_calls"] == 0


def test_native_loop_marks_prepared_and_counts_rounds(monkeypatch, loop_setup):
    ag = loop_setup
    ticket = Ticket()
    calls = []
    def create(**kwargs):
        assert ticket.prepared
        calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Done.")],
                               stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=2))
    monkeypatch.setattr(ag, "get_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    text, _ = ag._call_claude_agent([{"role": "user", "content": "Explain this"}],
                                    system="Fixture system", model="claude-sonnet-5-5",
                                    session_ctx={"_laya_pilot": ticket})
    assert text == "Done."
    assert len(calls) == 1
    assert ticket.counts == {"model_rounds": 1}
    assert ticket.execution == {"cloud"}
    assert ticket.finished == []


def test_dispatcher_reuses_ticket_across_local_to_cloud_fallback(monkeypatch, loop_setup):
    from agent_friday.services import demo_mode, model_router, tool_catalogue
    from agent_friday.routing.model_router import ModelRouter
    ag = loop_setup
    ticket = Ticket()
    ctx = {"_laya_pilot": ticket}
    monkeypatch.setattr(demo_mode, "is_demo", lambda: False)
    monkeypatch.setattr(ag, "_load_settings", lambda: {"model_routing": {"mode": "smart"}})
    monkeypatch.setattr(ModelRouter, "route", lambda *a, **k: {"provider": "local", "model": "fixture-local"})
    monkeypatch.setattr(model_router, "_health_order", lambda attempts, *a: attempts)
    monkeypatch.setattr(tool_catalogue, "enabled", lambda: True)
    monkeypatch.setattr(ag, "get_anthropic_client", lambda: object())
    seen = []
    def local(messages, **kwargs):
        seen.append(kwargs["session_ctx"]["_laya_pilot"])
        ag._pilot_model_round(kwargs["session_ctx"], "local")
        raise RuntimeError("fixture unavailable")
    def cloud(messages, **kwargs):
        seen.append(kwargs["session_ctx"]["_laya_pilot"])
        ag._pilot_model_round(kwargs["session_ctx"], "cloud")
        return "Done.", []
    monkeypatch.setattr(ag, "_call_ollama", local)
    monkeypatch.setattr(ag, "_call_claude_agent", cloud)
    text, _ = ag._generate_agent_untraced([{"role": "user", "content": "Explain this"}], session_ctx=ctx)
    assert text == "Done."
    assert seen == [ticket, ticket]
    assert ticket.counts["model_rounds"] == 2
    assert ticket.execution == {"local", "cloud"}
    assert ticket.finished == []


def test_measurement_failure_cannot_fail_model_or_tool(monkeypatch, loop_setup):
    class BrokenTicket:
        def __getattr__(self, name):
            raise RuntimeError("fixture telemetry failure")
    ag = loop_setup
    ctx = {"_laya_pilot": BrokenTicket()}
    text, _ = ag._oai_agentic_loop([{"role": "user", "content": "Explain this"}], None,
                                   lambda *a: _reply(), provider="local", model="fixture-local", session_ctx=ctx)
    assert text == "Done."
    assert ag._execute_tool("fixture_read", {}, session_ctx=ctx, handler=lambda args: "fixture result") == "fixture result"


def test_empty_response_failure_is_excluded_from_success_timings(loop_setup):
    ag = loop_setup
    ticket = Ticket()
    ctx = {"_laya_pilot": ticket}
    response = _reply()
    response["choices"][0]["message"]["content"] = ""
    ag._oai_agentic_loop([{"role": "user", "content": "Explain this"}], None,
                         lambda *a: response, provider="local", model="fixture-local", session_ctx=ctx)
    assert ticket.counts["model_rounds"] == 2
    assert ctx["_laya_pilot_outcome"] == "error"


def test_confirmation_only_turn_finishes_once_without_model_rounds(client, monkeypatch, pilot):
    from flask import jsonify
    ticket, starts = pilot
    monkeypatch.setattr(chat_mod, "_confirmed_action_response", lambda *a: jsonify({"response": "Fixture approved"}))
    response = client.post("/api/chat", json={"message": "Confirm the fixture action"})
    assert response.status_code == 200
    assert len(starts) == 1
    assert len(ticket.finished) == 1
    assert ticket.counts["model_rounds"] == 0


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/send"])
def test_final_integrity_rejection_is_not_a_success(client, monkeypatch, pilot, endpoint):
    ticket, _ = pilot
    monkeypatch.setattr(chat_mod, "validate_toolcall_integrity", lambda reply, trace, *a, **k: (
        "The attempted answer could not be verified.", trace,
        {"blocked": True, "retries": 1, "final_leaks": [], "final_claims": ["fixture claim"]}))
    response = client.post(endpoint, json={"message": "Explain this fixture"})
    assert response.status_code == 200
    assert len(ticket.finished) == 1
    assert ticket.finished[0]["error"] is True


def test_preloaded_email_schema_saves_one_scripted_discovery_round(monkeypatch, loop_setup):
    """A deterministic replay proves the mechanism, not real-model speed."""
    from agent_friday.services import tool_catalogue
    from agent_friday.routing.model_router import anthropic_to_openai_tools
    ag = loop_setup
    handled = []
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "search_email", lambda args: handled.append(args) or "Synthetic email result")
    baseline = tool_catalogue.opening_set(ag.CLAUDE_TOOLS)
    baseline_names = {t["name"] for t in baseline}
    measured = {}
    for arm in ("control", "assisted"):
        ticket = Ticket()
        ticket.arm = arm
        ticket.plan = lambda: "apps"
        opening = tool_catalogue.opening_set(ag.CLAUDE_TOOLS, pilot=ticket)
        assert baseline_names.issubset({t["name"] for t in opening})
        loader = next(t for t in opening if t["name"] == "load_tools")
        assert all(t["name"] in loader["description"] for t in ag.CLAUDE_TOOLS)
        def send(convo, tools):
            names = {t["function"]["name"] for t in tools}
            if any(m.get("role") == "tool" and "Synthetic email result" in m.get("content", "") for m in convo):
                return _reply()
            if "search_email" in names:
                return _reply("search_email", {"query": "synthetic fixture"})
            return _reply("load_tools", {"names": ["search_email"]})
        answer, _ = ag._oai_agentic_loop(
            [{"role": "user", "content": "Find the synthetic email"}],
            anthropic_to_openai_tools(opening), send, provider="local",
            model="fixture-local", max_iters=4, catalogue_all=ag.CLAUDE_TOOLS,
            session_ctx={"_laya_pilot": ticket})
        assert answer == "Done."
        measured[arm] = ticket.counts
    assert measured["control"]["model_rounds"] == 3
    assert measured["control"]["loader_calls"] == 1
    assert measured["assisted"]["model_rounds"] == 2
    assert measured["assisted"]["loader_calls"] == 0
    assert measured["control"]["tool_calls"] == measured["assisted"]["tool_calls"] == 1
    assert len(handled) == 2

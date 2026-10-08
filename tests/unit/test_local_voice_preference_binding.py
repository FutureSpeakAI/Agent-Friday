"""The generic local agent changes only its owning call's delivery state."""
import ast
from contextvars import copy_context
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday.services import agent, crew_runtime, voice_delivery as delivery


def _session(cid="voice-one"):
    session = {"engine": "local", "conversation_id": cid,
               "_crew_host_origin": crew_runtime.CrewHostOrigin(False, 7)}
    delivery.initialize_session_preferences(session, {
        "voice_response_depth": "adaptive", "voice_speaking_pace": "natural"})
    return session


def _context(session, **changes):
    return {"authenticated": True, "is_voice": True, "surface": "voice-local",
            "provider": "local", "conversation_id": session["conversation_id"],
            "_crew_host_origin": session["_crew_host_origin"], **changes}


@pytest.fixture
def dispatch(monkeypatch):
    """Keep the real registry, schema and dispatcher; isolate hook side effects."""
    from agent_friday import core
    from agent_friday.services import model_router
    events = []

    def pre(ctx):
        events.append(("pre", dict(ctx.input), dict(ctx.session_ctx)))
        return agent._hooks.ALLOW

    def post(ctx, result):
        events.append(("post", ctx.tool_name))
        return result

    monkeypatch.setattr(agent._hooks, "run_pre_hooks", pre)
    monkeypatch.setattr(agent._hooks, "run_post_hooks", post)
    monkeypatch.setattr(agent._receipts, "record", lambda *a, **kw: None)
    monkeypatch.setattr(agent._tool_output, "clip_result", lambda _name, result: result)
    monkeypatch.setattr(model_router, "announce_tool", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_workflow_caller_context", lambda: {"nested_execution": False})
    monkeypatch.setattr(core, "_save_settings", lambda _data: pytest.fail("Call state was persisted"))

    def run(context, data=None):
        return agent._execute_tool("voice_preferences", data or {
            "action": "set", "scope": "session", "pace": "measured", "depth": "detailed"},
            session_ctx=context)

    return run, events


def _assert_applied(result, session):
    assert not result.startswith("Tool error"), "Local call preferences did not reach the session"
    assert json.loads(result)["scope"] == "session"
    assert delivery.session_preferences(session) == {
        "voice_response_depth": "detailed", "voice_speaking_pace": "measured"}


@pytest.fixture
def owner_store(monkeypatch):
    from agent_friday.services import conversations
    records = {conversations.MAIN_ID: {"id": conversations.MAIN_ID},
               "voice-one": {"id": "voice-one"}}
    reads = []

    def load(cid):
        reads.append(cid)
        return records.get(cid)

    def no_write(*args, **kwargs):
        pytest.fail("Voice owner admission must not create, resolve or save a conversation")

    monkeypatch.setattr(conversations, "load", load)
    for name in ("resolve", "ensure_main", "create", "save"):
        monkeypatch.setattr(conversations, name, no_write)
    return conversations, records, reads


def _apply_for_owner(voice, requested_id, run):
    session = _session(voice._local_voice_owner(requested_id))
    with delivery.using_local_session(session, is_current=lambda: True):
        _assert_applied(run(_context(session)), session)
    return session


@pytest.mark.parametrize("requested_id", [None, "", "  ", "voice-one"])
def test_existing_owner_reaches_real_governed_session_dispatch(dispatch, owner_store, requested_id):
    from agent_friday.routes import voice
    conversations, records, reads = owner_store
    run, _ = dispatch
    session = _apply_for_owner(voice, requested_id, run)
    expected = "voice-one" if requested_id == "voice-one" else conversations.MAIN_ID
    assert session["conversation_id"] == expected
    assert reads == [expected]
    assert records[expected] == {"id": expected}


def test_unresolved_main_owner_is_a_red_control(dispatch, owner_store, monkeypatch):
    from agent_friday.routes import voice
    run, _ = dispatch
    monkeypatch.setattr(voice, "_local_voice_owner", lambda requested_id: requested_id)
    with pytest.raises(AssertionError, match="Local call preferences did not reach the session"):
        _apply_for_owner(voice, None, run)


@pytest.fixture
def route_admission(owner_store, monkeypatch):
    """Run the actual authenticated route only as far as manifest admission."""
    from agent_friday import core
    from agent_friday.routes import voice
    events = []
    original_owner = voice._local_voice_owner

    class BeforeVoiceEngine(BaseException):
        pass

    def owner(requested_id):
        cid = original_owner(requested_id)
        events.append(("owner", cid))
        return cid

    def manifest():
        events.append(("manifest", None))
        raise BeforeVoiceEngine

    monkeypatch.setattr(voice, "FRIDAY_WS_TOKEN", "")
    monkeypatch.setattr(voice, "_api_token_valid", lambda token: False)
    monkeypatch.setattr(voice, "_ws_auth_ok", lambda token: True)
    monkeypatch.setattr(crew_runtime, "capture_host_origin",
                        lambda: crew_runtime.CrewHostOrigin(False, 7))
    monkeypatch.setattr(voice, "_local_voice_owner", owner)
    monkeypatch.setattr(voice, "_load_settings", lambda: {})
    monkeypatch.setattr(voice._vm, "get_manifest", manifest)
    handler = core.app.view_functions["ws_voice_local"].__wrapped__

    def enter(requested_id=None):
        messages = []
        ws = SimpleNamespace(send=lambda raw: messages.append(json.loads(raw)))
        query = {} if requested_id is None else {"conversation_id": requested_id}
        with core.app.test_request_context("/ws/voice-local", query_string=query):
            try:
                handler(ws)
            except BeforeVoiceEngine:
                pass
        return messages

    return enter, events, voice


@pytest.mark.parametrize("requested_id", [None, "voice-one"])
def test_actual_local_route_establishes_owner_before_engine_admission(route_admission, owner_store,
                                                                    requested_id):
    enter, events, _ = route_admission
    conversations, _, reads = owner_store
    assert enter(requested_id) == []
    expected = requested_id or conversations.MAIN_ID
    assert events == [("owner", expected), ("manifest", None)]
    assert reads == [expected]


@pytest.mark.parametrize("gate", ["token", "session"])
def test_actual_local_route_auth_denial_cannot_read_or_create_owner(route_admission, owner_store,
                                                                 monkeypatch, gate):
    enter, events, voice = route_admission
    _, _, reads = owner_store
    if gate == "token":
        monkeypatch.setattr(voice, "FRIDAY_WS_TOKEN", "synthetic-required")
    else:
        monkeypatch.setattr(voice, "_ws_auth_ok", lambda token: False)
    assert enter() == [{"type": "error", "error": "unauthorized"}]
    assert events == [] and reads == []


@pytest.mark.parametrize("failure", ["missing-main", "missing-explicit", "invalid-explicit",
                                      "mismatched-record", "load-error"])
def test_actual_local_route_unavailable_owner_refuses_before_engines(route_admission, owner_store,
                                                                   monkeypatch, failure):
    enter, events, _ = route_admission
    conversations, records, reads = owner_store
    requested_id = None
    if failure == "missing-main":
        records.pop(conversations.MAIN_ID)
    elif failure == "missing-explicit":
        requested_id = "voice-missing"
    elif failure == "invalid-explicit":
        requested_id = "../outside"
    elif failure == "mismatched-record":
        requested_id = "voice-one"
        records[requested_id] = {"id": "voice-other"}
    else:
        def unavailable(cid):
            raise OSError("Synthetic conversation read failure")
        monkeypatch.setattr(conversations, "load", unavailable)
    messages = enter(requested_id)
    assert len(messages) == 1 and messages[0]["type"] == "error"
    assert messages[0]["error"] == "voice_context_unavailable"
    assert "Open an available chat" in messages[0]["detail"]
    assert events == []
    if requested_id:
        assert reads == [requested_id], "Explicit owners must not fall back to Main"


@pytest.mark.parametrize("scope", [None, "session"])
def test_governed_generic_dispatch_updates_the_same_call_for_the_next_turn(dispatch, scope):
    run, events = dispatch
    session = _session()
    context = _context(session)
    args = {"action": "set", "pace": "measured", "depth": "detailed"}
    if scope:
        args["scope"] = scope
    with delivery.using_local_session(session, is_current=lambda: True):
        _assert_applied(run(context, args), session)
    with delivery.using_local_session(session, is_current=lambda: True):
        result = json.loads(run(context, {"action": "inspect", "scope": "session"}))
        assert (result["pace"], result["depth"]) == ("measured", "detailed")
        with delivery.using_preferences(delivery.session_preferences(session)):
            assert delivery.synthesis_plan("The next idea.")["speed"] == .9
            assert delivery.preferences()["depth"] == "detailed"
    assert [event[0] for event in events] == ["pre", "post", "pre", "post"]
    assert events[0][1] == args and events[0][2] == context
    assert "delivery_preferences" not in events[0][2]
    assert agent._CURRENT_TOOL_CONTEXT.get() is None


def test_missing_generic_binding_is_a_red_control(dispatch, monkeypatch):
    run, _ = dispatch
    session = _session()
    with delivery.using_local_session(session, is_current=lambda: True):
        monkeypatch.setattr(delivery, "local_session_for_context", lambda _context: None)
        with pytest.raises(AssertionError, match="Local call preferences did not reach the session"):
            _assert_applied(run(_context(session)), session)
    assert "delivery_preferences" not in session


@pytest.mark.parametrize("changes", [
    {"surface": "chat"}, {"is_voice": False}, {"authenticated": False},
    {"conversation_id": "voice-other"}, {"_crew_host_origin": None},
    {"is_background_task": True}, {"task_id": "task-one"},
    {"nested_execution": True}, {"scheduled": True}, {"schedule_id": "schedule-one"},
    {"agent_id": "crew-one"}, {"agent_profile_id": "profile-one"},
    {"crew_agent_id": "crew-one"}, {"crew_revision": 1},
    {"crew_binding": {"agent_id": "crew-one"}},
    {"grant_scope": {"tools": ["voice_preferences"]}}, {"origin": "phone"},
])
def test_other_callers_cannot_use_an_inherited_voice_binding(dispatch, changes):
    run, _ = dispatch
    session = _session()
    with delivery.using_local_session(session, is_current=lambda: True):
        result = run(_context(session, **changes))
    assert "active voice call" in result
    assert "delivery_preferences" not in session


@pytest.mark.parametrize("cid", [None, ""])
def test_a_call_without_a_conversation_cannot_bind_preferences(dispatch, cid):
    run, _ = dispatch
    session = _session(cid)
    with delivery.using_local_session(session, is_current=lambda: True):
        assert "active voice call" in run(_context(session))
    assert "delivery_preferences" not in session


def test_separate_calls_with_equal_origin_values_do_not_share_preferences(dispatch):
    run, _ = dispatch
    first, second = _session(), _session()
    assert first["_crew_host_origin"] == second["_crew_host_origin"]
    with delivery.using_local_session(first, is_current=lambda: True):
        assert "active voice call" in run(_context(second))
        with delivery.using_local_session(second, is_current=lambda: True):
            _assert_applied(run(_context(second)), second)
            assert "active voice call" in run(_context(first))
        _assert_applied(run(_context(first)), first)
    assert first["delivery_preferences"] is not second["delivery_preferences"]


def test_closed_cancelled_retargeted_and_copied_turns_cannot_reuse_a_binding(dispatch):
    run, _ = dispatch
    session = _session()
    context = _context(session)
    current = [True]
    with delivery.using_local_session(session, is_current=lambda: current[0]):
        copied = copy_context()
        current[0] = False
        assert "active voice call" in run(context)
        current[0] = True
        session["conversation_id"] = "voice-other"
        assert "active voice call" in run(context)
        session["conversation_id"] = "voice-one"
    assert copied.run(delivery.local_session_for_context, context) is None
    assert "active voice call" in copied.run(run, context)
    assert "delivery_preferences" not in session


def test_task_registry_and_governance_refusals_remain_effective(dispatch, monkeypatch):
    run, _ = dispatch
    session = _session()
    with delivery.using_local_session(session, is_current=lambda: True):
        monkeypatch.setattr(agent, "_workflow_caller_context", lambda: {"nested_execution": True})
        assert "not delegated work" in run(_context(session))
        monkeypatch.setattr(agent._hooks, "run_pre_hooks", lambda _ctx:
                            SimpleNamespace(action="deny", reason="Synthetic governance refusal"))
        assert run(_context(session)) == "Synthetic governance refusal"
    assert "delivery_preferences" not in session


@pytest.fixture
def relay(monkeypatch):
    from agent_friday.routes import voice
    from agent_friday.services import local_seats, voice_engine
    calls, gates = [], []
    settings = {}
    monkeypatch.setattr(voice_engine, "_load_settings", lambda: dict(settings))
    monkeypatch.setattr(voice_engine, "_brain_serving", lambda: "local:synthetic-seat")
    monkeypatch.setattr(local_seats, "resolve", lambda *a, **kw: "local:synthetic-seat")
    monkeypatch.setattr(voice, "_build_voice_system_prompt", lambda settings=None, **kw:
                        (delivery.instruction(settings), {"volatile": ""}))
    monkeypatch.setattr(voice, "_voice_user_message", lambda text, *a, **kw: text)

    def generate(messages, **kwargs):
        calls.append({**kwargs, "messages": messages, "delivery": delivery.current_overrides()})
        assert delivery.local_session_for_context(kwargs["session_ctx"]) is None
        return "Synthetic deep answer", []

    def gate(text, name):
        gates.append((text, name))
        return "Synthetic gated answer"

    monkeypatch.setattr(agent, "_generate_agent", generate)
    monkeypatch.setattr(voice, "_gate_voice_tool_result", gate)

    def run(engine, question):
        session = _session()
        session["engine"] = engine
        delivery.update_session_preferences(session, "Slow down.")
        result = voice_engine._tool_ask_friday({"question": question}, session)
        return result, calls[-1], session

    return run, settings, gates


@pytest.mark.parametrize("engine", ["local", "gemini"])
def test_deep_relay_uses_question_and_call_preferences_without_changing_authority(relay, engine):
    run, _, gates = relay
    _, simple, _ = run(engine, "What time is it?")
    result, deep, session = run(engine, "Explain the tradeoffs between these approaches.")
    assert deep["max_tokens"] == 1400 > simple["max_tokens"]
    assert "few spoken sentences" not in deep["messages"][0]["content"]
    assert "Explain the tradeoffs" in deep["messages"][0]["content"]
    assert "measured speaking pace" in deep["system"]
    assert deep["delivery"]["voice_speaking_pace"] == "measured"
    assert deep["model"] == "local:synthetic-seat"
    context = deep["session_ctx"]
    assert context["pin_to_seat"] is True and context["provider"] == "local"
    assert context["_crew_host_origin"] is session["_crew_host_origin"]
    assert context["conversation_id"] == session["conversation_id"]
    assert "delivery_preferences" not in context
    assert result == ("Synthetic deep answer" if engine == "local" else "Synthetic gated answer")
    assert gates == ([] if engine == "local" else [("Synthetic deep answer", "ask_friday")] * 2)
    assert delivery.current_overrides() == {}


@pytest.mark.parametrize("engine", ["local", "gemini"])
def test_deep_relay_still_respects_an_explicit_token_limit(relay, engine):
    run, settings, _ = relay
    settings["voice_max_tokens"] = 120
    _, call, _ = run(engine, "Explain the tradeoffs.")
    assert call["max_tokens"] == 120


@pytest.mark.parametrize("engine", ["local", "gemini"])
def test_ignoring_the_relay_question_is_a_red_control(relay, monkeypatch, engine):
    from agent_friday.routes import voice
    original = voice._voice_reply_cap
    monkeypatch.setattr(voice, "_voice_reply_cap", lambda settings, user_text="": original(settings))
    run, _, _ = relay
    _, call, _ = run(engine, "Explain the tradeoffs.")
    with pytest.raises(AssertionError, match="Deep relay lost the question budget"):
        assert call["max_tokens"] == 1400, "Deep relay lost the question budget"


def test_generic_agent_call_is_inside_the_revocable_local_session_binding():
    from agent_friday.routes import voice
    tree = ast.parse(Path(voice.__file__).read_text(encoding="utf-8"))
    route = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                 and node.name == "ws_voice_local")
    assignments = [node for node in ast.walk(route) if isinstance(node, ast.Assign)]
    owner = next(node.value for node in assignments if any(
        isinstance(target, ast.Name) and target.id == "_open_cid" for target in node.targets))
    assert isinstance(owner, ast.List) and len(owner.elts) == 1
    assert isinstance(owner.elts[0], ast.Call) and isinstance(owner.elts[0].func, ast.Name)
    assert owner.elts[0].func.id == "_local_voice_owner"
    tool_session = next(node.value for node in assignments if any(
        isinstance(target, ast.Name) and target.id == "_tool_session" for target in node.targets))
    call_owner = next(value for key, value in zip(tool_session.keys, tool_session.values)
                      if isinstance(key, ast.Constant) and key.value == "conversation_id")
    expected_owner = ast.parse("_open_cid[0]", mode="eval").body
    assert ast.dump(call_owner) == ast.dump(expected_owner)
    session_owner = next(node.value for node in assignments if any(
        isinstance(target, ast.Attribute) and target.attr == "conversation_id"
        and isinstance(target.value, ast.Name) and target.value.id == "sess"
        for target in node.targets))
    assert ast.dump(session_owner) == ast.dump(expected_owner)
    bindings = [(node, item.context_expr) for node in ast.walk(tree) if isinstance(node, ast.With)
                for item in node.items if isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "using_local_session"]
    assert len(bindings) == 1
    block, binding = bindings[0]
    assert isinstance(binding.args[0], ast.Name) and binding.args[0].id == "_tool_session"
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "_generate_agent" for node in ast.walk(block))
    check = next(item.value for item in binding.keywords if item.arg == "is_current")
    assert isinstance(check, ast.Lambda)
    state = {"done": False, "cancel": False}
    scope = {key: SimpleNamespace(is_set=lambda key=key: state[key]) for key in state}
    is_current = eval(compile(ast.Expression(body=check), str(voice.__file__), "eval"), scope)
    assert is_current()
    for key in state:
        state[key] = True
        assert not is_current()
        state[key] = False

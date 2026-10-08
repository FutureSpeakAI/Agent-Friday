"""Local voice rejects stale Crew calls before recording their arguments."""
import ast
import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday.routes import voice
from agent_friday.services import agent, crew_runtime, off_record, voice_engine


@pytest.mark.parametrize("name", ["ask_crew", "ask-crew"])
@pytest.mark.parametrize("origin_kind", ["missing", "private-ended", "public-changed"])
def test_local_crew_refusal_precedes_logging_orbs_and_dispatch(monkeypatch, name, origin_kind):
    state = {"private": origin_kind == "private-ended", "generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    origin = crew_runtime.capture_host_origin()
    session = {} if origin_kind == "missing" else {"_crew_host_origin": origin}
    if origin_kind != "missing":
        state.update(private=False, generation=9)
    assert agent._resolve_tool_name(name)[0] == "ask_crew"

    def forbidden(*a, **kw):
        pytest.fail("Denied Crew arguments reached local voice instrumentation or dispatch")

    monkeypatch.setattr(voice._log, "info", forbidden)
    monkeypatch.setattr(voice, "_voice_orb_start", forbidden)
    monkeypatch.setattr(voice, "_voice_orb_finish", forbidden)
    monkeypatch.setattr(voice, "_run_voice_tool_bounded", forbidden)
    result = voice._local_voice_tool(
        name, {"agent": "Reviewer", "request": "Private marker"}, forbidden, session)
    assert "CREW DENY" in result and "Private marker" not in result


def test_public_local_crew_origin_reaches_the_governed_worker(monkeypatch):
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: False)
    monkeypatch.setattr(off_record, "generation", lambda: 8)
    origin = crew_runtime.capture_host_origin()
    seen, events = [], []

    def execute(name, args, *, handler, session_ctx):
        seen.append((name, session_ctx))
        assert crew_runtime.require_public_host_origin(session_ctx["_crew_host_origin"]) is origin
        return "accepted"

    monkeypatch.setattr(agent, "_execute_tool", execute)
    monkeypatch.setattr(voice, "voice_tool_limit_s", lambda settings=None: 2)
    monkeypatch.setattr(voice, "_voice_orb_start", lambda name: events.append("start") or "fixture")
    monkeypatch.setattr(voice, "_voice_orb_finish", lambda *a: events.append("finish"))
    result = voice._local_voice_tool(
        "ask_crew", {"agent": "Reviewer", "request": "Public work"}, lambda data: True,
        {"engine": "local", "conversation_id": "public-chat", "_crew_host_origin": origin})
    assert result == "accepted" and events == ["start", "finish"]
    assert len(seen) == 1 and seen[0][0] == "ask_crew"
    assert seen[0][1]["conversation_id"] == "public-chat"
    assert seen[0][1]["provider"] == "local"


@pytest.mark.parametrize("raises", [False, True])
def test_expired_local_completion_discards_the_orb_without_arguments(monkeypatch, raises):
    state = {"generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: False)
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    origin = crew_runtime.capture_host_origin()
    removed = []

    def run(*a, **kw):
        state["generation"] = 9
        if raises:
            raise RuntimeError("Private result marker")
        return "Private result marker"

    def forbidden(*a, **kw):
        pytest.fail("Expired Crew completion reached argument or exception instrumentation")

    monkeypatch.setattr(voice, "_run_voice_tool_bounded", run)
    monkeypatch.setattr(voice, "_voice_orb_start", lambda name: "fixture-orb")
    monkeypatch.setattr(voice, "_voice_orb_finish", forbidden)
    monkeypatch.setattr(voice._log, "error", forbidden)
    monkeypatch.setattr(voice, "process_remove", removed.append)
    result = voice._local_voice_tool("ask_crew", {"request": "Original marker"}, lambda data: True,
                                     {"_crew_host_origin": origin})
    assert "CREW DENY" in result and "marker" not in result
    assert removed == ["fixture-orb"]


@pytest.mark.parametrize("name", ["ask_crew", "ask-crew"])
def test_front_refuses_expired_calls_before_parsing_or_provider_history(monkeypatch, name):
    from agent_friday.services import model_router, voice_front
    state = {"private": True, "generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    origin = crew_runtime.capture_host_origin()
    bodies = []
    seat = voice_front.FrontSeat()
    monkeypatch.setattr(seat, "_post", lambda body, **kw:
                        bodies.append(body) or SimpleNamespace(raise_for_status=lambda: None))

    def consume(*a, **kw):
        state.update(private=False, generation=9)
        return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "fixture-call",
            "function": {"name": name, "arguments": "Private marker: invalid JSON"}}]}}]}

    def forbidden(*a, **kw):
        pytest.fail("Denied front call reached argument parsing or execution")

    monkeypatch.setattr(model_router, "_consume_sse_completion", consume)
    monkeypatch.setattr(model_router, "turn_cancelled", lambda: False)
    monkeypatch.setattr(voice_front, "validate_tool_call", forbidden)
    kwargs = {"run_tool": forbidden}
    # The unfixed provider must reach the forbidden parser, rather than fail
    # because its older signature does not have the admission callback yet.
    if "admit_tool" in inspect.signature(seat.run_turn).parameters:
        kwargs["admit_tool"] = lambda tool: agent._crew_delegation_denial(
            tool, {"_crew_host_origin": origin})
    result = seat.run_turn("Fixture", [{"role": "user", "content": "Synthetic request"}], {}, **kwargs)
    assert "CREW DENY" in result and len(bodies) == 1
    assert "Private marker" not in json.dumps(bodies)


def _voice_callback(name, **bindings):
    tree = ast.parse(Path(voice.__file__).read_text(encoding="utf-8"))
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    assert len(functions) == 1
    scope = dict(vars(voice), **bindings)
    exec(compile(ast.Module(body=functions, type_ignores=[]), voice.__file__, "exec"), scope)
    return scope[name]


@pytest.mark.parametrize("boundary", ["public", "between-calls", "completion"])
def test_front_admission_keeps_public_work_and_rechecks_later_calls(monkeypatch, boundary):
    from agent_friday.services import model_router, voice_front
    state = {"generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: False)
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    origin = crew_runtime.capture_host_origin()
    names = (["search_web"] if boundary == "between-calls" else []) + ["ask_crew"]
    calls = [{"id": name, "function": {"name": name, "arguments": "{}"}} for name in names]
    responses = [{"choices": [{"message": {"tool_calls": calls}}]},
                 {"choices": [{"message": {"content": "accepted"}}]}]
    posted, executed = [], []
    seat = voice_front.FrontSeat()
    monkeypatch.setattr(seat, "_post", lambda body, **kw:
                        posted.append(body) or SimpleNamespace(raise_for_status=lambda: None))
    monkeypatch.setattr(model_router, "_consume_sse_completion", lambda *a, **kw: responses.pop(0))
    monkeypatch.setattr(model_router, "turn_cancelled", lambda: False)
    validate = voice_front.validate_tool_call

    def validate_current(call, contract):
        if call["function"]["name"] == "ask_crew" and state["generation"] != 8:
            pytest.fail("A later Crew call reached argument parsing after its origin expired")
        return validate(call, contract)

    def run_tool(name, args):
        executed.append(name)
        if boundary in ("between-calls", "completion"):
            state["generation"] = 9
        return "Private result marker" if name == "ask_crew" else "Other result"

    monkeypatch.setattr(voice_front, "validate_tool_call", validate_current)
    contract = {"tools": [{"function": {"name": name, "parameters": {}}} for name in names]}
    kwargs = {"run_tool": run_tool}
    if "admit_tool" in inspect.signature(seat.run_turn).parameters:
        kwargs["admit_tool"] = lambda name: agent._crew_delegation_denial(
            name, {"_crew_host_origin": origin})
    result = seat.run_turn("Fixture", [{"role": "user", "content": "Synthetic"}], contract, **kwargs)
    if boundary == "public":
        assert result == "accepted" and executed == ["ask_crew"] and len(posted) == 2
    else:
        assert "CREW DENY" in result and len(posted) == 1
        assert executed == (["search_web"] if boundary == "between-calls" else ["ask_crew"])
        assert "Private result marker" not in json.dumps(posted)


@pytest.mark.parametrize("name", ["ask_crew", "ask-crew"])
@pytest.mark.parametrize("boundary", ["success", "exception", "after-gather", "public"])
def test_native_crew_completion_rechecks_before_recording_and_sending(monkeypatch, name, boundary):
    state = {"generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: False)
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    origin = crew_runtime.capture_host_origin()
    session = {"_crew_host_origin": origin}
    refusal = _voice_callback("_crew_delegation_refusal", _voice_session=session)
    finished, discarded, gated, errors, sent = [], [], [], [], []

    async def run_with_limit(*args, runner):
        result = runner(*args)
        if boundary in ("success", "exception"):
            state["generation"] = 9
        if boundary == "exception":
            raise RuntimeError("Private result marker")
        return result

    async def concurrent(calls, one):
        responses = await voice._run_calls_concurrently(calls, one)
        if boundary == "after-gather":
            state["generation"] = 9
        return responses

    async def send_tool_response(**kwargs):
        sent.extend(kwargs["function_responses"])

    run = _voice_callback(
        "_run_tool_calls", _crew=[None], _crew_host={}, _voice_session=session,
        _crew_delegation_refusal=refusal,
        types=SimpleNamespace(FunctionResponse=lambda **kw: SimpleNamespace(**kw)),
        _vlog=lambda message: None,
        _log=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None,
                             error=lambda *a, **kw: errors.append(a)),
        _turn_tools=[], _safe_send=lambda frame: None,
        _voice_orb_start=lambda tool: "fixture-orb",
        _discard_voice_orb=lambda orb: discarded.append(orb) if orb else None,
        _voice_orb_finish=lambda *args: finished.append(args),
        _time=SimpleNamespace(time=lambda: 0), _voice_tool_with_limit=run_with_limit,
        _voice_tool_run=lambda *args: "Private result marker",
        _gate_voice_tool_result=lambda result, tool: gated.append(result) or result,
        _tell_hold=lambda: None, _user_words_ts=[0],
        _mark_if_stale=lambda result, *args: result, _run_calls_concurrently=concurrent)
    call = SimpleNamespace(name=name, args={"agent": "Reviewer", "request": "Original marker"},
                           id="fixture-call")
    asyncio.run(run(SimpleNamespace(send_tool_response=send_tool_response),
                    SimpleNamespace(function_calls=[call])))
    assert len(sent) == 1 and sent[0].id == "fixture-call"
    if boundary == "public":
        assert sent[0].response["result"] == "Private result marker"
        assert len(finished) == 1 and not discarded
    else:
        assert "NOT DONE" in sent[0].response["result"]
        assert "marker" not in sent[0].response["result"]
        assert not errors
        if boundary != "after-gather":
            assert not finished and not gated and discarded == ["fixture-orb"]


@pytest.mark.parametrize("engine", ["local", "gemini"])
@pytest.mark.parametrize("origin_kind", ["public", "private-ended", "public-changed"])
def test_deep_voice_handoff_keeps_the_exact_call_origin(monkeypatch, engine, origin_kind):
    from agent_friday.services import local_seats
    state = {"private": origin_kind == "private-ended", "generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    origin = crew_runtime.capture_host_origin()
    if origin_kind != "public":
        state.update(private=False, generation=9)
    monkeypatch.setattr(voice_engine, "_brain_serving", lambda: "fixture-local")
    monkeypatch.setattr(local_seats, "resolve", lambda role: "fixture-local")
    monkeypatch.setattr(voice_engine, "_load_settings", lambda: {})
    monkeypatch.setattr(voice, "_build_voice_system_prompt", lambda *a, **kw: ("Fixture", {}))
    monkeypatch.setattr(voice, "_voice_user_message", lambda text, *a, **kw: text)
    monkeypatch.setattr(voice, "_gate_voice_tool_result", lambda text, name: text)
    seen = []

    def generate(*a, session_ctx, **kw):
        seen.append(session_ctx)
        return agent._crew_delegation_denial("ask_crew", session_ctx) or "accepted", []

    monkeypatch.setattr(agent, "_generate_agent", generate)
    result = voice_engine._tool_ask_friday(
        {"question": "Ask the Reviewer about this work"},
        {"engine": engine, "conversation_id": "fixture-chat", "_crew_host_origin": origin})
    assert len(seen) == 1 and seen[0].get("_crew_host_origin") is origin
    assert seen[0]["provider"] == "local" and seen[0]["pin_to_seat"] is True
    assert seen[0]["conversation_id"] == "fixture-chat"
    assert (result == "accepted") if origin_kind == "public" else "CREW DENY" in result

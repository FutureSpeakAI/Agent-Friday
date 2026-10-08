"""Chat and both voice paths retain the same Sites action authority."""
import ast
import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday.routes import voice
from agent_friday.services import agent, crew_runtime, off_record, voice_front


NAMES = ["site_action", "domain_action", "site-action", "domain-action"]


@pytest.fixture
def session(monkeypatch):
    state = {"private": False, "generation": 8}
    monkeypatch.setattr(off_record, "active", lambda *a, **kw: state["private"])
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    return state, {"engine": "local", "conversation_id": "fixture-sites-chat",
                   "_crew_host_origin": crew_runtime.capture_host_origin()}


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("origin_kind", ["missing", "private-ended", "public-changed", "json-shaped"])
def test_local_refusal_precedes_logs_and_dispatch(session, monkeypatch, name, origin_kind):
    state, context = session
    if origin_kind == "missing":
        context.pop("_crew_host_origin")
    elif origin_kind == "json-shaped":
        context["_crew_host_origin"] = {"private": False, "generation": 8}
    elif origin_kind == "private-ended":
        state["private"] = True
        context["_crew_host_origin"] = crew_runtime.capture_host_origin()
    state.update(private=False, generation=9)

    def forbidden(*a, **kw):
        pytest.fail("Stale Sites voice arguments reached instrumentation")

    monkeypatch.setattr(voice._log, "info", forbidden)
    monkeypatch.setattr(voice, "_voice_orb_start", forbidden)
    monkeypatch.setattr(voice, "_voice_orb_finish", forbidden)
    monkeypatch.setattr(voice, "_run_voice_tool_bounded", forbidden)
    result = voice._local_voice_tool(name, {"action": "inspect", "operation_id": "Synthetic marker"},
                                     forbidden, context)
    assert "SITES DENY" in result and "Synthetic marker" not in result


@pytest.mark.parametrize("name", ["site_action", "domain_action"])
def test_public_local_worker_keeps_exact_origin_and_conversation(session, monkeypatch, name):
    _state, context = session
    seen = []

    def execute(tool, args, *, handler=None, session_ctx):
        seen.append((tool, session_ctx))
        assert session_ctx["_crew_host_origin"] is context["_crew_host_origin"]
        return "accepted"

    monkeypatch.setattr(agent, "_execute_tool", execute)
    monkeypatch.setattr(voice, "voice_tool_limit_s", lambda settings=None: 2)
    monkeypatch.setattr(voice, "_voice_orb_start", lambda name: "fixture-orb")
    monkeypatch.setattr(voice, "_voice_orb_finish", lambda *a: None)
    result = voice._local_voice_tool(name, {"action": "list" if name == "site_action" else "accounts"}, lambda frame: True, context)
    assert result == "accepted" and len(seen) == 1
    assert seen[0][0] == name
    assert seen[0][1]["conversation_id"] == context["conversation_id"]


@pytest.mark.parametrize("name", NAMES)
def test_local_front_refuses_before_parsing_or_history(session, monkeypatch, name):
    from agent_friday.services import model_router
    state, context = session
    seat, posted = voice_front.FrontSeat(), []
    monkeypatch.setattr(seat, "_post", lambda body, **kw:
                        posted.append(body) or SimpleNamespace(raise_for_status=lambda: None))

    def consume(*args, **kwargs):
        state["generation"] = 9
        return {"choices": [{"message": {"content": "", "tool_calls": [{"id": "fixture-call",
            "function": {"name": name, "arguments": "Synthetic marker: invalid JSON"}}]}}]}

    def forbidden(*a, **kw):
        pytest.fail("Stale local front call reached parsing or execution")

    monkeypatch.setattr(model_router, "_consume_sse_completion", consume)
    monkeypatch.setattr(model_router, "turn_cancelled", lambda: False)
    monkeypatch.setattr(voice_front, "validate_tool_call", forbidden)
    kwargs = {"run_tool": forbidden}
    if "admit_tool" in inspect.signature(seat.run_turn).parameters:
        # The fallback exercises the actual old boundary in the red control.
        admission = getattr(agent, "_host_action_denial", agent._crew_delegation_denial)
        kwargs["admit_tool"] = lambda tool: admission(tool, context)
    result = seat.run_turn("Fixture", [{"role": "user", "content": "Synthetic request"}], {}, **kwargs)
    assert "SITES DENY" in result and len(posted) == 1
    assert "Synthetic marker" not in json.dumps(posted)


def _callback(name, **bindings):
    tree = ast.parse(Path(voice.__file__).read_text(encoding="utf-8"))
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    assert len(functions) == 1
    scope = dict(vars(voice), **bindings)
    exec(compile(ast.Module(body=functions, type_ignores=[]), voice.__file__, "exec"), scope)
    return scope[name]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("boundary", ["admission", "admission-unread-args", "worker", "completion", "after-gather", "public"])
def test_native_calls_recheck_before_instrumentation_and_send(session, monkeypatch, name, boundary):
    state, context = session
    refusal = _callback("_crew_delegation_refusal", _voice_session=context)
    finished, discarded, gated, errors, sent, late_logs = [], [], [], [], [], []
    if boundary.startswith("admission"):
        state["generation"] = 9

    async def run_with_limit(*args, runner):
        if boundary == "worker":
            state["generation"] = 9
        result = runner(*args)
        if boundary == "completion":
            state["generation"] = 9
        return result

    async def concurrent(calls, one):
        responses = await voice._run_calls_concurrently(calls, one)
        if boundary == "after-gather":
            state["generation"] = 9
        return responses

    async def send_tool_response(**kwargs):
        sent.extend(kwargs["function_responses"])

    def log(*args, **kwargs):
        if state["generation"] != 8 and "Synthetic marker" in repr(args):
            late_logs.append(args)

    def execute(*args):
        if state["generation"] != 8:
            pytest.fail("Stale native Sites call reached worker")
        return "Synthetic result marker"

    run = _callback(
        "_run_tool_calls", _crew=[None], _crew_host={}, _voice_session=context,
        _crew_delegation_refusal=refusal,
        types=SimpleNamespace(FunctionResponse=lambda **kw: SimpleNamespace(**kw)),
        _vlog=log, _log=SimpleNamespace(info=log, warning=log, error=lambda *a, **kw: errors.append(a)),
        _turn_tools=[], _safe_send=lambda frame: None,
        _voice_orb_start=lambda tool: "fixture-orb",
        _discard_voice_orb=lambda orb: discarded.append(orb) if orb else None,
        _voice_orb_finish=lambda *args: finished.append(args),
        _time=SimpleNamespace(time=lambda: 0), _voice_tool_with_limit=run_with_limit,
        _voice_tool_run=execute, _gate_voice_tool_result=lambda result, tool: gated.append(result) or result,
        _tell_hold=lambda: None, _user_words_ts=[0],
        _mark_if_stale=lambda result, *args: result, _run_calls_concurrently=concurrent)
    call = SimpleNamespace(name=name, args={"action": "inspect", "operation_id": "Synthetic marker"},
                           id="fixture-call")
    if boundary == "admission-unread-args":
        class UnreadArguments:
            id = "fixture-call"

            @property
            def args(self):
                pytest.fail("Refused native Sites arguments were read before admission")

        call = UnreadArguments()
        call.name = name
    asyncio.run(run(SimpleNamespace(send_tool_response=send_tool_response),
                    SimpleNamespace(function_calls=[call])))
    assert len(sent) == 1 and sent[0].id == "fixture-call"
    if boundary == "public":
        assert sent[0].response["result"] == "Synthetic result marker"
        assert len(finished) == 1 and not discarded
    else:
        assert "NOT DONE" in sent[0].response["result"]
        assert "Crew delegation" not in sent[0].response["result"]
        assert "marker" not in sent[0].response["result"]
        assert not errors and not late_logs
        if boundary != "after-gather":
            assert not finished and not gated

@pytest.mark.parametrize("name", ["site_action", "domain_action"])
@pytest.mark.parametrize("boundary", ["worker-completion", "worker-exception", "stale-label"])
def test_local_completion_refuses_before_result_logs_or_orb_receipts(session, monkeypatch, name, boundary):
    state, context = session
    finished, discarded, leaked, workers = [], [], [], []
    marker = "Synthetic delayed result marker"

    def log(*args, **kwargs):
        if state["generation"] != 8 and (marker in repr(args) or "Synthetic argument marker" in repr(args)):
            leaked.append(args)

    def execute(tool, args, *, handler=None, session_ctx):
        workers.append(session_ctx)
        assert session_ctx["_crew_host_origin"] is context["_crew_host_origin"]
        if boundary == "worker-completion":
            state["generation"] = 9
        return marker

    def interrupted(*args, **kwargs):
        state["generation"] = 9
        raise RuntimeError(marker)

    def stale_label(result, *args):
        state["generation"] = 9
        return result

    monkeypatch.setattr(agent, "_execute_tool", execute)
    monkeypatch.setattr(voice, "voice_tool_limit_s", lambda settings=None: 2)
    monkeypatch.setattr(voice._log, "info", log)
    monkeypatch.setattr(voice._log, "warning", log)
    monkeypatch.setattr(voice._log, "error", log)
    monkeypatch.setattr(voice, "_voice_orb_start", lambda tool: "fixture-orb")
    monkeypatch.setattr(voice, "_voice_orb_finish", lambda *args: finished.append(args))
    monkeypatch.setattr(voice, "_discard_voice_orb", lambda orb: discarded.append(orb))
    if boundary == "worker-exception":
        monkeypatch.setattr(voice, "_run_voice_tool_bounded", interrupted)
    elif boundary == "stale-label":
        monkeypatch.setattr(voice, "_mark_if_stale", stale_label)
    result = voice._local_voice_tool(name, {"action": "inspect", "operation_id": "Synthetic argument marker"},
                                     lambda frame: True, context)
    assert "SITES DENY" in result and "marker" not in result
    assert discarded == ["fixture-orb"] and not finished and not leaked
    if boundary != "worker-exception":
        assert len(workers) == 1
        assert workers[0]["conversation_id"] == context["conversation_id"]


@pytest.mark.parametrize("name", ["site_action", "domain_action"])
def test_local_front_drops_expired_worker_result_before_next_model_request(session, monkeypatch, name):
    from agent_friday.services import model_router
    state, context = session
    seat, posted = voice_front.FrontSeat(), []
    def post(body, **kwargs):
        posted.append(json.loads(json.dumps(body)))
        return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(seat, "_post", post)
    arguments = {"action": "list" if name == "site_action" else "accounts"}
    monkeypatch.setattr(model_router, "_consume_sse_completion", lambda *a, **kw:
        {"choices": [{"message": {"content": "", "tool_calls": [{"id": "fixture-call",
            "function": {"name": name, "arguments": json.dumps(arguments)}}]}, "finish_reason": "tool_calls"}]})
    monkeypatch.setattr(model_router, "turn_cancelled", lambda: False)
    monkeypatch.setattr(voice_front, "validate_tool_call", lambda *a: (name, arguments, None))
    def run_tool(tool, args):
        state["generation"] = 9
        return "Synthetic delayed result marker"
    admission = getattr(agent, "_host_action_denial", agent._crew_delegation_denial)
    result = seat.run_turn("Fixture", [{"role": "user", "content": "Synthetic request"}], {},
                           run_tool=run_tool, admit_tool=lambda tool: admission(tool, context))
    assert "SITES DENY" in result and "marker" not in result
    assert len(posted) == 1 and "Synthetic delayed result marker" not in json.dumps(posted)

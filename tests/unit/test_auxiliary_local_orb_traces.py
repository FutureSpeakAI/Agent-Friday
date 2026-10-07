"""Pinned auxiliary calls record their own work without changing transports."""
from __future__ import annotations

import pytest

import agent_friday.core as core
from agent_friday.routes import work_plan
from agent_friday.services import agent, model_router, reasoning_trace as rt
from agent_friday.services.knowledge_graph import indexer


@pytest.fixture(autouse=True)
def _isolated_traces(tmp_path, monkeypatch):
    from agent_friday.services import file_grants

    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    file_grants._SIGNING_KEY_CACHE.clear()
    rt._reset_for_tests()
    monkeypatch.setattr(rt, "BASE_DIR_OVERRIDE", tmp_path / "traces")
    monkeypatch.setattr(rt, "settings", lambda: {"capture": True, "retention_days": 0})
    yield
    with core.PROCESSES_LOCK:
        for pid in list(core.PROCESSES):
            if pid.startswith("example-auxiliary-trace-"):
                core.PROCESSES.pop(pid)
    rt._reset_for_tests()


@pytest.mark.parametrize("path", ["queue", "indexer"])
@pytest.mark.parametrize("state", ["new", "existing", "capture-off"])
@pytest.mark.parametrize("fails", [False, True], ids=["success", "error"])
def test_auxiliary_orb_trace_context_preserves_pinned_calls(monkeypatch, path, state, fails):
    caller = rt.start("chat", "Existing task", parent_id=None) if state == "existing" else None
    monkeypatch.setattr(rt, "settings", lambda: {"capture": state != "capture-off", "retention_days": 0})
    messages = [{"role": "user", "content": "Synthetic public input"}]
    pid, seen = "example-auxiliary-trace-" + path, {}

    def local_transport(sent, **kwargs):
        seen.update(trace_id=rt.current(), messages=sent, kwargs=kwargs)
        core.process_register(pid, name="Example auxiliary call", model=kwargs["model"])
        rt.note("Synthetic public checkpoint")
        if fails:
            raise RuntimeError("Synthetic transport failure")
        return "Synthetic result", []

    def forbidden_reroute(*args, **kwargs):
        raise AssertionError("a pinned local call must not enter the general router")

    monkeypatch.setattr(model_router, "_call_ollama", local_transport)
    monkeypatch.setattr(model_router, "_generate_text", forbidden_reroute)
    monkeypatch.setattr(indexer, "_resolve_model", lambda sensitivity, mode: ("example-local-model", True))

    def call():
        if path == "queue":
            return work_plan._runner({"title": "Example queued work", "spec": messages[0]["content"],
                                      "seat_hint": "example-local-model", "disposition": "now_local"})
        return indexer._llm(messages, "Example indexing system", 2, "local", orb_label="Example knowledge indexing")

    with rt.activate(caller):
        if fails:
            with pytest.raises(RuntimeError, match="Synthetic transport failure"):
                call()
        else:
            expected = ("Synthetic result", "example-local-model") if path == "queue" else "Synthetic result"
            assert call() == expected
        assert rt.current() == caller, "the callee must restore its caller's trace context"

    assert seen["messages"] == messages
    kwargs = seen["kwargs"]
    assert kwargs["model"] == "example-local-model"
    assert kwargs["max_tokens"] == 4096
    if path == "queue":
        assert set(kwargs) == {"model", "tools", "max_tokens"}
        assert kwargs["tools"] is agent.CLAUDE_TOOLS
    else:
        assert kwargs == {"system": "Example indexing system", "model": "example-local-model",
                          "max_tokens": 4096, "orb_label": "Example knowledge indexing"}

    trace_id = seen["trace_id"]
    with core.PROCESSES_LOCK:
        assert core.PROCESSES[pid]["trace_id"] == trace_id
    if state == "capture-off":
        assert trace_id is None
    else:
        assert trace_id
        trace = rt.live_trace(trace_id)
        assert any(e.get("text") == "Synthetic public checkpoint" for e in trace["events"])
        if state == "existing":
            assert trace_id == caller
            assert trace["status"] == "running", "a nested call must not finish its owner's trace"
        else:
            assert trace["kind"] == "background"
            assert trace["label"] == ("Example queued work" if path == "queue" else "Example knowledge indexing")
            assert trace["status"] == ("failed" if fails else "complete")

"""Every run leaves a trace record, and a run that never reasoned says why.

The owner opened the daily-creation trace and found "no reasoning or tool calls
were recorded": the run failed before any model call (no provider could
generate text), the error was caught and printed, the trace closed "complete"
with no events, and finish() refused to archive an empty trace. Two rules:

1. Every run writes a trace record from start to end; an empty run is archived.
2. A run that ends before reasoning says so in the trace itself:
   "No reasoning happened: <reason>", never a blank thread.
"""
from __future__ import annotations

import pytest

from agent_friday.services import reasoning_trace as rt


@pytest.fixture
def archived(friday_dir, monkeypatch):
    got = []
    monkeypatch.setattr(rt, "archive", lambda record: got.append(record) or {"seq": len(got)})
    return got


def _notes(record):
    return [e.get("text", "") for e in record.get("events", []) if e.get("type") == "note"]


def test_a_run_that_did_nothing_is_archived_and_says_why(archived):
    with rt.scope("scheduled", "Daily creation", nested=True) as tid:
        pass
    rec = [r for r in archived if r["trace_id"] == tid]
    assert rec, "an empty run was not archived"
    assert any(n.startswith("No reasoning happened:") for n in _notes(rec[0])), _notes(rec[0])


def test_a_run_that_raised_records_the_error_as_the_reason(archived):
    with pytest.raises(RuntimeError):
        with rt.scope("scheduled", "Front Page", nested=True) as tid:
            raise RuntimeError("the local seat did not answer")
    rec = [r for r in archived if r["trace_id"] == tid][0]
    assert rec["status"] == "failed"
    assert any("No reasoning happened" in n and "the local seat did not answer" in n
               for n in _notes(rec)), _notes(rec)


def test_a_swallowed_failure_still_names_its_reason(archived):
    with rt.scope("scheduled", "Daily creation", nested=True) as tid:
        rt.set_reason("No model provider could generate text (local-only)")
    rec = [r for r in archived if r["trace_id"] == tid][0]
    assert any("No model provider could generate text" in n for n in _notes(rec)), _notes(rec)


def test_generate_text_names_provider_exhaustion_in_the_trace(archived, monkeypatch):
    from agent_friday.services import model_router as mr

    class _Router:
        def route(self, messages, task_context=None):
            return {"provider": "local", "model": "bonsai2:27b"}
    import agent_friday.routing.model_router as routing
    monkeypatch.setattr(routing, "get_router", lambda cfg: _Router())
    monkeypatch.setattr(mr, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})

    def _fail(*a, **k):
        raise RuntimeError("seat down")
    monkeypatch.setattr(mr, "_call_ollama", _fail)
    with rt.scope("scheduled", "Daily creation", nested=True) as tid:
        try:
            mr._generate_text([{"role": "user", "content": "hi"}])
        except RuntimeError:
            pass                                    # the caller swallows it, as creations.py does
    rec = [r for r in archived if r["trace_id"] == tid][0]
    assert any("No model provider could generate text" in n for n in _notes(rec)), _notes(rec)


def test_a_local_answer_without_thinking_is_a_model_call_not_a_blank(archived, monkeypatch):
    from agent_friday.services import model_router as mr

    class _Router:
        def route(self, messages, task_context=None):
            return {"provider": "local", "model": "bonsai2:27b"}
    import agent_friday.routing.model_router as routing
    monkeypatch.setattr(routing, "get_router", lambda cfg: _Router())
    monkeypatch.setattr(mr, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})
    monkeypatch.setattr(mr, "_call_ollama", lambda *a, **k: ("an answer", []))
    with rt.scope("scheduled", "Weekly digest", nested=True) as tid:
        mr._generate_text([{"role": "user", "content": "hi"}])
    rec = [r for r in archived if r["trace_id"] == tid][0]
    types = [e.get("type") for e in rec["events"]]
    assert "model_call" in types, types
    assert not any(n.startswith("No reasoning happened") for n in _notes(rec))


def test_a_scheduled_run_refused_at_its_gate_leaves_a_trace(archived, monkeypatch):
    from agent_friday.services import scheduler as s
    from agent_friday.services import stand_down as sd
    monkeypatch.setattr(sd, "is_stood_down", lambda: True)
    monkeypatch.setattr(sd, "reason", lambda: "the owner asked for the machine")
    monkeypatch.setitem(s.BUILTIN_TASKS, "t_gate", {"fn": lambda: None, "label": "Gate probe"})
    with pytest.raises(s.StoodDown):
        s._run_task({"id": "sch_t_gate", "name": "Gate probe",
                     "task": {"kind": "builtin", "ref": "t_gate"}})
    assert any(any("the owner asked for the machine" in n for n in _notes(r)) for r in archived), \
        [(_notes(r)) for r in archived]

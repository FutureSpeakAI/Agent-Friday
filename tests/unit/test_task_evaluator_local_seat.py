"""The background task's quality evaluator runs on a local seat, or not at all.

The evaluator grades the output of every background task. A cloud grader would
be one paid call per task that nobody chose, and it would send the task's goal
and output off the machine. So the evaluator resolves the local seat that is
actually serving (the same resolution `local_only` schedules use) and:

  * with a local seat, grades the output there and records the grade as the
    journal's `evaluate` decision;
  * with no local seat, records `evaluate: skipped` with the reason, and makes
    no model call of any kind.

It never reaches a cloud client on either branch.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import local_call
from agent_friday.services import scheduler
from agent_friday.services import task_journal as tj


TID = "evaluator-local-0001"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0,
                                                 "capture_reasoning": False,
                                                 "encrypt_at_rest": False})
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_get_friday_system_prompt", lambda *a, **k: "s")
    monkeypatch.setattr(ag, "_predict_route_provider", lambda **kw: "local")
    monkeypatch.setattr(ag, "_gated_vault_control", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_report_task_completion", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_generate_agent",
                        lambda messages, system=None, **kw: ("the answer", []))
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: 0.0)
    monkeypatch.setattr(cm, "record", lambda *a, **k: 0.0)

    cloud = []

    def _no_cloud(*a, **k):
        cloud.append(1)
        raise AssertionError("the evaluator reached for a cloud client")
    monkeypatch.setattr(ag, "get_anthropic_client", _no_cloud)
    yield cloud


def _run_worker():
    with ag.TASKS_LOCK:
        ag.TASKS[TID] = {"task_id": TID, "name": "t", "prompt": "p",
                         "status": "queued", "created": time.time(),
                         "log": [], "result": ""}
    try:
        ag._task_worker(TID, "t", "summarise the notes")
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop(TID, None)
    return next(e for e in tj.read(TID)
                if e["kind"] == "decision" and e["point"] == "evaluate")


def test_with_a_local_seat_the_output_is_graded_there(monkeypatch, _iso):
    monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: "local-model:7b")
    calls = []

    def fake_call(system, user, model, **kw):
        calls.append({"model": model, "user": user})
        return "GRADE: PASS\nREASON: it answered the goal"
    monkeypatch.setattr(local_call, "call", fake_call)

    ev = _run_worker()

    assert [c["model"] for c in calls] == ["local-model:7b"]
    assert "summarise the notes" in calls[0]["user"]
    assert "the answer" in calls[0]["user"]
    assert ev["chosen"] == "GRADE: PASS", ev
    assert _iso == [], "a cloud client was requested"


def test_with_no_local_seat_it_skips_and_says_why(monkeypatch, _iso):
    monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: None)
    calls = []
    monkeypatch.setattr(local_call, "call",
                        lambda *a, **k: calls.append(1) or "GRADE: PASS")

    ev = _run_worker()

    assert ev["chosen"] == "skipped", ev
    assert "no local seat is serving" in ev["reason"], ev
    assert calls == [], "a model was called with no local seat serving"
    assert _iso == [], "a cloud client was requested"


def test_a_failing_seat_lookup_skips_and_names_the_error(monkeypatch, _iso):
    def boom():
        raise RuntimeError("residency state unreadable")
    monkeypatch.setattr(scheduler, "_resolve_local_seat", boom)
    calls = []
    monkeypatch.setattr(local_call, "call",
                        lambda *a, **k: calls.append(1) or "GRADE: PASS")

    ev = _run_worker()

    assert ev["chosen"] == "skipped", ev
    assert "RuntimeError" in ev["reason"] and "residency state unreadable" in ev["reason"], ev
    assert calls == [] and _iso == []


@pytest.mark.parametrize("raw, grade", [
    ("GRADE: FAIL\nREASON: missed the point", "GRADE: FAIL"),
    ("**GRADE:** partial\n**REASON:** half done", "GRADE: PARTIAL"),
    ("Here is my grade.\nGRADE: PASS\nREASON: fine", "GRADE: PASS"),
])
def test_the_local_reply_is_normalised(monkeypatch, raw, grade):
    monkeypatch.setattr(local_call, "call", lambda *a, **k: raw)
    out = ag._evaluate_output(TID, "goal", "output", model="local-model:7b")
    lines = out.splitlines()
    assert lines[0] == grade
    assert lines[1].startswith("REASON: ")


@pytest.mark.parametrize("raw", ["", "I think it was quite good."])
def test_a_reply_with_no_grade_is_unavailable_not_a_judgement(monkeypatch, raw):
    monkeypatch.setattr(local_call, "call", lambda *a, **k: raw)
    out = ag._evaluate_output(TID, "goal", "output", model="local-model:7b")
    assert out.startswith("GRADE: UNAVAILABLE")
    assert "NOT been assessed" in out

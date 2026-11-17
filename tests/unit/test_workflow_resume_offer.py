"""A scheduled workflow killed mid-run leaves one receipt saying which step it reached, is NOT resumed on
its own, and is offered "resume from step N" on a card (BD-16 C3). Synthetic runs and a recording
stand-in for the task spawner; no model runs."""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent, approvals, workflow_resume as wr
from agent_friday.services import recovery_receipts as rr
from tests import ledger_support

NOW = time.time()
STEPS = [{"name": "Gather", "prompt": "gather the sources"},
         {"name": "Draft", "prompt": "write the brief"},
         {"name": "Send", "prompt": "send the brief"}]
DEFINITION = {"name": "Morning brief", "slug": "morning-brief", "steps": STEPS, "revision": 1}


@pytest.fixture(autouse=True)
def world(friday_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(wr, "_registered", False)
    monkeypatch.setattr(agent, "TASKS", {})
    rr._reset_for_tests()
    wr.register()
    spawned = []
    monkeypatch.setattr(agent, "_spawn_task", lambda *a, **kw: spawned.append(kw) or "task-new")
    yield spawned
    rr._reset_for_tests()


def _task(tid, step, status, **kw):
    rec = {"task_id": tid, "name": STEPS[step]["name"], "chain": "morning-brief", "chain_step": step,
           "run_id": "wfr_1", "status": status, "created": NOW + step, "workflow_definition": DEFINITION,
           "schedule_id": "sch_brief", "result": "the gathered sources" if step == 0 else ""}
    rec.update(kw)
    agent.TASKS[tid] = rec


def _killed_at_step_two():
    _task("t0", 0, "completed")
    _task("t1", 1, "interrupted")


def _boot():
    return wr.offer_after_boot([dict(t) for t in agent.TASKS.values()])


def _cards():
    return [c for c in approvals.list_approvals(kind=wr.KIND)]


def _receipts():
    return [d for d in ledger_support.decisions() if d.get("subject") == "workflow:wfr_1"]


def test_a_run_killed_at_step_two_leaves_one_receipt_naming_the_step_and_nothing_starts(world):
    _killed_at_step_two()
    assert _boot() == ["wfr_1"]
    [r] = _receipts()
    assert r["decision"] == "recovered" and r["step"] == 2 and r["steps_total"] == 3
    assert r["outcome"] == "interrupted" and r["next"] == "asks_the_owner_before_resuming"
    assert r["schedule"] == "sch_brief" and r["what"] == "Morning brief" and r["repeated"] is False
    [card] = _cards()
    assert card["status"] == "pending" and "resume from step 2" in card["title"]
    assert world == [], "an interrupted workflow was started without the owner"


def test_a_second_start_adds_no_second_receipt_or_card():
    _killed_at_step_two()
    _boot()
    rr._reset_for_tests()
    _boot()
    assert len(_receipts()) == 1 and len(_cards()) == 1


def test_yes_starts_that_one_step_again_with_the_earlier_result_as_context(world):
    _killed_at_step_two()
    _boot()
    approvals.decide(_cards()[0]["approval_id"], "approve")
    [call] = world
    assert call["chain"] == "morning-brief" and call["chain_step"] == 1
    assert "write the brief" in call["prompt"] and "the gathered sources" in call["prompt"]
    assert "gather the sources" not in call["prompt"], "step 1 must not run again"
    assert call["task_id"] not in ("t0", "t1")


def test_no_and_expiry_start_nothing(world):
    _killed_at_step_two()
    _boot()
    approvals.decide(_cards()[0]["approval_id"], "deny")
    assert world == []


def test_a_run_that_moved_on_before_the_answer_starts_nothing(world):
    _killed_at_step_two()
    _boot()
    _task("t2", 1, "running", chain_retry=1, created=NOW + 50)
    approvals.decide(_cards()[0]["approval_id"], "approve")
    assert world == []


def test_a_run_not_interrupted_is_not_offered(world):
    _task("t0", 0, "completed")
    _task("t1", 1, "running")
    assert _boot() == [] and _cards() == [] and _receipts() == []


def test_workflow_steps_are_never_in_the_automatic_resume_set():
    _killed_at_step_two()
    assert wr.is_workflow_step(agent.TASKS["t1"]) is True
    assert wr.is_workflow_step({"task_id": "plain", "status": "interrupted"}) is False
    assert wr.is_workflow_step(None) is False


def test_a_restart_offers_the_step_and_does_not_resume_it_even_when_auto_resume_is_on(world, monkeypatch, tmp_path):
    from agent_friday.services import reconcile, task_journal as tj, task_resume
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    monkeypatch.setattr(task_resume, "auto_enabled", lambda: True)
    resumed = []
    monkeypatch.setattr(reconcile, "_resume_in_background", lambda tid: resumed.append(tid))
    state = {"task_id": "wf-step-2", "name": "Draft", "status": "interrupted", "created": NOW,
             "chain": "morning-brief", "chain_step": 1, "run_id": "wfr_1", "workflow_definition": DEFINITION}
    tj.index_put("wf-step-2", "Draft", "interrupted", NOW)
    tj.write_state("wf-step-2", state)
    monkeypatch.setattr(task_resume, "resumable_after_boot", lambda: [
        {"task_id": "wf-step-2", "needs_confirmation": False}])
    monkeypatch.setattr(agent, "TASKS", {})
    agent._restore_tasks_from_journal(announce=True)
    assert resumed == [], "a workflow step was resumed without the owner"
    assert len(_cards()) == 1 and "resume from step 2" in _cards()[0]["title"]


def test_a_card_for_an_earlier_step_starts_nothing_once_the_run_was_stopped_at_a_later_one(world):
    _killed_at_step_two()
    _boot()
    _task("t2", 2, "interrupted", created=NOW + 50)
    approvals.decide(_cards()[0]["approval_id"], "approve")
    assert world == []

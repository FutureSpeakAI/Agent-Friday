"""A task record says where the task came from, what model policy it ran under,
and points at the signed receipt of how it ended (north star §30.9, NS-30.9-1).

Before: the record carried principal, goal ancestry and a verification plan, but
no origin, the model pins were scattered over five fields, and nothing on the
record named a terminal receipt. Now:

* `origin` is derived only from how the task was spawned (a schedule, a workflow
  run, a crew, a parent task, a goal, the local job queue, a conversation), never
  from its name or prompt;
* `model_policy` gathers the model, seat and pins in one place;
* when the task ends, one signed `task_end` entry is written to the action ledger
  (ids, status, origin, times; never its name, prompt or result) and the record
  keeps that entry's seq and hash as `terminal_receipt`.
"""
from __future__ import annotations

import pytest

import agent_friday.services.agent as agent
from tests import ledger_support


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    with agent.TASKS_LOCK:
        saved = dict(agent.TASKS)
    yield
    with agent.TASKS_LOCK:
        agent.TASKS.clear()
        agent.TASKS.update(saved)
    tj.reset_for_tests()


@pytest.mark.parametrize("kwargs, origin", [
    ({"schedule_id": "s1", "conversation_id": "c"}, "schedule"),
    ({"workflow_context": {"run_id": "r1"}}, "workflow"),
    ({"crew_context": {"agent_id": "a"}}, "crew"),
    ({"parent_task_id": "t0", "goal_ancestry": {"goal_id": "g"}}, "subagent"),
    ({"goal_ancestry": {"goal_id": "g"}}, "goal"),
    ({"job": {"kind": "wiki_distill"}}, "local_job"),
    ({"conversation_id": "c1"}, "conversation"),
    ({}, "internal"),
])
def test_the_origin_comes_from_how_the_task_was_spawned(kwargs, origin):
    assert agent._task_origin(**kwargs) == origin
    assert origin in agent.TASK_ORIGINS


def test_a_task_record_carries_its_origin_and_model_policy(isolated):
    def runner(tid):
        return {"status": "complete", "result": "synthetic"}
    tid = agent._spawn_task("a synthetic task", "do the synthetic thing", runner=runner,
                            goal_ancestry={"goal_id": "goal_synthetic"})
    with agent.TASKS_LOCK:
        rec = dict(agent.TASKS[tid])
    assert rec["origin"] == "goal"
    mp = rec["model_policy"]
    assert set(mp) == {"model", "seat", "seat_is_local", "pinned_to_seat", "local_only",
                       "cloud_pinned", "local_only_result"}
    assert mp["pinned_to_seat"] is False and mp["local_only_result"] is False
    from agent_friday.services import task_journal as tj
    created = [e for e in tj.read(tid) if e.get("kind") == "created"]
    assert created and created[0]["origin"] == "goal" and created[0]["model_policy"] == mp


def _running(tid, **extra):
    """A task record as a worker leaves it while it runs (no worker thread needed)."""
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = dict({"task_id": tid, "name": "a synthetic task to end",
                                 "prompt": "the synthetic prompt", "status": "running",
                                 "created": 1.0, "started": 2.0, "origin": "conversation",
                                 "principal": "owner", "terminal_receipt": None}, **extra)


def _rows_for(tid):
    return [r for r in ledger_support.rows("task_end") if r.get("task_id") == tid]


def test_an_ended_task_points_at_its_signed_terminal_receipt(isolated):
    _running("t-end-1")
    agent._task_set("t-end-1", status="complete", ended=3.0, result="the synthetic result text")
    with agent.TASKS_LOCK:
        rec = dict(agent.TASKS["t-end-1"])
    receipt = rec["terminal_receipt"]
    assert isinstance(receipt["seq"], int) and receipt["hash"]
    rows = _rows_for("t-end-1")
    assert len(rows) == 1, "one terminal receipt per task"
    assert rows[0]["status"] == "complete" and rows[0]["origin"] == "conversation"
    assert rows[0]["started"] == 2.0 and rows[0]["ended"] == 3.0
    text = ledger_support.text("task_end")
    for private in ("a synthetic task to end", "the synthetic prompt", "the synthetic result text"):
        assert private not in text, "the receipt carries no name, prompt or result"
    from agent_friday.services import action_ledger
    seqs = [e.get("seq") for e in action_ledger.entries(["task_end"], verified=True)
            if (e.get("rec") or {}).get("task_id") == "t-end-1"]
    assert seqs == [receipt["seq"]], "the record names the verified ledger entry"
    from agent_friday.services import task_journal as tj
    assert (tj.read_state("t-end-1") or {}).get("terminal_receipt") == receipt, "kept on disk too"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_every_way_of_ending_gets_one(isolated, status):
    tid = "t-end-" + status
    _running(tid)
    agent._task_set(tid, status=status, ended=3.0, result="x")
    assert [r["status"] for r in _rows_for(tid)] == [status]


def test_the_receipt_is_written_once_even_if_the_status_is_set_again(isolated):
    _running("t-end-2")
    agent._task_set("t-end-2", status="complete", ended=3.0)
    with agent.TASKS_LOCK:
        first = dict(agent.TASKS["t-end-2"])["terminal_receipt"]
    agent._task_set("t-end-2", status="failed", ended=4.0)
    with agent.TASKS_LOCK:
        again = dict(agent.TASKS["t-end-2"])["terminal_receipt"]
    assert first and again == first
    assert len(_rows_for("t-end-2")) == 1


def test_a_running_task_has_no_receipt_yet(isolated):
    _running("t-end-3")
    agent._task_set("t-end-3", tool_calls=1)
    with agent.TASKS_LOCK:
        assert agent.TASKS["t-end-3"]["terminal_receipt"] is None
    assert _rows_for("t-end-3") == []


def test_an_off_record_task_receipt_says_only_that_it_ended(isolated):
    _running("t-off-1", off_record=True)
    agent._task_set("t-off-1", status="complete", ended=3.0, result="private result")
    assert _rows_for("t-off-1") == [{"task_id": "t-off-1", "status": "complete", "off_record": True}]


def test_the_ledger_page_names_the_entry():
    from agent_friday.routes import ledger
    assert ledger.KIND_LABELS["task_end"] == "A task ended"

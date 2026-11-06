"""Every ending gets its terminal receipt, including endings written in place
(north star §30.9, NS-30.9-1).

The seat supervisor (cancel while queued, a watchdog kill, a worker that never
started), the seat queue, boot reconcile and a resume settle set a task's status
on the record directly, never through _task_set, so its settle never ran. The
supervisor's assess pass (reconcile_orphan_tasks) now settles every task, made
since tasks carry an origin, whose receipt does not name its status, and backs off
after a failed ledger write.
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
    with agent.TASKS_LOCK:
        saved = dict(agent.TASKS)
        agent.TASKS.clear()
    getattr(agent, "_RECEIPT_RETRY_AFTER", {}).clear()
    yield
    th = getattr(agent, "_RECEIPT_SWEEP", {}).get("thread")
    if th is not None:
        th.join(timeout=10)
    with agent._SETTLE_GUARD:
        agent._SETTLE_DIRTY.clear()
        agent._SETTLER["active"] = False
    getattr(agent, "_RECEIPT_RETRY_AFTER", {}).clear()
    with agent.TASKS_LOCK:
        agent.TASKS.clear()
        agent.TASKS.update(saved)
    tj.reset_for_tests()


def _sweep(*a, **k):
    """The sweep (absent before this change: then nothing is settled)."""
    return getattr(agent, "settle_out_of_date_receipts", lambda *a, **k: 0)(*a, **k)


def _put(tid, **rec):
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = dict({"task_id": tid, "name": "synthetic", "status": "running",
                                 "created": 1.0, "started": 2.0, "origin": "conversation",
                                 "principal": "owner", "terminal_receipt": None}, **rec)


def _edit_in_place(tid, **fields):
    """What the seat supervisor does: change the record without _task_set."""
    with agent.TASKS_LOCK:
        agent.TASKS[tid].update(fields)


def _rows_for(tid):
    return [r for r in ledger_support.rows("task_end") if r.get("task_id") == tid]


def _receipt(tid):
    with agent.TASKS_LOCK:
        return dict(agent.TASKS[tid]).get("terminal_receipt")


def test_an_ending_written_in_place_gets_its_receipt_at_the_next_pass(isolated):
    _put("t-in-1")
    _edit_in_place("t-in-1", status="cancelled", ended=3.0)
    assert _rows_for("t-in-1") == []
    assert _sweep() == 1
    assert [r["status"] for r in _rows_for("t-in-1")] == ["cancelled"]
    assert _receipt("t-in-1")["status"] == "cancelled"
    assert _sweep() == 0, "settled once"


def test_a_watchdog_kill_after_a_receipt_supersedes_it(isolated):
    _put("t-in-2")
    agent._task_set("t-in-2", status="complete", ended=3.0)
    first = _receipt("t-in-2")
    _edit_in_place("t-in-2", status="killed")
    _sweep()
    rows = _rows_for("t-in-2")
    assert [r["status"] for r in rows] == ["complete", "killed"]
    assert rows[1]["supersedes"] == first["seq"]
    assert _receipt("t-in-2")["status"] == "killed"


def test_a_resume_written_in_place_moves_the_receipt_to_history(isolated):
    _put("t-in-3")
    agent._task_set("t-in-3", status="failed", ended=3.0)
    first = _receipt("t-in-3")
    _edit_in_place("t-in-3", status="running", ended=None)
    _sweep()
    with agent.TASKS_LOCK:
        rec = dict(agent.TASKS["t-in-3"])
    assert rec["terminal_receipt"] is None and rec.get("previous_terminal_receipts") == [first]
    from agent_friday.services import task_journal as tj
    disk = tj.read_state("t-in-3") or {}
    assert disk.get("status") == "running" and disk.get("terminal_receipt") is None, "the state file agrees"


def test_records_from_before_receipts_are_left_alone(isolated):
    _put("t-old-1")
    with agent.TASKS_LOCK:
        agent.TASKS["t-old-1"].pop("origin")
    _edit_in_place("t-old-1", status="interrupted")
    assert _sweep() == 0
    assert _rows_for("t-old-1") == []


def test_a_failed_write_backs_off(isolated):
    from agent_friday.services import action_ledger
    _put("t-in-4")
    _edit_in_place("t-in-4", status="interrupted")
    real = action_ledger.try_append
    action_ledger.try_append = lambda kind, rec, **kw: None
    try:
        assert _sweep() == 1
        retry_at = getattr(agent, "_RECEIPT_RETRY_AFTER", {}).get("t-in-4", 0.0)
        assert _sweep(now=retry_at - 1) == 0, "not again straight away"
    finally:
        action_ledger.try_append = real
    later = retry_at + 1
    assert _sweep(now=later) == 1
    assert [r["status"] for r in _rows_for("t-in-4")] == ["interrupted"]


def test_the_supervisor_pass_settles_an_ending_written_in_place(isolated):
    """The seat supervisor cancels a queued task in place; its next assess pass
    (reconcile_orphan_tasks) gives the task its receipt, off the supervisor thread."""
    import time
    _put("t-in-5", created=time.time())
    _edit_in_place("t-in-5", status="cancelled", ended=time.time())
    agent.reconcile_orphan_tasks()
    deadline = time.time() + 10
    while time.time() < deadline and not _rows_for("t-in-5"):
        time.sleep(0.05)
    assert [r["status"] for r in _rows_for("t-in-5")] == ["cancelled"]


def test_one_sweep_settles_a_bounded_number(isolated, monkeypatch):
    monkeypatch.setattr(agent, "RECEIPT_SWEEP_MAX", 2, raising=False)
    for i in range(5):
        _put("t-many-%d" % i)
        _edit_in_place("t-many-%d" % i, status="interrupted")
    assert _sweep() == 2
    assert _sweep() == 2
    assert _sweep() == 1

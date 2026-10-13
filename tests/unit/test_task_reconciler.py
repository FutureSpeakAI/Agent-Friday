"""A finished task always leaves "running".

The worker's own exit writes a task's end. When it does not (an exception that
escaped, a worker that returned before its finally, a cancel that changed only
the cache, a restart), the reconciler closes the record through the same
journaled path, and the Activity lane stops listing it.
"""
from __future__ import annotations

import threading
import time

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import activity_lane
from agent_friday.services import task_journal as tj

T0 = 1_800_000_000.0


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(ag, "TASKS", {})
    monkeypatch.setattr(ag, "TASK_THREADS", {})
    monkeypatch.setattr(ag, "_PENDING_TASK_THREADS", {})
    monkeypatch.setattr(ag, "_SEAT_SUPERVISOR", None)
    return tj


def _task(tid, status="running", created=T0, **extra):
    rec = {"task_id": tid, "id": tid, "name": "Research " + tid, "status": status,
           "created": created, "started": created, "ended": None, "log": [], "result": "",
           **extra}
    ag.TASKS[tid] = rec
    tj.write_state(tid, rec)
    tj.index_put(tid, rec["name"], status, created)
    return rec


def _finished_thread():
    th = threading.Thread(target=lambda: None)
    th.start()
    th.join()
    return th


def test_a_task_whose_worker_is_gone_leaves_running(journal):
    _task("t-gone")
    ag.TASK_THREADS["t-gone"] = _finished_thread()
    assert ag.reconcile_orphan_tasks(now=T0 + 600) == ["t-gone"]
    assert ag.TASKS["t-gone"]["status"] == "interrupted"
    assert tj.read_state("t-gone")["status"] == "interrupted"
    assert ag.ORPHAN_REASON in tj.read_state("t-gone")["status_reason"]


def test_a_working_task_is_left_alone(journal):
    stop = threading.Event()
    th = threading.Thread(target=stop.wait, daemon=True)
    th.start()
    try:
        _task("t-live")
        ag.TASK_THREADS["t-live"] = th
        assert ag.reconcile_orphan_tasks(now=T0 + 600) == []
        assert ag.TASKS["t-live"]["status"] == "running"
    finally:
        stop.set()


def test_a_worker_not_started_yet_or_waiting_for_its_seat_is_left_alone(journal):
    _task("t-new")
    ag.TASK_THREADS["t-new"] = threading.Thread(target=lambda: None)
    _task("t-wait", status="queued-for-seat")
    ag._PENDING_TASK_THREADS["t-wait"] = threading.Thread(target=lambda: None)
    assert ag.reconcile_orphan_tasks(now=T0 + 600) == []


def test_a_young_task_gets_its_grace(journal):
    _task("t-young")
    ag.TASK_THREADS["t-young"] = _finished_thread()
    assert ag.reconcile_orphan_tasks(now=T0 + ag.ORPHAN_GRACE_S - 1) == []


def test_a_record_that_ended_on_disk_takes_that_end(journal):
    _task("t-done")
    tj.write_state("t-done", dict(ag.TASKS["t-done"], status="complete", result="done",
                                  ended=T0 + 30))
    ag.TASK_THREADS["t-done"] = _finished_thread()
    ag.reconcile_orphan_tasks(now=T0 + 600)
    assert ag.TASKS["t-done"]["status"] == "complete"


def test_a_task_with_no_thread_of_its_own_closes_only_when_stale(journal):
    _task("t-inline", inline_job=True, last_seen=T0 + 100)
    assert ag.reconcile_orphan_tasks(now=T0 + 600) == []
    assert ag.reconcile_orphan_tasks(now=T0 + 100 + ag.OWNERLESS_STALE_S + 1) == ["t-inline"]


def test_a_restart_closes_every_unfinished_record(journal):
    for tid, status in (("b-run", "running"), ("b-seat", "queued-for-seat"), ("b-paused", "paused")):
        _task(tid, status=status)
    _task("b-ok", status="complete")
    found = tj.reconcile_on_boot(now=T0 + 600)
    assert sorted(t["task_id"] for t in found["interrupted"]) == ["b-paused", "b-run", "b-seat"]
    assert tj.read_state("b-seat")["status"] == "interrupted"
    assert tj.index_read()["b-seat"]["status"] == "interrupted"


@pytest.mark.parametrize("status", ["superseded", "killed", "failed:watchdog-kill",
                                    "interrupted", "complete"])
def test_every_final_spelling_is_terminal(status):
    assert tj.is_terminal(status)


@pytest.mark.parametrize("status", ["running", "queued", "queued-for-seat", "paused"])
def test_work_in_progress_is_not_terminal(status):
    assert not tj.is_terminal(status)


def test_an_interrupted_task_ages_out_of_activity(journal, monkeypatch):
    now = time.time()
    _task("a-old", status="interrupted", created=now - 7200, ended=now - 7000)
    _task("a-new", status="interrupted", created=now - 120, ended=now - 60)
    _task("a-hidden", status="complete", created=now - 120, ended=now - 60, tray_dismissed=True)
    ids = {r["id"] for r in activity_lane._task_rows(now)}
    assert "a-new" in ids and "a-old" not in ids and "a-hidden" not in ids

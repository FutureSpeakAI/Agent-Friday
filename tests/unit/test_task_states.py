"""NS-17.7-1: every task is in one of the North Star's task states, carried on its record and
journaled on change.

Before: a task had a free-form status (queued, running, complete, interrupted, ...) and nothing
said "planning", "paused", "cancel requested", "waiting for your answer" or "recovered after a
crash", although Friday knew most of those facts. Now services/task_states derives the state from
what is recorded; the task record carries it as `state`, every change is journaled as a `state`
event, and the tasks API returns it. Synthetic tasks only; no worker runs.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj

TID = "state-task-0001"


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
        ag.TASKS[TID] = {"task_id": TID, "name": "States", "status": "queued",
                         "created": time.time(), "log": [], "result": ""}
    tj.append(TID, "created", name="States", prompt="p")
    tj.write_state(TID, dict(ag.TASKS[TID]))
    yield TID
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def _states():
    return [e["state"] for e in tj.read(TID) if e["kind"] == "state"]


def test_each_change_of_state_is_journaled_and_kept_on_the_record(task):
    ag._task_set(TID, status="running", started=time.time(), phase="planning")
    ag._task_set(TID, phase="working")
    ag._task_set(TID, user_paused=True)
    ag._task_set(TID, user_paused=False)
    ag._task_set(TID, status="complete", ended=time.time(), result="done", verified=True)
    assert _states() == ["planning", "running", "paused", "running", "completed_verified"]
    assert tj.read_state(TID).get("state") == "completed_verified"
    assert ag.TASKS[TID].get("state") == "completed_verified"


def test_a_stop_request_is_a_state_until_the_task_stops(task):
    ag._task_set(TID, status="running", started=time.time())
    tj.request_stop(TID)
    assert tj.read_state(TID).get("state") == "cancel_requested"
    assert _states()[-1:] == ["cancel_requested"]
    ag._task_set(TID, status="cancelled", ended=time.time())
    assert _states()[-1:] == ["cancelled"]


@pytest.mark.parametrize("rec,stop,want", [
    ({"status": "queued"}, False, "queued"),
    ({"status": "running", "phase": "planning"}, False, "planning"),
    ({"status": "running"}, False, "running"),
    ({"status": "waiting_approval"}, False, "waiting_for_approval"),
    ({"status": "running", "waiting_on": {"kind": "approval"}}, False, "waiting_for_approval"),
    ({"status": "running", "waiting_on": {"kind": "input"}}, False, "waiting_for_input"),
    ({"status": "queued-for-seat"}, False, "waiting_for_external_event"),
    ({"status": "running", "user_paused": True}, False, "paused"),
    ({"status": "running"}, True, "cancel_requested"),
    ({"status": "cancelled"}, False, "cancelled"),
    ({"status": "failed:watchdog-kill"}, False, "failed"),
    ({"status": "timeout"}, False, "failed"),
    ({"status": "completed_unverified"}, False, "completed_unverified"),
    ({"status": "complete", "verified": True}, False, "completed_verified"),
    ({"status": "complete"}, False, "completed_unverified"),
    ({"status": "skipped"}, False, "cancelled"),
    ({"status": "complete", "user_paused": True, "waiting_on": {"kind": "input"},
      "phase": "planning"}, True, "completed_unverified"),
    ({"status": "interrupted"}, False, "recovered_after_crash"),
], ids=["queued", "planning", "running", "waiting-approval", "waiting-approval-card",
        "waiting-input", "waiting-seat", "paused", "cancel-requested", "cancelled",
        "failed", "timeout", "unverified", "verified", "complete-without-evidence", "skipped",
        "finished-beats-stale-flags", "recovered"])
def test_every_north_star_state_is_reachable(rec, stop, want):
    from agent_friday.services import task_states as ts
    assert ts.derive(rec, stop_requested=stop) == want
    assert want in ts.STATES


def test_a_finished_task_is_never_told_to_stop():
    from agent_friday.services import task_states as ts
    assert ts.derive({"status": "complete", "verified": True}, stop_requested=True) == "completed_verified"


def test_a_pause_in_the_seat_queue_is_journaled(task):
    ag._task_set(TID, status="queued-for-seat")
    with ag.TASKS_LOCK:
        ag.TASKS[TID]["user_paused"] = True          # the queue changes the record in place
    ag._task_set(TID, user_paused=True)
    assert _states()[-2:] == ["waiting_for_external_event", "paused"]


def test_a_task_recovered_at_the_next_start_says_so(task):
    ag._task_set(TID, status="running", started=time.time())
    tj.index_put(TID, "States", "running", time.time())
    tj.reconcile_on_boot()
    st = tj.read_state(TID)
    assert st.get("state") == "recovered_after_crash" and st.get("exit") in ("closed", "unexpected")
    assert _states()[-1:] == ["recovered_after_crash"]

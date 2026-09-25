"""Red-first contract for Defect E: dispatch wiring of seat_queue + task_watchdog.

These tests pin the seat_supervisor module — the glue that makes the Defect C/D
modules actually govern dispatch. They are written before the module exists and
must fail at the import gate until it is implemented.

Contract summary:
- wire_spawn() consults SeatQueue before any thread exists; queued tasks get
  NO thread and carry the user notice.
- on_task_end() promotes the next FIFO task via the injected thread factory.
- The supervisor loop enforces watchdog verdicts: kill -> failed:watchdog-kill,
  seat freed, next task promoted, stop_requested honored within one pass.
- Tier-2 seat reclaim fires only when enabled and only for local seats.
- Cloud seats pass through unqueued.
"""

import time

import pytest

from agent_friday.services import seat_queue, seat_supervisor, task_watchdog


def _record(task_id, seat="local/bonsai2:27b", **kw):
    rec = {
        "id": task_id,
        "seat": seat,
        "status": "pending",
        "created_at": time.time(),
        "timeout": 1800,
        "tool_calls": 0,
    }
    rec.update(kw)
    return rec


class FakeThreadFactory:
    """Counts thread starts instead of creating real threads."""

    def __init__(self):
        self.started = []

    def __call__(self, record):
        self.started.append(record["id"])


@pytest.fixture()
def supervisor():
    factory = FakeThreadFactory()
    sup = seat_supervisor.SeatSupervisor(
        queue=seat_queue.SeatQueue(),
        thread_factory=factory,
        settings={"watchdog_kill_reclaims_seat": True},
    )
    return sup, factory


def test_first_local_spawn_runs(supervisor):
    sup, factory = supervisor
    rec = _record("t1")
    verdict = sup.wire_spawn(rec)
    assert verdict == "running"
    assert factory.started == ["t1"]


def test_second_spawn_to_busy_local_seat_creates_no_thread(supervisor):
    sup, factory = supervisor
    sup.wire_spawn(_record("t1"))
    rec2 = _record("t2")
    verdict = sup.wire_spawn(rec2)
    assert verdict == "queued-for-seat"
    assert factory.started == ["t1"]  # no second thread
    assert rec2["status"] == "queued-for-seat"


def test_queued_record_carries_user_notice(supervisor):
    sup, _ = supervisor
    sup.wire_spawn(_record("t1"))
    rec2 = _record("t2")
    sup.wire_spawn(rec2)
    assert rec2.get("user_notice") == seat_queue.USER_NOTICE


def test_completion_promotes_fifo(supervisor):
    sup, factory = supervisor
    sup.wire_spawn(_record("t1"))
    sup.wire_spawn(_record("t2"))
    sup.wire_spawn(_record("t3"))
    sup.on_task_end("t1")
    assert factory.started == ["t1", "t2"]
    sup.on_task_end("t2")
    assert factory.started == ["t1", "t2", "t3"]


def test_cloud_seats_pass_through_unqueued(supervisor):
    sup, factory = supervisor
    a = _record("c1", seat="cloud/sonnet-5")
    b = _record("c2", seat="cloud/sonnet-5")
    assert sup.wire_spawn(a) == "running"
    assert sup.wire_spawn(b) == "running"
    assert factory.started == ["c1", "c2"]


def test_chain_steps_queue_like_ordinary_tasks(supervisor):
    sup, factory = supervisor
    sup.wire_spawn(_record("t1"))
    step = _record("chain-9-step-1", chain="rsi-x")
    verdict = sup.wire_spawn(step)
    assert verdict == "queued-for-seat"
    assert "chain-9-step-1" not in factory.started


def test_supervisor_kill_marks_frees_and_promotes(supervisor):
    sup, factory = supervisor
    wedged = _record("t1")
    sup.wire_spawn(wedged)
    sup.wire_spawn(_record("t2"))
    # Reproduce the wedge signature: massively overdue, zero tool calls.
    wedged["created_at"] = time.time() - 8869
    wedged["status"] = "running"
    sup.assess_pass()
    assert wedged["status"] == "failed:watchdog-kill"
    assert factory.started == ["t1", "t2"]  # t2 promoted


def test_stop_requested_killed_within_one_pass(supervisor):
    sup, factory = supervisor
    rec = _record("t1")
    sup.wire_spawn(rec)
    rec["status"] = "running"
    rec["stop_requested"] = True
    sup.assess_pass()
    assert rec["status"] == "failed:watchdog-kill"


def test_kill_logs_enforcement_tier(supervisor):
    sup, _ = supervisor
    rec = _record("t1")
    sup.wire_spawn(rec)
    rec["created_at"] = time.time() - 8869
    rec["status"] = "running"
    sup.assess_pass()
    assert rec.get("kill_tier") in (1, 2)


def test_tier2_reclaim_gated_by_setting():
    factory = FakeThreadFactory()
    reclaimed = []
    sup = seat_supervisor.SeatSupervisor(
        queue=seat_queue.SeatQueue(),
        thread_factory=factory,
        settings={"watchdog_kill_reclaims_seat": False},
        reclaim_seat=lambda seat: reclaimed.append(seat),
    )
    rec = _record("t1")
    sup.wire_spawn(rec)
    rec["created_at"] = time.time() - 8869
    rec["status"] = "running"
    sup.assess_pass()
    assert rec["status"] == "failed:watchdog-kill"  # tier 1 always
    assert reclaimed == []  # tier 2 never fires when disabled


def test_tier2_never_touches_cloud_seats():
    factory = FakeThreadFactory()
    reclaimed = []
    sup = seat_supervisor.SeatSupervisor(
        queue=seat_queue.SeatQueue(),
        thread_factory=factory,
        settings={"watchdog_kill_reclaims_seat": True},
        reclaim_seat=lambda seat: reclaimed.append(seat),
    )
    rec = _record("c1", seat="cloud/sonnet-5")
    sup.wire_spawn(rec)
    rec["created_at"] = time.time() - 8869
    rec["status"] = "running"
    sup.assess_pass()
    assert rec["status"] == "failed:watchdog-kill"
    assert reclaimed == []


def test_default_assessor_is_task_watchdog():
    assert seat_supervisor.DEFAULT_ASSESS is task_watchdog.assess


def test_supervisor_default_cadence_at_most_30s():
    assert seat_supervisor.DEFAULT_CADENCE_SECONDS <= 30

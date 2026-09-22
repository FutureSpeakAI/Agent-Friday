"""Red-first ground truth for Defect D: per-seat task queue + watchdog wiring.

Contract pinned by these tests:
- agent_friday.services.seat_queue exposes a SeatQueue with submit/complete/assess_all.
- A local seat runs at most ONE task at a time; further submissions are held
  with status 'queued-for-seat' (honest label, never silently stacked).
- FIFO promotion: when the running task completes or is killed, the oldest
  queued task is promoted to 'running'.
- Cloud seats are exempt from serialization.
- Watchdog integration: assess_all() consults task_watchdog.assess(); a 'kill'
  verdict terminates the running task AND frees the seat for the next in line.
- USER_NOTICE constant exists and tells the user that local AI runs one job at
  a time and needs time to run.

Regression case: 2026-09-21 — multiple workflow tasks dispatched onto the
single-slot bonsai2 seat simultaneously; they serialized invisibly at the HTTP
layer with no status, and the user had to cancel them by hand.
"""

import time

import pytest

from agent_friday.services import seat_queue
from agent_friday.services.seat_queue import SeatQueue, USER_NOTICE

LOCAL_SEAT = "local/bonsai2:27b"
CLOUD_SEAT = "cloud/sonnet-5"


def _task(task_id, seat=LOCAL_SEAT, budget=1800):
    return {
        "id": task_id,
        "seat": seat,
        "seat_is_local": seat.startswith("local/"),
        "timeout_seconds": budget,
        "created_at": time.time(),
    }


def test_module_exports_user_notice():
    assert isinstance(USER_NOTICE, str)
    lowered = USER_NOTICE.lower()
    assert "local" in lowered
    assert "one" in lowered  # one job at a time
    assert "time" in lowered  # must be given time to run


def test_first_local_task_runs_immediately():
    q = SeatQueue()
    status = q.submit(_task("t1"))
    assert status == "running"


def test_second_local_task_is_queued_not_running():
    q = SeatQueue()
    q.submit(_task("t1"))
    status = q.submit(_task("t2"))
    assert status == "queued-for-seat"


def test_queued_task_carries_honest_label():
    q = SeatQueue()
    q.submit(_task("t1"))
    q.submit(_task("t2"))
    rec = q.get("t2")
    assert rec["status"] == "queued-for-seat"
    assert rec.get("user_notice") == USER_NOTICE


def test_fifo_promotion_on_completion():
    q = SeatQueue()
    q.submit(_task("t1"))
    q.submit(_task("t2"))
    q.submit(_task("t3"))
    q.complete("t1")
    assert q.get("t2")["status"] == "running"
    assert q.get("t3")["status"] == "queued-for-seat"


def test_cloud_seat_tasks_run_concurrently():
    q = SeatQueue()
    assert q.submit(_task("c1", seat=CLOUD_SEAT)) == "running"
    assert q.submit(_task("c2", seat=CLOUD_SEAT)) == "running"


def test_local_seats_serialize_independently():
    q = SeatQueue()
    other_seat = "local/phi4:14b"
    assert q.submit(_task("a1")) == "running"
    assert q.submit(_task("b1", seat=other_seat)) == "running"
    assert q.submit(_task("a2")) == "queued-for-seat"


def test_kill_verdict_terminates_and_frees_seat():
    q = SeatQueue()
    q.submit(_task("t1"))
    q.submit(_task("t2"))

    def fake_assess(record):
        return "kill" if record["id"] == "t1" else "ok"

    q.assess_all(assess=fake_assess)
    assert q.get("t1")["status"] == "killed"
    assert q.get("t2")["status"] == "running"


def test_ok_verdict_leaves_running_task_alone():
    q = SeatQueue()
    q.submit(_task("t1"))
    q.assess_all(assess=lambda rec: "ok")
    assert q.get("t1")["status"] == "running"


def test_assess_all_uses_task_watchdog_by_default():
    # The default assessor must be task_watchdog.assess — the wedge that ran
    # 8869s against an 1800s budget with zero tool calls must rule 'kill'.
    from agent_friday.services import task_watchdog

    assert seat_queue.DEFAULT_ASSESS is task_watchdog.assess


def test_completed_task_cannot_be_promoted_again():
    q = SeatQueue()
    q.submit(_task("t1"))
    q.complete("t1")
    q.submit(_task("t2"))
    assert q.get("t2")["status"] == "running"
    assert q.get("t1")["status"] == "completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

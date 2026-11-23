"""NOT collected on its own (no test_ prefix): a file that leaves a job in the process-wide seat supervisor, the way
a test that starts a local task and never waits for it does. tests/unit/test_local_queue_does_not_leak_between_files.py
runs it, then tests/api/test_local_queue_starts_clean.py, in one process."""
from __future__ import annotations


def test_leave_a_running_job_in_the_seat_supervisor(client):
    import agent_friday.services.agent as ag
    sup = ag._seat_supervisor()
    with sup._lock:
        sup._records["leak-probe-1"] = {"id": "leak-probe-1", "status": "running", "seat_is_local": True,
                                        "seat": "leak-probe-seat", "label": "Leak probe", "kind": "task"}
        sup._started.add("leak-probe-1")
    assert any(j.get("id") == "leak-probe-1" for j in ag.local_queue_snapshot()["jobs"])

"""A task record or an orb one test leaves running is not running in the next.

Activity (services/activity_lane) counts three process-wide registries: the Local AI queue,
the task records (agent.TASKS) and the process orbs (core.PROCESSES). The queue is emptied
around every test (tests/conftest.py); the other two were not, so a record left "running"
by any earlier test on the worker made queue_status say "One running." in
tests/unit/test_local_queue_single_source.py, a test that started nothing. The two tests
below run in order: the first leaves a running job record and a running orb behind, the
second asserts neither is there.
"""
from __future__ import annotations

import time

from agent_friday import core
from agent_friday.services import activity_lane
from agent_friday.services import agent

TASK = "left-running-job"
ORB = "left-running-orb"


def test_a_test_leaves_a_running_job_and_a_running_orb_behind():
    with agent.TASKS_LOCK:
        agent.TASKS[TASK] = {"task_id": TASK, "id": TASK, "name": "Memory review",
                             "job_label": "Memory review", "status": "running",
                             "created": time.time(), "started": time.time(), "ended": None,
                             "log": [], "steps": []}
    core.process_register(ORB, name="Friday", label="Working", category="monitoring")
    ids = {r["id"] for r in activity_lane.activity()["tasks"] if r["status"] == "running"}
    assert {TASK, ORB} <= ids, "Activity counts both as running"


def test_the_next_test_finds_neither():
    assert TASK not in agent.TASKS
    assert ORB not in core.PROCESSES
    ids = {r["id"] for r in activity_lane.activity()["tasks"]}
    assert not ids & {TASK, ORB}, ids
    assert "Memory review" not in activity_lane.spoken_running(names=True)

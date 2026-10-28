"""One process registry with goal ancestry, principal, cost, receipt link and stop capability
(BD-16; NS-29.2-1: "Every significant process registers id, task/goal ancestry, principal,
label, category, model/provider, status, start, progress, step, cost, stop capability,
result, receipt link").

core.process_register / process_update are the one registry the tray, the orbs and
/api/processes read. Ancestry and principal come from the task record services/agent
._spawn_task keeps (goal_ancestry, principal, schedule_id), so a milestone's child tasks
and a task resumed after a restart keep them. These tests register processes the way
Friday's code does and read them back; nothing runs.
"""
from __future__ import annotations

import inspect

import pytest

from agent_friday import core
from agent_friday.services import agent


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    made = []
    monkeypatch.setattr(agent, "TASKS", dict(agent.TASKS))
    yield made
    with core.PROCESSES_LOCK:
        for pid in made:
            core.PROCESSES.pop(pid, None)


def _reg(clean, pid, **kw):
    clean.append(pid)
    core.process_register(pid, name="Work", **kw)
    with core.PROCESSES_LOCK:
        return dict(core.PROCESSES[pid])


def _task(tid, **rec):
    agent.TASKS[tid] = dict({"task_id": tid, "principal": "owner", "goal_ancestry": None}, **rec)


FIELDS = ("goal_id", "milestone_id", "principal", "schedule_id", "cost_usd", "receipt_id", "stop_capability")


def test_every_process_carries_the_registry_fields(clean):
    row = _reg(clean, "p-plain")
    missing = [f for f in FIELDS if f not in row]
    assert not missing, "the process registry has no %s (NS-29.2-1)" % ", ".join(missing)
    assert row["principal"] == "owner" and row["cost_usd"] == 0.0
    assert row["stop_capability"] == "stop_everything"


def test_a_goal_milestones_task_shows_its_ancestry(clean):
    _task("task-77", goal_ancestry={"goal_id": "g-1", "milestone_id": "m-2"})
    row = _reg(clean, "p-goal", task_id="task-77")
    assert (row.get("goal_id"), row.get("milestone_id")) == ("g-1", "m-2"), "no goal ancestry"
    assert row.get("stop_capability") == "after_this_step"


def test_a_crew_task_and_a_scheduled_task_say_whose_they_are(clean):
    _task("task-crew", principal="crew:researcher")
    _task("task-sched", schedule_id="daily-brief")
    assert _reg(clean, "p-crew", task_id="task-crew").get("principal") == "crew:researcher"
    assert _reg(clean, "p-sched", task_id="task-sched").get("schedule_id") == "daily-brief"


def test_a_task_linked_after_the_process_started_still_reaches_it(clean):
    _task("task-88", goal_ancestry={"goal_id": "g-9", "milestone_id": None})
    _reg(clean, "p-late")
    core.process_update("p-late", task_id="task-88")
    with core.PROCESSES_LOCK:
        assert core.PROCESSES["p-late"].get("goal_id") == "g-9"


def test_cost_adds_up_and_the_receipt_is_linked(clean):
    params = inspect.signature(core.process_update).parameters
    assert "cost_usd" in params and "receipt" in params, "a process cannot record its cost or receipt"
    _reg(clean, "p-cost")
    core.process_update("p-cost", cost_usd=0.012)
    core.process_update("p-cost", cost_usd=0.003, receipt="decision:4521")
    core.process_update("p-cost", cost_usd=-5)               # never lowers the total
    with core.PROCESSES_LOCK:
        p = core.PROCESSES["p-cost"]
        assert p["cost_usd"] == pytest.approx(0.015) and p["receipt_id"] == "decision:4521"


def test_an_image_job_can_be_cancelled_at_once(clean):
    assert _reg(clean, "image-123").get("stop_capability") == "cancel_now"


def test_old_callers_are_unchanged(clean):
    row = _reg(clean, "p-old", label="Local Inference x", category="inference", model="m", task_id="t")
    assert row["label"] == "Local Inference x" and row["status"] == "running" and row["task_id"] == "t"

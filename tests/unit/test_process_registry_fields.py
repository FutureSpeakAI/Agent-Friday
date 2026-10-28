"""One process registry with goal ancestry, principal, cost, receipt link and stop capability
(BD-16; NS-29.2-1: "Every significant process registers id, task/goal ancestry, principal,
label, category, model/provider, status, start, progress, step, cost, stop capability,
result, receipt link").

core.process_register / process_update are the one registry the tray, the orbs and
/api/processes read. These tests register processes the way Friday's code does and read
them back; nothing runs.
"""
from __future__ import annotations

import pytest

from agent_friday import core


@pytest.fixture(autouse=True)
def clean():
    made = []
    yield made
    with core.PROCESSES_LOCK:
        for pid in made:
            core.PROCESSES.pop(pid, None)
    if hasattr(core, "TASK_ANCESTRY"):
        core.TASK_ANCESTRY.clear()


def _reg(clean, pid, **kw):
    clean.append(pid)
    core.process_register(pid, name="Work", **kw)
    with core.PROCESSES_LOCK:
        return dict(core.PROCESSES[pid])


FIELDS = ("goal_id", "principal", "cost_usd", "receipt_id", "stoppable")


def test_every_process_carries_the_registry_fields(clean):
    row = _reg(clean, "p-plain")
    missing = [f for f in FIELDS if f not in row]
    assert not missing, "the process registry has no %s (NS-29.2-1)" % ", ".join(missing)
    assert row["principal"] == "owner" and row["stoppable"] is True and row["cost_usd"] == 0.0


def test_a_goal_milestones_task_shows_its_ancestry(clean):
    note = getattr(core, "note_task_ancestry", None)
    assert note is not None, "nothing records which goal a task works for"
    note("task-77", goal_id="g-1", milestone_id="m-2")
    row = _reg(clean, "p-goal", task_id="task-77")
    assert (row["goal_id"], row["milestone_id"], row["principal"]) == ("g-1", "m-2", "goal")


def test_ancestry_noted_after_the_process_started_still_reaches_it(clean):
    row = _reg(clean, "p-late", task_id="task-88")
    assert row.get("goal_id") is None
    core.note_task_ancestry("task-88", goal_id="g-9", milestone_id=None)
    with core.PROCESSES_LOCK:
        assert core.PROCESSES["p-late"]["goal_id"] == "g-9"


def test_a_scheduled_run_works_for_the_schedule(clean):
    assert _reg(clean, "sched-daily-brief-1")["principal"] == "schedule"


def test_cost_adds_up_and_the_receipt_is_linked(clean):
    _reg(clean, "p-cost")
    core.process_update("p-cost", cost_usd=0.012)
    core.process_update("p-cost", cost_usd=0.003, receipt="decision:4521")
    core.process_update("p-cost", cost_usd=-5)               # never lowers the total
    with core.PROCESSES_LOCK:
        p = core.PROCESSES["p-cost"]
        assert p["cost_usd"] == pytest.approx(0.015) and p["receipt_id"] == "decision:4521"


def test_a_process_that_cannot_be_stopped_says_why(clean):
    row = _reg(clean, "p-commit", stoppable=False, stop_note="Writing the backup; stopping now would leave it half-made.")
    assert row["stoppable"] is False and "half-made" in row["stop_note"]
    core.process_update("p-commit", stoppable=True)
    with core.PROCESSES_LOCK:
        assert core.PROCESSES["p-commit"]["stoppable"] is True
        assert core.PROCESSES["p-commit"]["stop_note"] is None


def test_old_callers_are_unchanged(clean):
    row = _reg(clean, "p-old", label="Local Inference x", category="inference", model="m", task_id="t")
    assert row["label"] == "Local Inference x" and row["status"] == "running" and row["task_id"] == "t"

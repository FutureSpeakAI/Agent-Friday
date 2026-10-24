"""NS-17.7-1: the tasks API returns each task's North Star state beside its status."""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj

TID = "state-task-0002"


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS[TID] = {"task_id": TID, "name": "States", "status": "queued",
                         "created": time.time(), "log": [], "result": ""}
    tj.append(TID, "created", name="States", prompt="p")
    yield TID
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def test_the_tasks_api_says_the_state(task, client):
    ag._task_set(TID, status="running", started=time.time(), phase="planning")
    row = client.get(f"/api/tasks/{TID}").get_json()
    assert row.get("state") == "planning" and row["status"] == "running", "status is kept as it was"
    listed = {t["task_id"]: t for t in client.get("/api/tasks").get_json()["tasks"] if t.get("task_id")}
    assert listed[TID].get("state") == "planning"

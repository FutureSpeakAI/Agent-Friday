"""The owner hub lists bounded Crew task metadata without sharing task content."""
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def hub(monkeypatch):
    from agent_friday.services import agent, crew_runtime
    tasks = {}
    monkeypatch.setattr(agent, "TASKS", tasks)
    monkeypatch.setattr(agent, "TASKS_LOCK", threading.RLock())
    return SimpleNamespace(runtime=crew_runtime, tasks=tasks)


def task(number, status="completed", **extra):
    return {"task_id": f"task-{number}", "name": "Researcher", "status": status,
            "created": number, "ended": number + 1,
            "conversation_id": "project-chat", "prompt": "private request",
            "result": "private result", "log": ["private trace"],
            "crew_context": {"agent_id": "crew-" + "a" * 16, "project_id": "project-a"},
            **extra}


def test_hub_excludes_regular_and_malformed_tasks_and_never_returns_content(hub):
    hub.tasks.update({"crew": task(1), "ordinary": task(2, crew_context=None),
                      "malformed": task(3, crew_context={"agent_id": "not-a-crew-profile"})})
    rows = hub.runtime.hub_tasks()
    assert rows == [{"task_id": "task-1", "agent_id": "crew-" + "a" * 16,
                     "speaker_name": "Researcher", "status": "completed",
                     "conversation_id": "project-chat", "project_id": "project-a",
                     "created_at": 1, "updated_at": 2}]


def test_hub_prioritizes_live_work_and_bounds_recent_history(hub):
    hub.tasks.update({f"done-{n}": task(n) for n in range(1, 125)})
    hub.tasks["old-active"] = task(0, "running", ended=None, started=0)
    rows = hub.runtime.hub_tasks()
    assert len(rows) == 100
    assert rows[0]["task_id"] == "task-0"
    assert rows[1]["task_id"] == "task-124"
    assert rows[-1]["task_id"] == "task-26"


def test_hub_returns_an_empty_list_without_work(hub):
    assert hub.runtime.hub_tasks() == []


def test_hub_normalizes_nonfinite_timestamps(hub):
    hub.tasks["bad-time"] = task(1, created=float("nan"), ended=float("inf"))
    row = hub.runtime.hub_tasks()[0]
    assert row["created_at"] is None and row["updated_at"] is None

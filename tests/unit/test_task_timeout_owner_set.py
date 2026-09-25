"""A background task has no built-in time limit.

The task timeout was a fixed 30 minutes, checked after the task had already
finished, so a long local job that completed at minute 31 was recorded as
"timeout" and its result replaced. Like every other limit, it now exists only
when the owner sets it (`task_timeout_seconds`, or FRIDAY_TASK_TIMEOUT).
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import task_journal as tj


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)


def _run_two_hour_task(monkeypatch, settings):
    base = dict(ag._load_settings() or {})
    base.pop("task_timeout_seconds", None)
    base.update(settings)
    monkeypatch.setattr(ag, "_load_settings", lambda: dict(base))

    def two_hours_of_work(*a, **kw):
        with ag.TASKS_LOCK:
            ag.TASKS["long-1"]["started"] = time.time() - 7200
        return "all 300 batches inspected", []
    monkeypatch.setattr(ag, "_generate_agent", two_hours_of_work)
    with ag.TASKS_LOCK:
        ag.TASKS["long-1"] = {"id": "long-1", "name": "inspection", "status": "queued",
                              "log": [], "created": time.time()}
    ag._task_worker_untraced("long-1", "inspection", "inspect everything")
    return ag.TASKS["long-1"]


def test_there_is_no_built_in_task_timeout(monkeypatch):
    monkeypatch.delenv("FRIDAY_TASK_TIMEOUT", raising=False)
    monkeypatch.setattr(ag, "TASK_TIMEOUT_SECONDS", None)
    task = _run_two_hour_task(monkeypatch, {})
    assert task["status"] != "timeout"
    assert "all 300 batches inspected" in str(task.get("result"))


def test_an_owner_set_task_timeout_is_obeyed(monkeypatch):
    task = _run_two_hour_task(monkeypatch, {"task_timeout_seconds": 1800})
    assert task["status"] == "timeout"


def test_the_module_default_is_unlimited_without_the_env_var(monkeypatch):
    monkeypatch.delenv("FRIDAY_TASK_TIMEOUT", raising=False)
    import importlib
    src = importlib.util.find_spec(ag.__name__).origin
    text = open(src, encoding="utf-8").read()
    assert "'FRIDAY_TASK_TIMEOUT', 1800" not in text
    monkeypatch.setattr(ag, "_load_settings", lambda: {})
    monkeypatch.setattr(ag, "TASK_TIMEOUT_SECONDS", None)
    assert ag._task_timeout_s() is None

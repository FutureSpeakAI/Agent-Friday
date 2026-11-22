"""The owner's routes for a built-in job's edits: history, restore default, revert (Scheduler, item 28b)."""
from __future__ import annotations

import pytest

from agent_friday.services import builtin_overrides as bo
from agent_friday.services import scheduler


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(bo, "_file", lambda: tmp_path / "overrides.json")
    monkeypatch.setattr(scheduler, "BUILTIN_TASKS", {"sample_job": {
        "fn": lambda: None, "label": "Sample job", "default_trigger": "daily", "default_spec": {"hour": 7, "minute": 0},
        "notify": "on_complete", "weekday_only": None, "default_enabled": True}})
    scheduler._seed_and_reconcile()
    return "sch_sample_job"


def test_history_restore_and_revert_through_the_routes(client, seeded):
    sid = seeded
    scheduler.update_schedule(sid, {"spec": {"hour": 5, "minute": 30}}, by="owner")
    history = client.get("/api/schedules/%s/history" % sid).get_json()["history"]
    assert [h["kind"] for h in history] == ["edit"] and history[0]["after"]["spec"]["hour"] == 5
    restored = client.post("/api/schedules/%s/restore-default" % sid).get_json()
    assert restored["status"] == "ok" and restored["schedule"]["spec"]["hour"] == 7
    back = client.post("/api/schedules/%s/revert" % sid).get_json()
    assert back["status"] == "ok" and back["schedule"]["spec"]["hour"] == 5            # the restore is undone
    assert [h["kind"] for h in client.get("/api/schedules/%s/history" % sid).get_json()["history"]][-2:] == ["restore_default", "revert"]


def test_a_job_that_is_not_a_built_in_has_no_shipped_default(client, seeded):
    assert client.post("/api/schedules/sch_not_a_builtin/restore-default").status_code == 404
    assert client.post("/api/schedules/sch_not_a_builtin/revert").status_code == 404

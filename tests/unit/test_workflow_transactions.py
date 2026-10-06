"""Edits remain one versioned definition/timetable transaction."""
import copy

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import scheduler as sch
from agent_friday.services import workflow_operations as ops
from agent_friday.services import workflow_overview as wo


@pytest.fixture
def stores(friday_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(ag, "WORKFLOWS_DIR", tmp_path / "workflows")
    for path in (sch.SCHEDULES_FILE, sch.RUNS_FILE):
        path.unlink(missing_ok=True)
    sch._RUNNING.clear()


def draft(**fields):
    return {"name": "Example routine", "steps": [{"name": "Read", "prompt": "Read the chosen sources."}],
            "when": {"trigger": "daily", "spec": {"hour": 8}}, **fields}


def test_schedule_summary_keeps_same_run_output_and_verification():
    detail = {"at": 10, "run_id": "example-run", "status": "unverified", "steps": [{"task_id": "example-task"}],
              "outputs": [{"artifact_id": "example-document"}], "verification": {"status": "unverified"},
              "delivery": {"state": "delivered"}}
    summary = {"at": 11, "run_id": "example-run", "status": "finished", "summary": "Done"}
    combined = wo._newest(detail, summary)
    assert combined["verification"] == detail["verification"]
    assert combined["outputs"] == detail["outputs"]
    assert combined["status"] == "unverified"


def test_adjacent_different_runs_do_not_borrow_steps():
    result = wo._newest({"at": 10, "run_id": "old", "steps": [{"task_id": "old-task"}]},
                        {"at": 11, "run_id": "new", "status": "running"})
    assert not result.get("steps")


def test_rename_retains_identity_and_revision_history(stores):
    first = wo.save(draft())
    renamed = wo.save(draft(**first, name="Renamed example"))
    assert renamed["slug"] == first["slug"]
    assert renamed["revision"] == first["revision"] + 1
    assert len(ag.list_workflow_chains()) == 1
    assert len(ops.revisions(first["slug"])) == 2
    assert sch.get_schedule(first["schedule_id"])["name"] == "Renamed example"


def test_stale_editor_cannot_replace_a_newer_revision(stores):
    first = wo.save(draft())
    latest = wo.save(draft(**first, description="A newer version"))
    with pytest.raises(ValueError, match="changed|newer|version"):
        wo.save(draft(**first, description="Stale editor"))
    assert ag.load_workflow_chain(first["slug"])["revision"] == latest["revision"]


def test_history_failure_restores_exact_definition_and_schedule(stores, monkeypatch):
    first = wo.save(draft())
    original = copy.deepcopy(ag.load_workflow_chain(first["slug"]))
    original_schedule = copy.deepcopy(sch.get_schedule(first["schedule_id"]))
    remember = ops.remember_definition

    def fail_new(definition, schedule=None):
        if definition.get("revision", 1) > original["revision"]:
            raise OSError("Example disk failure")
        return remember(definition, schedule)

    monkeypatch.setattr(ops, "remember_definition", fail_new)
    with pytest.raises(OSError, match="Example disk failure"):
        wo.save(draft(**first, description="Unsaved edit", notify="never",
                      when={"trigger": "daily", "spec": {"hour": 10}}))
    assert ag.load_workflow_chain(first["slug"]) == original
    assert sch.get_schedule(first["schedule_id"]) == original_schedule


def test_first_save_history_failure_leaves_no_dangling_schedule(stores, monkeypatch):
    monkeypatch.setattr(ops, "remember_definition", lambda *a, **k: (_ for _ in ()).throw(OSError("Example full disk")))
    with pytest.raises(OSError):
        wo.save(draft())
    assert not ag.list_workflow_chains()
    assert not sch.list_schedules()


def test_delete_obeys_recording_policy(stores, monkeypatch):
    first = wo.save(draft())
    monkeypatch.setattr(ops, "require_recording", lambda: (_ for _ in ()).throw(ValueError("Off record")))
    with pytest.raises(ValueError, match="Off record"):
        wo.delete(first["slug"])
    assert ag.load_workflow_chain(first["slug"])
    assert sch.get_schedule(first["schedule_id"])

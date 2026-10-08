"""The shared chat, voice and desktop operations change the same stores."""
import pytest

from agent_friday.services import agent as ag, conversations as cv, projects
from agent_friday.services import scheduler as sch, workflow_operations as ops
from agent_friday import skill_registry


@pytest.fixture
def stores(friday_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(ag, "WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.setattr(skill_registry, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(ag, "chain_run_status", lambda *a, **k: None)
    for path in (sch.SCHEDULES_FILE, sch.RUNS_FILE):
        path.unlink(missing_ok=True)
    sch._RUNNING.clear()


def create(**fields):
    return ops.execute("create", {"name": "Example procedure", "steps": [
        {"name": "Prepare", "prompt": "Prepare an example result and check it."}], **fields})


def test_chat_create_binds_project_and_destination(stores):
    project = projects.create("Example project")
    chat = cv.create("Example chat")
    cv.patch(chat["id"], project=project["id"])
    result = ops.execute("create", {"name": "Example procedure", "steps": [
        {"name": "Read", "prompt": "Read the sources."}]}, {"conversation_id": chat["id"]})
    assert result["workflow"]["conversation_id"] == chat["id"]
    assert result["workflow"]["project_id"] == project["id"]


def test_partial_update_preserves_timing_and_restore_creates_revision(stores):
    first = create(when={"trigger": "daily", "spec": {"hour": 8}}, inputs=["Original notes"])
    changed = ops.execute("update", {"slug": first["slug"], "inputs": ["Revised notes"]})
    assert sch.get_schedule(first["schedule_id"])["spec"]["hour"] == 8
    assert changed["workflow"]["inputs"] == ["Revised notes"]
    restored = ops.execute("restore", {"slug": first["slug"], "revision": 1})
    assert restored["revision"] == 3
    assert ag.load_workflow_chain(first["slug"])["inputs"] == ["Original notes"]
    assert [r["revision"] for r in ops.revisions(first["slug"])] == [3, 2, 1]


def test_pause_changes_future_timing_only(stores):
    saved = create(when={"trigger": "daily", "spec": {"hour": 8}})
    assert "unchanged" in ops.execute("pause", saved)["note"]
    assert sch.get_schedule(saved["schedule_id"])["enabled"] is False
    ops.execute("resume", saved)
    assert sch.get_schedule(saved["schedule_id"])["enabled"] is True


def test_manual_run_does_not_borrow_scheduled_grants(stores, monkeypatch):
    saved = create(when={"trigger": "daily", "spec": {"hour": 8}})
    calls = []
    monkeypatch.setattr(ag, "run_workflow_chain", lambda *a, **k: calls.append((a, k)) or "task-example")
    monkeypatch.setattr(ag, "_task_snapshot", lambda tid: {"run_id": "run-example"})
    started = ops.execute("run", {"slug": saved["slug"], "schedule_id": saved["schedule_id"]})
    assert started["run_id"] == "run-example"
    assert "schedule_id" not in calls[0][1]
    owner = ag.load_workflow_chain(saved["slug"])["conversation_id"]
    assert cv.load(owner)
    assert calls[0][1]["conversation_id"] == owner


def test_second_active_run_is_rejected(stores, monkeypatch):
    saved = create()
    monkeypatch.setattr(ag, "chain_run_status", lambda *a, **k: {"state": "running"})
    with pytest.raises(ValueError, match="already running"):
        ops.execute("repeat", saved)


def test_learning_rejects_unverified_and_stale_runs(stores, monkeypatch):
    saved = create()
    monkeypatch.setattr(ag, "chain_run_status", lambda *a, **k: {"state": "completed_unverified"})
    with pytest.raises(ValueError, match="Verify"):
        ops.execute("learn", saved)
    monkeypatch.setattr(ag, "chain_run_status", lambda *a, **k: {
        "state": "completed", "workflow_revision": 0, "verification": {"status": "verified"}})
    with pytest.raises(ValueError, match="changed"):
        ops.execute("learn", saved)


def test_learning_and_delete_preserve_inspectable_procedure(stores, monkeypatch):
    saved = create(success_criteria="Review the source coverage.")
    monkeypatch.setattr(ag, "chain_run_status", lambda *a, **k: {
        "state": "completed", "workflow_revision": 1, "verification": {"status": "verified"}})
    learned = ops.execute("learn", saved)
    proc = skill_registry.get_skill(learned["skill"])
    assert proc.source == "workflow"
    assert "structurally checked" in proc.body
    assert "Prepare an example result" in proc.body
    assert ops.execute("learn", saved)["note"].startswith("This procedure is already")
    ops.execute("delete", saved)
    assert ag.load_workflow_chain(saved["slug"]) is None
    assert skill_registry.get_skill(learned["skill"])


def test_starter_selection_and_draft_have_no_side_effects(stores):
    assert len(ops.execute("starters")["starters"]) >= 5
    result = ops.execute("draft", {"text": "Every weekday at eight, read example notes"})
    assert result["draft"]["steps"]
    assert not ag.list_workflow_chains()
    assert not sch.list_schedules()


@pytest.mark.parametrize("action", ["create", "update", "run", "repeat", "resume", "restore", "learn",
                                    "delete", "retry_delivery", "pause", "stop", "inspect", "list", "describe"])
def test_nested_task_cannot_launch_or_defer_unscoped_work(stores, action):
    with pytest.raises(ValueError, match="scoped background"):
        ops.execute(action, {"name": "Example"}, {"nested_execution": True})
    assert not ag.list_workflow_chains()

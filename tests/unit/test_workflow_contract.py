"""Workflow definitions preserve ownership, checks and execution choices."""
import pytest

from agent_friday.services import agent as ag
from agent_friday.services import conversations as cv
from agent_friday.services import projects
from agent_friday.services import scheduler as sch
from agent_friday.services import workflow_overview as wo


@pytest.fixture
def stores(friday_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(ag, "WORKFLOWS_DIR", tmp_path / "workflows")
    for path in (sch.SCHEDULES_FILE, sch.RUNS_FILE):
        if path.exists():
            path.unlink()
    sch._RUNNING.clear()
    yield


def draft(name="Research brief", **fields):
    return {"name": name, "steps": [{"name": "Research", "prompt": "Prepare a sourced brief."}], **fields}


def test_roundtrip_preserves_the_project_sources_and_outcome(stores):
    project = projects.create("Sample research")
    chat = cv.create("Research discussion")
    cv.patch(chat["id"], project=project["id"])
    spec = draft(project_id=project["id"], conversation_id=chat["id"],
                 inputs=["Project briefing notes"], success_criteria="Name any unavailable source.",
                 output={"kind": "artifact", "title": "Weekly findings"}, notify="on_change")
    saved = wo.save(spec)
    row = next(w for w in wo.overview()["workflows"] if w["slug"] == saved["slug"])
    for field in ("project_id", "conversation_id", "inputs", "success_criteria", "output", "notify"):
        assert row.get(field) == spec[field], field


def test_edit_preserves_execution_options(stores):
    saved = wo.save(draft(steps=[{"name": "Research", "prompt": "Read sources.",
                                "retries": 2, "with_context": False, "seat": "configured-seat"}]))
    step = ag.load_workflow_chain(saved["slug"])["steps"][0]
    assert step["retries"] == 2
    assert step["with_context"] is False
    assert step["seat"] == "configured-seat"


def test_notification_choice_reaches_existing_scheduler(stores):
    saved = wo.save(draft(notify="on_change", when={"trigger": "daily", "spec": {"hour": 8}}))
    assert sch.get_schedule(saved["schedule_id"])["notify"] == "on_change"


def test_mismatched_schedule_cannot_be_reassigned_by_edit(stores):
    one = wo.save(draft("One", when={"trigger": "daily", "spec": {"hour": 8}}))
    two = wo.save(draft("Two", when={"trigger": "daily", "spec": {"hour": 9}}))
    with pytest.raises(ValueError, match="belong|match"):
        wo.save(draft("One", slug=one["slug"], schedule_id=two["schedule_id"],
                      when={"trigger": "daily", "spec": {"hour": 10}}))
    assert sch.get_schedule(two["schedule_id"])["task"]["ref"] == two["slug"]


def test_unverified_run_is_not_presented_as_finished(stores):
    result = wo._chain_last({"state": "completed_unverified", "run_id": "run-example", "steps": [
        {"name": "Research", "index": 0, "status": "completed_unverified", "started": 1, "ended": 2,
         "result_tail": "A draft without checked output."}]})
    assert result["status"] == "unverified"
    assert result["run_id"] == "run-example"


@pytest.mark.parametrize("fields", [
    {"notify": "silently-send"}, {"output": {"kind": "imaginary"}},
    {"project_id": "missing-project"}, {"inputs": "not a list"},
])
def test_invalid_contract_does_not_write_a_workflow(stores, fields):
    with pytest.raises(ValueError):
        wo.save(draft(**fields))
    assert not ag.list_workflow_chains()


def test_project_and_conversation_membership_must_agree(stores):
    project = projects.create("Example project")
    chat = cv.create("Independent chat")
    with pytest.raises(ValueError, match="project"):
        wo.save(draft(project_id=project["id"], conversation_id=chat["id"]))

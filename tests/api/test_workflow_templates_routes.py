"""The career starter is visible, saved once, and never scheduled by Add."""
import json

import pytest

from agent_friday.routes import workflows
from agent_friday.services import agent, scheduler


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "WORKFLOWS_DIR", tmp_path / "workflows")
    monkeypatch.setattr(scheduler, "list_schedules", lambda: [])

    def unexpected(*args, **kwargs):
        pytest.fail("Adding a starter must not create a schedule or start a task")

    monkeypatch.setattr(scheduler, "register_schedule", unexpected)
    monkeypatch.setattr(agent, "_spawn_task", unexpected)


def test_catalog_add_and_overview_use_the_same_saved_workflow(client, stores):
    catalog = client.get("/api/workflows/templates")
    assert catalog.status_code == 200
    template = next(t for t in catalog.get_json()["templates"] if t["id"] == "career-search")
    assert template["installed"] is False
    before = client.get("/api/workflows/overview").get_json()
    assert before["templates"] == catalog.get_json()["templates"]
    assert before["workflows"] == []

    first = client.post("/api/workflows/templates/career-search/add", json={})
    assert first.status_code == 200
    assert first.get_json() == {"status": "ok", "slug": "career-search",
                                "created": True, "schedule_id": None}
    second = client.post("/api/workflows/templates/career-search/add", json={})
    assert second.status_code == 200
    assert second.get_json()["created"] is False

    after = client.get("/api/workflows/overview").get_json()
    assert after["templates"][0]["installed"] is True
    saved = after["workflows"]
    assert len(saved) == 1
    assert saved[0]["slug"] == "career-search"
    assert saved[0]["when"] is None
    assert saved[0]["schedule_id"] is None
    assert saved[0]["running"] is False
    assert saved[0]["last_run"] is None


def test_unknown_starter_returns_an_error_without_saving(client, stores):
    response = client.post("/api/workflows/templates/nonexistent/add", json={})
    assert response.status_code == 400
    assert "Unknown workflow starter" in response.get_json()["message"]
    assert agent.list_workflow_chains() == []


def test_add_has_its_own_login_gate(app, stores):
    # Bypass global before_request hooks to exercise the route's own decorator.
    with app.test_request_context(
        "/api/workflows/templates/career-search/add", method="POST", json={},
        environ_overrides={"REMOTE_ADDR": "203.0.113.5"},
    ):
        response = workflows.workflows_template_add("career-search")
        status = response[1] if isinstance(response, tuple) else response.status_code
        assert status in (401, 403)
    assert agent.list_workflow_chains() == []


def test_corrupt_saved_starter_is_visible_as_a_repair_problem(client, stores):
    agent.WORKFLOWS_DIR.mkdir()
    saved = agent.WORKFLOWS_DIR / "career-search.json"
    saved.write_text("{unfinished", encoding="utf-8")
    catalog = client.get("/api/workflows/templates").get_json()["templates"][0]
    assert catalog["installed"] is False
    assert "Repair or rename" in catalog["problem"]
    overview = client.get("/api/workflows/overview").get_json()
    assert overview["templates"][0] == catalog
    assert overview["workflows"] == []
    response = client.post("/api/workflows/templates/career-search/add", json={})
    assert response.status_code == 400
    assert response.get_json()["message"] == catalog["problem"]
    assert saved.read_text(encoding="utf-8") == "{unfinished"


@pytest.mark.parametrize("steps", [[{}], ["broken"], [{"prompt": 7}], {"prompt": "Read notes"}])
def test_malformed_starter_does_not_break_overview_or_hide_custom_workflows(client, stores, steps):
    agent.save_workflow_chain({"name": "My notes", "steps": [{"prompt": "Summarize my notes."}]})
    saved = agent.WORKFLOWS_DIR / "career-search.json"
    before = json.dumps({"name": "Career search", "slug": "career-search", "steps": steps})
    saved.write_text(before, encoding="utf-8")
    response = client.get("/api/workflows/overview")
    assert response.status_code == 200
    overview = response.get_json()
    assert [workflow["name"] for workflow in overview["workflows"]] == ["My notes"]
    assert overview["templates"][0]["installed"] is False
    assert "Repair or rename" in overview["templates"][0]["problem"]
    assert saved.read_text(encoding="utf-8") == before

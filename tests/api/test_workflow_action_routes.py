"""Desktop operations share validation with conversational workflow tools."""
import pytest

from agent_friday.routes import workflows as routes
from agent_friday.services import agent, scheduler, workflow_operations


@pytest.fixture
def stores(friday_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "WORKFLOWS_DIR", tmp_path / "workflows")
    scheduler.SCHEDULES_FILE.unlink(missing_ok=True)
    monkeypatch.setattr(agent, "chain_run_status", lambda *a, **k: None)


def test_action_create_inspect_update_pause_and_delete(client, stores):
    made = client.post("/api/workflows/action", json={"action": "create", "name": "Example brief",
        "steps": [{"name": "Read", "prompt": "Read the example notes."}],
        "when": {"trigger": "daily", "spec": {"hour": 8}}, "notify": "on_change"})
    assert made.status_code == 200
    saved = made.get_json()
    slug = saved["slug"]
    inspected = client.post("/api/workflows/action", json={"action": "inspect", "slug": slug}).get_json()
    assert inspected["schedule"]["notify"] == "on_change"
    changed = client.post("/api/workflows/action", json={"action": "update", "slug": slug,
        "revision": saved["revision"], "inputs": ["Example source"]})
    assert changed.status_code == 200
    stale = client.post("/api/workflows/action", json={"action": "update", "slug": slug,
        "revision": saved["revision"], "description": "Stale edit"})
    assert stale.status_code == 400
    assert client.post("/api/workflows/action", json={"action": "pause", "slug": slug}).status_code == 200
    assert scheduler.get_schedule(saved["schedule_id"])["enabled"] is False
    assert client.post("/api/workflows/action", json={"action": "delete", "slug": slug}).status_code == 200
    assert not agent.list_workflow_chains()
    assert not scheduler.list_schedules()


def test_action_rejects_invalid_body_and_unknown_action(client, stores):
    for body in ([], "example", {"action": "invent"}):
        assert client.post("/api/workflows/action", json=body).status_code == 400


def test_starters_read_does_not_save(client, stores):
    response = client.get("/api/workflows/starters")
    assert response.status_code == 200
    assert response.get_json()["starters"]
    assert not agent.list_workflow_chains()


def test_new_action_route_is_authenticated_without_global_hook(app, monkeypatch):
    monkeypatch.setattr(workflow_operations, "execute", lambda *a, **k: pytest.fail("Unauthenticated action executed"))
    with app.test_request_context("/api/workflows/action", method="POST", json={"action": "list"},
                                  environ_overrides={"REMOTE_ADDR": "203.0.113.5"}):
        response = routes.workflow_action()
        status = response[1] if isinstance(response, tuple) else response.status_code
        assert status in (401, 403)


def test_legacy_create_retains_timing_and_delete_removes_it(client, stores):
    saved = workflow_operations.execute("create", {"name": "Example old client", "steps": [
        {"name": "Read", "prompt": "Read the selected example."}],
        "when": {"trigger": "weekly", "spec": {"weekdays": [0], "hour": 8}}})
    response = client.post("/api/workflows/chains", json={"name": "Example old client", "steps": [
        {"name": "Compare", "prompt": "Compare the selected examples."}]})
    assert response.status_code == 200
    assert scheduler.get_schedule(saved["schedule_id"])["trigger"] == "weekly"
    assert client.delete("/api/workflows/chains/" + saved["slug"]).status_code == 200
    assert not scheduler.list_schedules()


def test_legacy_run_rejects_non_object_options_without_starting(client, stores, monkeypatch):
    saved = workflow_operations.execute("create", {"name": "Example run options", "steps": [
        {"name": "Read", "prompt": "Read the example."}]})
    monkeypatch.setattr(agent, "_spawn_task", lambda **kwargs: pytest.fail("Malformed options started work"))
    for body in (["example"], "example", 7, False):
        response = client.post("/api/workflows/chains/" + saved["slug"] + "/run", json=body)
        assert response.status_code == 400

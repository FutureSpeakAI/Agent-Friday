"""Owner profile CRUD preserves revisions and returns actionable conflicts."""
import pytest


@pytest.fixture
def crew_store(monkeypatch, tmp_path):
    from agent_friday.services import crew_profiles as profiles
    monkeypatch.setattr(profiles, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(profiles, "_reasoning_options", lambda: [{"id": "test", "models": [{"id": "model"}]}])
    monkeypatch.setattr(profiles, "_voice_options", lambda: [{"id": "speech", "models": [{"id": "tts"}]}])
    monkeypatch.setattr(profiles, "_skill_options", lambda: [])
    monkeypatch.setattr(profiles, "_project_options", lambda: [])
    return profiles


def _profile():
    return {"name": "Reviewer", "provider": "test", "model": "model",
            "voice": {"provider": "speech", "model": "tts", "voice_id": "stock"}}


def test_profile_endpoints_require_registered_authentication(client, crew_store):
    rules = {r.rule for r in client.application.url_map.iter_rules()}
    assert "/api/crew/agents" in rules
    assert "/api/crew/capabilities" in rules
    from agent_friday.routes import crew
    assert "/api/crew/tasks" in rules
    for name in ("crew_capabilities", "crew_agents", "crew_create", "crew_get", "crew_update", "crew_retire", "crew_tasks"):
        assert hasattr(getattr(crew, name), "__wrapped__")


def test_hub_task_endpoint_returns_only_the_runtime_summary(client, monkeypatch):
    from agent_friday.services import crew_runtime
    rows = [{"task_id": "example-task", "agent_id": "crew-" + "a" * 16,
             "speaker_name": "Reviewer", "status": "running"}]
    monkeypatch.setattr(crew_runtime, "hub_tasks", lambda: rows)
    response = client.get("/api/crew/tasks")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "tasks": rows}


def test_create_edit_conflict_and_retire_round_trip(client, crew_store):
    created = client.post("/api/crew/agents", json=_profile())
    assert created.status_code == 201
    first = created.get_json()["agent"]
    path = "/api/crew/agents/" + first["id"]
    edited = client.patch(path, json={"revision": first["revision"], "name": "Updated"})
    assert edited.status_code == 200
    current = edited.get_json()["agent"]
    stale = client.patch(path, json={"revision": first["revision"], "name": "Lost edit"})
    assert stale.status_code == 409
    assert stale.get_json()["current"] == current
    assert client.get(path).get_json()["agent"]["name"] == "Updated"
    retired = client.post(path + "/retire", json={"revision": current["revision"]})
    assert retired.status_code == 200
    assert client.get("/api/crew/agents").get_json()["agents"] == []
    assert client.get("/api/crew/agents?include_retired=true").get_json()["agents"][0]["status"] == "retired"


def test_unknown_fields_and_missing_revision_are_rejected(client, crew_store):
    assert client.post("/api/crew/agents", json={**_profile(), "admin": True}).status_code == 400
    first = client.post("/api/crew/agents", json=_profile()).get_json()["agent"]
    response = client.patch("/api/crew/agents/" + first["id"], json={"name": "No revision"})
    assert response.status_code == 409

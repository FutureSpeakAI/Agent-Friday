"""GET /api/models — the catalog endpoint that drives the UI model picker."""
from __future__ import annotations


def test_models_route_ok(client):
    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "roles" in data and "models" in data and "providers" in data
    for role in ("orchestrator", "subagent", "creative", "voice"):
        assert role in data["roles"]


def test_models_route_reports_selected(client):
    data = client.get("/api/models").get_json()
    assert "selected" in data
    for key in ("orchestrator_model", "subagent_model", "creative_model", "voice_model"):
        assert key in data["selected"]


def test_models_route_no_recalled_models(client):
    data = client.get("/api/models").get_json()
    blob = " ".join(m["id"] + m["label"] for m in data["models"]).lower()
    assert "fable" not in blob and "mythos" not in blob


def test_orchestrator_includes_openai_and_local(client):
    data = client.get("/api/models").get_json()
    providers = {m["provider"] for m in data["roles"]["orchestrator"]}
    assert "openai" in providers
    assert "ollama-local" in providers
    assert "anthropic" in providers

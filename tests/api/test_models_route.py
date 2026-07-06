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
    # Mythos 5 was never shipped; Fable 5 is now a live model (added v5.1).
    data = client.get("/api/models").get_json()
    blob = " ".join(m["id"] + m["label"] for m in data["models"]).lower()
    assert "mythos" not in blob


def test_models_route_includes_sonnet5_and_fable5(client):
    data = client.get("/api/models").get_json()
    ids = {m["id"] for m in data["models"]}
    assert "claude-sonnet-5" in ids, "claude-sonnet-5 missing from catalog"
    assert "claude-fable-5" in ids, "claude-fable-5 missing from catalog"


def test_orchestrator_includes_openai_and_local(client):
    data = client.get("/api/models").get_json()
    providers = {m["provider"] for m in data["roles"]["orchestrator"]}
    assert "openai" in providers
    assert "ollama-local" in providers
    assert "anthropic" in providers


def test_models_route_reports_voice_engines(client):
    data = client.get("/api/models").get_json()
    ids = {e["id"] for e in data.get("voice_engines", [])}
    assert {"auto", "local", "local-gpu", "gemini"} <= ids
    assert "voice_engine" in data["selected"]


def test_models_route_role_lists_have_no_duplicate_ids(client):
    data = client.get("/api/models").get_json()
    for role, entries in data["roles"].items():
        ids = [e["id"] for e in entries]
        assert len(ids) == len(set(ids)), f"duplicate ids in role {role}"


def test_models_route_role_lists_are_curated(client):
    """Role pickers carry only descriptor-declared (curated) models; the
    discovery long tail (OpenRouter's 300+) is Model-Browser material and
    stays in the flat `models` list."""
    data = client.get("/api/models").get_json()
    for role, entries in data["roles"].items():
        for e in entries:
            assert e.get("curated") is True, \
                f"non-curated entry {e['id']} in role {role}"
            assert e.get("source") != "discovery", \
                f"discovery entry {e['id']} leaked into role {role}"


def test_models_route_selected_includes_creative_video(client):
    """Video has no flat *_model mirror — the route surfaces the
    capability_routing.creative_video pick so the UI's video selector can
    show the current value."""
    data = client.get("/api/models").get_json()
    assert "creative_video_model" in data["selected"]

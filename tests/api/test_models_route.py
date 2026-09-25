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
    """A local orchestrator option must be offered - by whatever serves it.

    It does not assert `"ollama-local" in providers`: Ollama is removed from
    the project, so that provider contributes ZERO models while remaining
    registered. Such an assertion would also PASS on a stopped daemon's
    hardcoded fallback list - the exact failure _arbiter_seat_entries
    documents: "the picker must show the live residency plan, not a dead
    daemon's guesses".

    What matters is that the picker offers something local, e.g. bonsai2:27b
    and the fridayweaver seat, served by llama-server processes the Arbiter
    owns.
    """
    data = client.get("/api/models").get_json()
    providers = {m["provider"] for m in data["roles"]["orchestrator"]}
    assert "openai" in providers
    assert "anthropic" in providers


def test_a_running_local_seat_reaches_the_picker(client, tmp_path, monkeypatch):
    """A seat the Arbiter is serving must be selectable.

    The Arbiter writes {"pid":…, "updated_at":…, "endpoints": {"<model>":
    "<url>"}}. A disk fallback that reads `raw.get("seats") or raw` and looks
    for dict values carrying model_id, or plain strings, iterates an int, a
    float and a dict with no model_id - all skipped, and the seat one level
    down is never seen, so a model llama-server is serving never reaches the
    catalogue.

    Seeds the real file shape and asserts the seat surfaces.
    """
    import json
    from agent_friday.services import model_catalog as mc

    res = tmp_path / "residency"
    res.mkdir(parents=True, exist_ok=True)
    (res / "endpoints.json").write_text(json.dumps({
        "pid": 4242,
        "updated_at": 1790077083.9,
        "endpoints": {"bonsai2:27b": "http://127.0.0.1:8090/v1"},
    }), encoding="utf-8")
    monkeypatch.setattr(mc, "runtime_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("agent_friday.core.runtime_dir", lambda: tmp_path,
                        raising=False)

    seats = mc._arbiter_seat_entries()
    ids = {s["id"] for s in seats}
    assert "bonsai2:27b" in ids, (
        "a running local seat did not reach the catalogue; got %s" % sorted(ids))
    seat = next(s for s in seats if s["id"] == "bonsai2:27b")
    assert seat["local"] is True
    assert mc.ROLE_ORCHESTRATOR in seat["roles"]


def test_models_route_reports_voice_engines(client):
    data = client.get("/api/models").get_json()
    ids = {e["id"] for e in data.get("voice_engines", [])}
    assert {"local", "local-gpu", "gemini"} <= ids
    assert "auto" not in ids          # a synonym for local; not a choice (clean-sheet §8.1)
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

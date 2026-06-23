"""API tests for the creative pipeline, projects, Scene DNA, QA and take routes.

The autouse `_no_real_llm` fixture stubs _generate_text (→ CANNED_TEXT), so
pipeline stages and QA evaluation run without a model. No Gemini key is touched
(image-take routes are tested only on the unavailable path).
"""
import pytest


# ── projects ────────────────────────────────────────────────────────────────
def test_project_crud_and_bible(client):
    r = client.post("/api/projects", json={"name": "Route Saga", "type": "video-series"})
    assert r.status_code == 200
    pid = r.get_json()["project"]["id"]

    # appears in the list
    lst = client.get("/api/projects").get_json()
    assert any(p["id"] == pid for p in lst["projects"])

    # add a character with a propagating visual description
    rc = client.post(f"/api/projects/{pid}/characters",
                     json={"name": "Maya", "visual_description": "silver-haired pilot"})
    assert rc.get_json()["status"] == "ok"

    # full bible reflects the cast
    bible = client.get(f"/api/projects/{pid}").get_json()["project"]
    assert bible["characters"][0]["name"] == "Maya"

    # activate + active endpoint
    client.post(f"/api/projects/{pid}/activate")
    assert client.get("/api/projects/active").get_json()["active_id"] == pid

    # continuity + style
    client.post(f"/api/projects/{pid}/continuity", json={"note": "It rains", "scene": "1"})
    client.post(f"/api/projects/{pid}/style", json={"style_guide": {"genre": "noir"}})
    bible = client.get(f"/api/projects/{pid}").get_json()["project"]
    assert bible["continuity"][0]["note"] == "It rains"
    assert bible["style_guide"]["genre"] == "noir"

    # cleanup
    assert client.delete(f"/api/projects/{pid}").get_json()["deleted"] is True


def test_create_project_requires_name(client):
    r = client.post("/api/projects", json={})
    assert r.get_json()["status"] == "error"


# ── pipelines ───────────────────────────────────────────────────────────────
def test_pipeline_templates_listed(client):
    r = client.get("/api/pipelines/templates")
    ids = [t["id"] for t in r.get_json()["templates"]]
    assert "research-brief-draft-review" in ids


def test_pipeline_run_create_and_advance(client):
    create = client.post("/api/pipelines/runs",
                         json={"pipeline_id": "research-brief-draft-review",
                               "input": {"topic": "clean energy"}})
    assert create.status_code == 200
    run = create.get_json()["run"]
    rid = run["run_id"]
    assert run["state"] == "pending"

    # advance one stage (research)
    adv = client.post(f"/api/pipelines/runs/{rid}/advance").get_json()["run"]
    assert adv["stage_index"] == 1
    assert "research" in adv["context"]


def test_pipeline_run_sync_to_checkpoint(client):
    create = client.post("/api/pipelines/runs",
                         json={"pipeline_id": "research-brief-draft-review",
                               "input": {"topic": "tides"}, "start": True, "sync": True})
    run = create.get_json()["run"]
    # brief stage is a checkpoint → pauses there
    assert run["state"] == "awaiting_checkpoint"
    assert "brief" in run["context"]

    # resume to completion
    resumed = client.post(f"/api/pipelines/runs/{run['run_id']}/resume",
                          json={"run_to_end": True}).get_json()["run"]
    assert resumed["state"] == "completed"
    assert resumed.get("final")


def test_pipeline_unknown_returns_error(client):
    r = client.post("/api/pipelines/runs", json={"pipeline_id": "nope"})
    assert r.get_json()["status"] == "error"


# ── scene dna ───────────────────────────────────────────────────────────────
def test_scene_dna_layers_and_edit(client):
    layers = client.get("/api/scene-dna/layers").get_json()
    assert "mood" in layers["layers"]

    edit = client.post("/api/scene-dna/edit",
                       json={"scene_dna": {"setting": "a dock"}, "layer": "mood",
                             "value": "tense"}).get_json()
    assert edit["scene_dna"]["mood"] == "tense"
    assert edit["scene_dna"]["setting"] == "a dock"
    assert "tense" in edit["prompt"]


def test_scene_dna_edit_rejects_unknown_layer(client):
    r = client.post("/api/scene-dna/edit",
                    json={"scene_dna": {}, "layer": "lighting", "value": "x"})
    assert r.get_json()["status"] == "error"


# ── qa + takes ──────────────────────────────────────────────────────────────
def test_qa_evaluate_returns_verdict(client):
    r = client.post("/api/qa/evaluate",
                    json={"content": "some draft", "intent": "be sharp"})
    assert r.status_code == 200
    assert "verdict" in r.get_json()


def test_qa_config_route(client):
    cfg = client.get("/api/qa/config").get_json()["config"]
    assert "threshold" in cfg


def test_takes_text_recommends(client):
    r = client.post("/api/takes/text",
                    json={"prompt": "write a tagline", "intent": "punchy", "n": 3})
    body = r.get_json()
    assert body["status"] == "ok"
    assert len(body["takes"]) == 3
    assert "recommended_index" in body


def test_takes_images_unavailable_without_key(client, monkeypatch):
    from services import creative_engine as ce
    monkeypatch.setattr(ce, "is_available", lambda: False)
    r = client.post("/api/takes/images", json={"prompt": "a dragon", "n": 2})
    assert r.get_json()["status"] in ("unavailable", "error")

"""/api/evolution only stores a structure index the desktop scene has.

A stored index outside the 13 structures used to turn every later page load's
GET into a 500 (IndexError on the name list), and the client then had no
scene to show. POST refuses such an index before storing it; GET treats one
already on disk as no pin and falls back to the calendar.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def evo(tmp_path, monkeypatch):
    from agent_friday.routes import insights
    monkeypatch.setattr(insights, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(insights, "EVOLUTION_FILE", tmp_path / "evolution.json")
    return tmp_path


@pytest.mark.parametrize("bad", [13, 99, -1, "3", 2.5, True, [1], {"i": 1}])
def test_post_refuses_an_index_that_is_not_a_structure(client, evo, bad):
    r = client.post("/api/evolution", json={"preferred_scene_index": bad})
    assert r.status_code == 400
    assert "0 to 12" in r.get_json()["error"]
    stored = json.loads((evo / "evolution.json").read_text("utf-8")) \
        if (evo / "evolution.json").exists() else {}
    assert "preferred_scene_index" not in stored
    assert client.get("/api/evolution").status_code == 200


@pytest.mark.parametrize("good", [0, 7, 12])
def test_post_keeps_a_real_structure(client, evo, good):
    r = client.post("/api/evolution", json={"preferred_scene_index": good})
    assert r.status_code == 200
    d = client.get("/api/evolution").get_json()
    assert d["structure_index"] == good and d["preferred_scene_index"] == good


def test_null_still_clears_the_pin(client, evo):
    client.post("/api/evolution", json={"preferred_scene_index": 4})
    r = client.post("/api/evolution", json={"preferred_scene_index": None})
    assert r.status_code == 200
    d = client.get("/api/evolution").get_json()
    assert d["preferred_scene_index"] is None
    assert d["structure_index"] == d["calendar_index"]


@pytest.mark.parametrize("stored", [13, 250, -3, "5", True])
def test_a_bad_pin_already_on_disk_does_not_break_the_page(client, evo, stored):
    (evo / "evolution.json").write_text(
        json.dumps({"first_launch": "2026-09-01", "preferred_scene_index": stored}),
        encoding="utf-8")
    r = client.get("/api/evolution")
    assert r.status_code == 200
    d = r.get_json()
    assert d["preferred_scene_index"] is None
    assert d["structure_index"] == d["calendar_index"]
    assert 0 <= d["structure_index"] <= 12

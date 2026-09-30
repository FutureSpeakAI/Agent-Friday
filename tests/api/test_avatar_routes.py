"""The avatar API the page and voice use (avatar-visual-genome.md §7, §8)."""
import json

import pytest

from agent_friday.services import avatar_genome as g
from agent_friday.services import avatar_growth as gr


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.governance.proof_of_integrity import IntegrityEngine
    eng = IntegrityEngine(friday_dir=tmp_path / "identity")
    monkeypatch.setattr(g, "_engine", lambda: eng)
    monkeypatch.setattr(g, "AVATAR_DIR", tmp_path / "avatar")
    monkeypatch.setattr(gr, "EVOLUTION_FILE", tmp_path / "evolution.json")
    monkeypatch.setattr(gr, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(gr, "_local_seat", lambda: None)
    return tmp_path


def test_the_genome_endpoint_serves_v1_and_never_the_seed(client, home):
    r = client.get("/api/avatar/genome")
    assert r.status_code == 200
    d = r.get_json()
    assert d["v1"] is True and d["step"] is None
    assert d["expression"]["CUBES"]["spacing"] == 1.6
    assert d["palette"]["moods"]["IDLE"]["base"] == "#1e54c7"
    assert "sigil" in d
    assert g.seed().hex() not in r.get_data(as_text=True)


def test_evolve_now_undo_and_rollback_over_http(client, home):
    assert client.post("/api/avatar/settings", json={"author": "seeded"}).status_code == 200
    r = client.post("/api/avatar/evolve-now")
    assert r.status_code == 200 and r.get_json()["status"] == "stepped"
    first = r.get_json()["step"]
    client.post("/api/avatar/evolve-now")
    hist = client.get("/api/avatar/history").get_json()["steps"]
    assert len(hist) == 2
    assert client.post("/api/avatar/undo").get_json()["status"] == "undone"
    assert client.get("/api/avatar/genome").get_json()["step"]["content_hash"] == first
    assert client.post("/api/avatar/reset").status_code == 200
    assert client.get("/api/avatar/genome").get_json()["v1"] is True
    assert client.post("/api/avatar/rollback", json={"step": first}).status_code == 200
    assert client.get("/api/avatar/genome").get_json()["step"]["content_hash"] == first


def test_a_bad_author_or_step_is_a_400_not_a_500(client, home):
    assert client.post("/api/avatar/settings", json={"author": "rm -rf"}).status_code == 400
    assert client.post("/api/avatar/settings", json={"apply_mode": "maybe"}).status_code == 400
    assert client.post("/api/avatar/rollback", json={"step": "nope"}).status_code == 404
    assert client.post("/api/avatar/rollback", json={}).status_code == 404
    assert client.post("/api/avatar/delete", json={"step": "sha256:" + "0" * 64}).status_code == 404


def test_the_status_says_who_authors_and_whether_it_is_waiting(client, home, monkeypatch):
    monkeypatch.setattr(gr, "_frontier_available", lambda: (False, "no cloud model is connected", []))
    d = client.get("/api/avatar/status").get_json()
    assert d["author"] == "frontier" and d["enabled"] is True
    r = client.post("/api/avatar/evolve-now").get_json()
    assert r["status"] == "waiting" and "connect_cloud" in r["fixes"]
    assert client.get("/api/avatar/status").get_json()["waiting"]["reason"]


def test_the_page_can_turn_evolution_off_and_on(client, home):
    client.post("/api/avatar/settings", json={"enabled": False})
    assert client.get("/api/avatar/status").get_json()["enabled"] is False
    client.post("/api/avatar/settings", json={"enabled": True})
    assert client.get("/api/avatar/status").get_json()["enabled"] is True

"""The codebase routes behind "+ Codebase" and the Preview, Files and Changes
tabs (docs/design/active/vibe-coding-salon.md §4.8, §5, Phase 2).
"""
from __future__ import annotations

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    yield


def test_the_blueprint_is_registered(server_module):
    assert "codebases" in server_module.ROUTE_MODULES
    assert "codebases" in server_module.BLUEPRINT_REPORT["registered"]


def test_plus_codebase_makes_a_repo_and_a_chat_bound_to_it(client):
    d = client.post("/api/codebases", json={"title": "Rent Tracker", "template": "static"}).get_json()
    assert d["status"] == "ok"
    rec, cid = d["codebase"], d["conversation"]["id"]
    assert rec["template"] == "static" and rec["tier"] == "B0"
    conv = client.get(f"/api/conversations/{cid}").get_json()["conversation"]
    assert conv["codebase"] == rec["id"] and conv["title"] == "Rent Tracker"
    listed = client.get("/api/codebases").get_json()["codebases"]
    assert [c["id"] for c in listed] == [rec["id"]]
    assert client.get(f"/api/codebases/{rec['id']}").get_json()["codebase"]["conversation_id"] == cid


def test_a_bad_template_or_folder_is_a_400(client, tmp_path):
    assert client.post("/api/codebases", json={"title": "x", "template": "rails"}).status_code == 400
    assert client.post("/api/codebases", json={"title": "x", "path": str(tmp_path / "missing")}).status_code == 400


def test_files_read_edit_steps_undo_and_preview(client):
    rec = client.post("/api/codebases", json={"title": "T", "template": "static"}).get_json()["codebase"]
    base = f"/api/codebases/{rec['id']}"
    files = client.get(base + "/files").get_json()["files"]
    assert "index.html" in [f["path"] for f in files]
    got = client.get(base + "/file?path=index.html").get_json()
    assert got["content"].startswith("<!doctype html>") or "<html" in got["content"]
    e = client.post(base + "/file", json={"path": "index.html", "content": "<h1>edited</h1>"}).get_json()
    assert e["step"]["author"] == "you"
    steps = client.get(base + "/steps").get_json()["steps"]
    assert [s["kind"] for s in steps][:2] == ["step", "start"]
    assert "edited" in client.get(base + "/preview").get_json()["html"]
    d = client.get(base + "/diff/" + steps[0]["sha"]).get_json()["diff"]
    assert "+<h1>edited</h1>" in d
    u = client.post(base + "/undo").get_json()
    assert u["step"]["kind"] == "undo"
    assert "edited" not in client.get(base + "/file?path=index.html").get_json()["content"]
    r = client.post(base + "/undo")
    assert r.status_code == 409, "nothing left to undo is a plain answer"


def test_paths_are_contained_and_ids_are_plain(client):
    rec = client.post("/api/codebases", json={"title": "T"}).get_json()["codebase"]
    base = f"/api/codebases/{rec['id']}"
    assert client.get(base + "/file?path=../../etc").status_code in (400, 404)
    assert client.post(base + "/file", json={"path": ".git/config", "content": "x"}).status_code == 400
    assert client.get("/api/codebases/..%2F..%2Fx/files").status_code in (400, 404)
    assert client.get("/api/codebases/cb-nothere/files").status_code == 404

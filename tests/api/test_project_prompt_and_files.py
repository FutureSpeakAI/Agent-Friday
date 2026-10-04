"""A chat inside a project is told, every turn, what the project holds (Chat Hub
M2): the standing instructions, the files, the connected codebases. A chat
outside one is told nothing. The project's files are kept and served by their
own routes.
"""
from __future__ import annotations

import pytest

from tests.api.turn_context_helpers import (
    CHAT_ROUTES, capture_requests, chat_requests, route_to, turn_prompt, whole_request)

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import projects


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    monkeypatch.setattr(projects, "_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


@pytest.fixture
def captured(patch_app):
    return capture_requests(patch_app)


def _project_with_a_chat(client):
    p = client.post("/api/projects", json={"name": "Parks desk", "instructions": "Answer like a city reporter."}).get_json()["project"]
    r = client.post(f"/api/projects/{p['id']}/files", json={"name": "notes.md", "content": "The pier closes at dusk."})
    assert r.status_code == 201, r.data[:300]
    conv = client.post("/api/conversations", json={"title": "Field notes", "project": p["id"]}).get_json()["conversation"]
    return p, conv


@pytest.mark.parametrize("route,provider", CHAT_ROUTES)
def test_the_prompt_carries_the_projects_instructions_and_files(client, captured, monkeypatch,
                                                                route, provider):
    route_to(monkeypatch, provider)
    p, conv = _project_with_a_chat(client)
    r = client.post(route, json={"message": "what closes at dusk?", "conversation_id": conv["id"]})
    assert r.status_code == 200, r.data[:300]
    joined = "\n".join(turn_prompt(q, route, said="what closes at dusk?")
                       for q in chat_requests(captured, route))
    assert projects.CONTEXT_HEADER in joined and "Parks desk" in joined
    assert "Answer like a city reporter." in joined
    assert "notes.md" in joined and "The pier closes at dusk." in joined


@pytest.mark.parametrize("route,provider", CHAT_ROUTES)
def test_a_chat_outside_any_project_is_told_nothing(client, captured, monkeypatch, route, provider):
    route_to(monkeypatch, provider)
    _project_with_a_chat(client)
    cid = client.post("/api/conversations", json={"title": "Plain"}).get_json()["conversation"]["id"]
    client.post(route, json={"message": "hi", "conversation_id": cid})
    assert chat_requests(captured, route)
    # Every word of the request, system and messages alike.
    joined = whole_request(captured)
    assert projects.CONTEXT_HEADER not in joined and "city reporter" not in joined


def test_project_files_are_kept_listed_served_and_removed(client):
    p = client.post("/api/projects", json={"name": "Files"}).get_json()["project"]
    base = f"/api/projects/{p['id']}/files"
    assert client.post(base, json={"name": "notes.md", "content": "hello"}).status_code == 201
    listed = client.get(base).get_json()["files"]
    assert [f["name"] for f in listed] == ["notes.md"] and listed[0]["bytes"] == 5
    got = client.get(base + "/notes.md")
    assert got.status_code == 200 and got.data == b"hello"
    assert client.get(base + "/missing.md").status_code == 404
    too_big = client.post(base, json={"name": "big.txt", "content": "x" * (projects.MAX_FILE_BYTES + 1)})
    assert too_big.status_code == 413
    assert client.delete(base + "/notes.md").status_code == 200
    assert client.get(base).get_json()["files"] == []
    # the project summary counts them
    summary = client.get(f"/api/projects/{p['id']}").get_json()["project"]
    assert summary["files"] == 0 and "codebases" in summary


def test_the_project_summary_names_its_connected_codebases(client):
    p = client.post("/api/projects", json={"name": "Builds"}).get_json()["project"]
    d = client.post("/api/codebases", json={"title": "Rent tracker", "template": "static"}).get_json()
    r = client.post(f"/api/projects/{p['id']}/codebases", json={"codebase": d["codebase"]["id"]})
    assert r.status_code == 200, r.data[:300]
    summary = client.get(f"/api/projects/{p['id']}").get_json()["project"]
    assert summary["codebases"] == [d["codebase"]["id"]]
    assert client.delete(f"/api/projects/{p['id']}/codebases/{d['codebase']['id']}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").get_json()["project"]["codebases"] == []

"""A chat inside a project is told, every turn, what the project holds (Chat Hub
M2): the standing instructions, the files, the connected codebases. A chat
outside one is told nothing. The project's files are kept and served by their
own routes.
"""
from __future__ import annotations

import pytest

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
    seen = []

    def fake_agent(messages, *a, **k):
        seen.append(k.get("system") if "system" in k else (a[0] if a else ""))
        return ("ok", [])

    def fake_text(*a, **k):
        seen.append(k.get("system") or "")
        return "ok"

    for name in ("_generate_agent", "_call_claude_agent", "_oai_agentic_loop"):
        patch_app(name, fake_agent)
    for name in ("_generate_text", "_call_claude", "_call_ollama", "_call_openai"):
        patch_app(name, fake_text)
    return seen


def _project_with_a_chat(client):
    p = client.post("/api/projects", json={"name": "Parks desk", "instructions": "Answer like a city reporter."}).get_json()["project"]
    r = client.post(f"/api/projects/{p['id']}/files", json={"name": "notes.md", "content": "The pier closes at dusk."})
    assert r.status_code == 201, r.data[:300]
    conv = client.post("/api/conversations", json={"title": "Field notes", "project": p["id"]}).get_json()["conversation"]
    return p, conv


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/send"])
def test_the_prompt_carries_the_projects_instructions_and_files(client, captured, route):
    p, conv = _project_with_a_chat(client)
    r = client.post(route, json={"message": "what closes at dusk?", "conversation_id": conv["id"]})
    assert r.status_code == 200, r.data[:300]
    joined = "\n".join(x for x in captured if x)
    assert projects.CONTEXT_HEADER in joined and "Parks desk" in joined
    assert "Answer like a city reporter." in joined
    assert "notes.md" in joined and "The pier closes at dusk." in joined


def test_a_chat_outside_any_project_is_told_nothing(client, captured):
    _project_with_a_chat(client)
    cid = client.post("/api/conversations", json={"title": "Plain"}).get_json()["conversation"]["id"]
    client.post("/api/chat", json={"message": "hi", "conversation_id": cid})
    joined = "\n".join(x for x in captured if x)
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

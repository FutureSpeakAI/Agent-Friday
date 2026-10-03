"""A codebase chat tells the model, every turn, what its codebase holds
(docs/design/active/vibe-coding-salon.md §4.8): the files, the last steps and
how to change them. Another conversation is told nothing about it.
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


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/send"])
def test_the_prompt_carries_the_codebase_files_and_steps(client, captured, route):
    d = client.post("/api/codebases", json={"title": "Rent Tracker", "template": "static"}).get_json()
    cid, cbid = d["conversation"]["id"], d["codebase"]["id"]
    cb.step(cbid, {"index.html": "<h1>rent</h1>"}, "Rent heading")
    r = client.post(route, json={"message": "make it bigger", "conversation_id": cid})
    assert r.status_code == 200, r.data[:300]
    joined = "\n".join(p for p in captured if p)
    assert cb.CONTEXT_HEADER in joined and cbid in joined
    assert "<h1>rent</h1>" in joined and "Rent heading" in joined
    assert "codebase_edit" in joined


def test_a_plain_chat_is_told_nothing_about_codebases(client, captured):
    client.post("/api/codebases", json={"title": "Elsewhere", "template": "static"})
    cid = client.post("/api/conversations", json={"title": "Plain"}).get_json()["conversation"]["id"]
    client.post("/api/chat", json={"message": "hi", "conversation_id": cid})
    joined = "\n".join(p for p in captured if p)
    assert cb.CONTEXT_HEADER not in joined and "Elsewhere" not in joined

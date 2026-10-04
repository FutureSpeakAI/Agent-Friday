"""A codebase chat tells the model, every turn, what its codebase holds
(docs/design/active/vibe-coding-salon.md §4.8): the files, the last steps and
how to change them. Another conversation is told nothing about it.
"""
from __future__ import annotations

import pytest

from tests.api.turn_context_helpers import (
    CHAT_ROUTES, capture_requests, chat_requests, route_to, turn_prompt, whole_request)

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    yield


@pytest.fixture
def captured(patch_app):
    return capture_requests(patch_app)


@pytest.mark.parametrize("route,provider", CHAT_ROUTES)
def test_the_prompt_carries_the_codebase_files_and_steps(client, captured, monkeypatch,
                                                         route, provider):
    route_to(monkeypatch, provider)
    d = client.post("/api/codebases", json={"title": "Rent Tracker", "template": "static"}).get_json()
    cid, cbid = d["conversation"]["id"], d["codebase"]["id"]
    cb.step(cbid, {"index.html": "<h1>rent</h1>"}, "Rent heading")
    r = client.post(route, json={"message": "make it bigger", "conversation_id": cid})
    assert r.status_code == 200, r.data[:300]
    joined = "\n".join(turn_prompt(q, route, said="make it bigger")
                       for q in chat_requests(captured, route))
    assert cb.CONTEXT_HEADER in joined and cbid in joined
    assert "<h1>rent</h1>" in joined and "Rent heading" in joined
    assert "codebase_edit" in joined


@pytest.mark.parametrize("route,provider", CHAT_ROUTES)
def test_a_plain_chat_is_told_nothing_about_codebases(client, captured, monkeypatch,
                                                      route, provider):
    route_to(monkeypatch, provider)
    client.post("/api/codebases", json={"title": "Elsewhere", "template": "static"})
    cid = client.post("/api/conversations", json={"title": "Plain"}).get_json()["conversation"]["id"]
    client.post(route, json={"message": "hi", "conversation_id": cid})
    assert chat_requests(captured, route)
    # Every word of the request, system and messages: the per-turn content is
    # not in the system prompt on /api/chat, so a system-only check is empty.
    joined = whole_request(captured)
    assert cb.CONTEXT_HEADER not in joined and "Elsewhere" not in joined

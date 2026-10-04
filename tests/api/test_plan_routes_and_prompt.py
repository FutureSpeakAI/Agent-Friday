"""Approving a plan from the panel, and the prompt carrying the plan's state."""
from __future__ import annotations

import pytest

from tests.api.turn_context_helpers import (
    CHAT_ROUTES, capture_requests, chat_requests, route_to, turn_prompt)

from agent_friday.services import artifacts as art
from agent_friday.services import plans


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    yield


def _new_conv(client):
    return client.post("/api/conversations", json={"title": "Plans"}).get_json()["conversation"]["id"]


def test_the_panel_approves_a_plan(client):
    cid = _new_conv(client)
    rec = plans.create(cid, "Tracker", "# Plan\n", ["One", "Two"])
    r = client.post(f"/api/artifacts/{cid}/{rec['id']}/plan/approve")
    d = r.get_json()
    assert r.status_code == 200 and d["artifact"]["meta"]["plan"]["approved"] is True
    msgs = client.get(f"/api/conversations/{cid}/messages").get_json()["messages"]
    assert any(m["role"] == "system" and "plan" in m["text"].lower() and "approved" in m["text"].lower() for m in msgs)
    assert client.post(f"/api/artifacts/{cid}/art-none/plan/approve").status_code == 404
    other = art.put(cid, kind="markdown", title="Not a plan", content="x")
    assert client.post(f"/api/artifacts/{cid}/{other['id']}/plan/approve").status_code == 400


@pytest.fixture
def captured(patch_app):
    return capture_requests(patch_app)


@pytest.mark.parametrize("route,provider", CHAT_ROUTES)
def test_the_prompt_says_wait_then_says_build(client, captured, monkeypatch, route, provider):
    route_to(monkeypatch, provider)
    cid = _new_conv(client)
    rec = plans.create(cid, "Tracker", "# Plan\n", ["Make the page"])
    client.post(route, json={"message": "ok?", "conversation_id": cid})
    joined = "\n".join(turn_prompt(q, route, said="ok?") for q in chat_requests(captured, route))
    assert plans.CONTEXT_HEADER in joined and "do not build" in joined.lower()
    captured.clear()
    plans.approve(cid, rec["id"])
    client.post(route, json={"message": "go", "conversation_id": cid})
    joined = "\n".join(turn_prompt(q, route, said="go") for q in chat_requests(captured, route))
    assert "next: milestone 1" in joined.lower() and "Make the page" in joined

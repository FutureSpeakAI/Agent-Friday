"""Approving a plan from the panel, and the prompt carrying the plan's state."""
from __future__ import annotations

import pytest

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
def test_the_prompt_says_wait_then_says_build(client, captured, route):
    cid = _new_conv(client)
    rec = plans.create(cid, "Tracker", "# Plan\n", ["Make the page"])
    client.post(route, json={"message": "ok?", "conversation_id": cid})
    joined = "\n".join(p for p in captured if p)
    assert plans.CONTEXT_HEADER in joined and "do not build" in joined.lower()
    captured.clear()
    plans.approve(cid, rec["id"])
    client.post(route, json={"message": "go", "conversation_id": cid})
    joined = "\n".join(p for p in captured if p)
    assert "next: milestone 1" in joined.lower() and "Make the page" in joined

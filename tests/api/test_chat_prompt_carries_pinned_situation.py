"""A situation pinned with check_situation(pin=true) is in view on every later
turn of that conversation, read at the start of the turn, on both chat
endpoints, and on no other conversation."""
from __future__ import annotations

import pytest

from agent_friday.services import situation

HEADER = "== SITUATION (pinned; live, read at the start of this turn) =="


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


@pytest.fixture
def pins(monkeypatch):
    monkeypatch.setitem(situation._PINS, "loaded", True)
    monkeypatch.setitem(situation._PINS, "ids", {})
    return situation


def _main_id(client):
    return client.get("/api/conversations").get_json()["main_id"]


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/send"])
def test_a_pinned_conversation_carries_the_live_situation(client, captured, pins, route):
    cid = _main_id(client)
    pins.set_pinned(cid, True)
    try:
        r = client.post(route, json={"message": "how is it going?", "conversation_id": cid})
    finally:
        pins.set_pinned(cid, False)
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    systems = [s for s in captured if isinstance(s, str) and s]
    assert systems and all(HEADER in s for s in systems)
    assert all("Situation at " in s.split(HEADER, 1)[1] for s in systems)


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/send"])
def test_an_unpinned_conversation_does_not(client, captured, pins, route):
    r = client.post(route, json={"message": "how is it going?",
                                 "conversation_id": _main_id(client)})
    assert r.status_code == 200
    systems = [s for s in captured if isinstance(s, str) and s]
    assert systems and not any(HEADER in s for s in systems)

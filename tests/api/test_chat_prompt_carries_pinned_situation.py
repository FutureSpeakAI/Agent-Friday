"""A situation pinned with check_situation(pin=true) is in view on every later
turn of that conversation, read at the start of the turn, on both chat
endpoints, and on no other conversation."""
from __future__ import annotations

import pytest

from tests.api.turn_context_helpers import (
    CHAT_ROUTES, capture_requests, chat_requests, route_to, turn_prompt, whole_request)

from agent_friday.services import situation

HEADER = "== SITUATION (pinned; live, read at the start of this turn) =="


@pytest.fixture
def captured(patch_app):
    return capture_requests(patch_app)


@pytest.fixture
def pins(monkeypatch):
    monkeypatch.setitem(situation._PINS, "loaded", True)
    monkeypatch.setitem(situation._PINS, "ids", {})
    return situation


def _main_id(client):
    return client.get("/api/conversations").get_json()["main_id"]


@pytest.mark.parametrize("route,provider", CHAT_ROUTES)
def test_a_pinned_conversation_carries_the_live_situation(client, captured, pins, monkeypatch,
                                                          route, provider):
    route_to(monkeypatch, provider)
    cid = _main_id(client)
    pins.set_pinned(cid, True)
    try:
        r = client.post(route, json={"message": "how is it going?", "conversation_id": cid})
    finally:
        pins.set_pinned(cid, False)
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    prompts = [turn_prompt(q, route, said="how is it going?")
               for q in chat_requests(captured, route)]
    assert prompts and all(HEADER in s for s in prompts)
    assert all("Situation at " in s.split(HEADER, 1)[1] for s in prompts)


@pytest.mark.parametrize("route,provider", CHAT_ROUTES)
def test_an_unpinned_conversation_does_not(client, captured, pins, monkeypatch, route, provider):
    route_to(monkeypatch, provider)
    r = client.post(route, json={"message": "how is it going?",
                                 "conversation_id": _main_id(client)})
    assert r.status_code == 200
    assert chat_requests(captured, route)
    # Every word of the request, system and messages alike.
    assert HEADER not in whole_request(captured)

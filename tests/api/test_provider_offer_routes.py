"""A6 over HTTP: a failed Anthropic turn comes back as an offer, the owner's button
sends that one turn to OpenRouter, and the fallback order is set only by a press.
Providers are stubbed; nothing leaves the test."""
from __future__ import annotations

import pytest

import agent_friday.routes.chat as chat_mod


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    from agent_friday import notifications_engine as ne
    try:
        from agent_friday.services import provider_fallback as pf
    except ImportError:  # the old tree: the tests run and fail by assertion
        pf = None
    if pf is not None:
        pf._reset_for_tests()
    monkeypatch.setattr(ne, "push", lambda **kw: {}, raising=False)
    yield
    if pf is not None:
        pf._reset_for_tests()


def _page():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token()}


def _anthropic_down_openrouter_ready(monkeypatch):
    from agent_friday.services import one_key
    monkeypatch.setattr(one_key, "openrouter_ready", lambda: True)
    monkeypatch.setattr(chat_mod, "get_anthropic_client", lambda *a, **k: object(), raising=False)
    calls = {"claude": 0, "openai": 0}

    def claude(*a, **k):
        calls["claude"] += 1
        raise RuntimeError("Error code: 529 - overloaded")

    def openai(*a, **k):
        calls["openai"] += 1
        return "answered through OpenRouter", []

    monkeypatch.setattr(chat_mod, "_call_claude_agent", claude)
    monkeypatch.setattr(chat_mod, "_call_openai", openai)
    return calls


def test_a_failed_anthropic_turn_is_an_offer_not_a_switch(client, monkeypatch):
    calls = _anthropic_down_openrouter_ready(monkeypatch)
    r = client.post("/api/chat", json={"message": "hello there"})
    d = r.get_json()
    assert calls["openai"] == 0, "the turn moved to OpenRouter on its own"
    offer = d.get("local_fallback") or {}
    assert offer.get("kind") == "cloud_provider_offer", d
    ids = [c["id"] for c in offer.get("choices", [])]
    assert ids == ["use_alternative", "retry_same"]
    assert "did not switch" in d["response"]


def test_the_offers_button_sends_that_turn_to_openrouter(client, monkeypatch):
    calls = _anthropic_down_openrouter_ready(monkeypatch)
    r = client.post("/api/chat", json={"message": "hello there", "cloud_fallback_choice": "openai"})
    assert r.status_code == 200
    assert calls["openai"] == 1 and calls["claude"] == 0, calls


def test_the_order_is_set_only_by_a_press(client):
    for headers in ({}, {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost"}):
        r = client.post("/api/models/cloud-fallback-order", json={"order": "anthropic_then_openai"},
                        headers=headers)
        assert r.status_code == 403
    assert client.get("/api/models/cloud-fallback-order").get_json()["order"] == "ask"
    # The general settings save drops it.
    client.post("/api/settings", json={"model_routing": {"cloud_fallback_order": "anthropic_then_openai"}},
                headers=_page())
    assert client.get("/api/models/cloud-fallback-order").get_json()["order"] == "ask"
    r = client.post("/api/models/cloud-fallback-order", json={"order": "anthropic_then_openai"},
                    headers=_page())
    try:
        assert r.status_code == 200 and r.get_json()["order"] == "anthropic_then_openai"
    finally:
        client.post("/api/models/cloud-fallback-order", json={"order": "ask"}, headers=_page())


def test_an_unknown_order_is_refused(client):
    r = client.post("/api/models/cloud-fallback-order", json={"order": "always"}, headers=_page())
    assert r.status_code == 400

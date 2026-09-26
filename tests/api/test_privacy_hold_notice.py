"""A message held because the privacy check is still starting is never silent.

While Layer 3 of the egress classifier is installed but not running yet,
cloud-bound text with no other privacy signal is held at the egress
chokepoint. The user must see that at once, with two choices: send anyway
(pattern filters only) or wait for the full check. These tests drive the two
chat endpoints with a fake model call that seals its payload exactly as the
real providers do (model_router._seal_or_block), and check that the notice
comes back whether the hold reaches the top of the turn or a fallback
swallows it and answers locally.

A packaged build ships without Layer 3 on purpose; it keeps sending.
"""
from __future__ import annotations

import pytest

from agent_friday.services import egress_gate as eg
from agent_friday.services import sensitivity_classifier as sc

TEXT = "she stays with me every other weekend and walks to the school by the park"
ROUTES = ("/api/chat", "/api/chat/send")


@pytest.fixture
def layer3(monkeypatch):
    """Layer 3 installed but not running: the state after a lost boot race."""
    state = {"expected": True}
    monkeypatch.setattr(sc, "_load_embedder", lambda: None)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: state["expected"])
    monkeypatch.setattr(eg, "_rate_limit", lambda: None)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)
    monkeypatch.setattr(eg, "gate_operational", lambda: True)
    return state


@pytest.fixture
def model(patch_app):
    """A model call that seals like a real provider, then answers."""
    seen = {"sent": [], "mode": "raise"}

    def fake_agent(messages, *a, **k):
        from agent_friday.services.model_router import _seal_or_block
        try:
            sealed = _seal_or_block({"messages": messages}, "anthropic")
        except sc.PrivacyCheckStarting:
            if seen["mode"] == "fallback":
                return ("answered by the local model", [])   # a fallback swallowed it
            raise
        seen["sent"].append(sealed)
        return ("cloud reply", [])

    def fake_text(*a, **k):
        return fake_agent([{"role": "user", "content": TEXT}])[0]

    for name in ("_generate_agent", "_call_claude_agent", "_oai_agentic_loop"):
        patch_app(name, fake_agent)
    for name in ("_generate_text", "_call_claude", "_call_ollama", "_call_openai"):
        patch_app(name, fake_text)
    return seen


def _sent_text(seen):
    return " ".join(str(m.get("content")) for p in seen["sent"] for m in p.get("messages", []))


@pytest.mark.parametrize("route", ROUTES)
def test_a_held_message_always_comes_back_with_the_notice(client, layer3, model, route):
    r = client.post(route, json={"message": TEXT})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    d = r.get_json()
    hold = d.get("privacy_hold")
    assert hold, "a message was withheld and the user was not told"
    assert hold["text"] == sc.HOLD_NOTICE and hold["message"] == TEXT
    assert [o["id"] for o in hold["options"]] == ["send_anyway", "wait"]
    assert "pattern filters only" in hold["options"][0]["label"]
    assert TEXT not in _sent_text(model), "held text reached the cloud"
    assert "cloud reply" not in (d.get("response") or "")


@pytest.mark.parametrize("route", ROUTES)
def test_the_notice_survives_a_fallback_that_swallows_the_hold(client, layer3, model, route):
    model["mode"] = "fallback"
    d = client.post(route, json={"message": TEXT}).get_json()
    assert d.get("privacy_hold"), "the hold was swallowed silently"
    assert TEXT not in _sent_text(model)


@pytest.mark.parametrize("route", ROUTES)
def test_send_anyway_sends_with_pattern_filters_only(client, layer3, model, route):
    d = client.post(route, json={"message": TEXT, "privacy_layer3_override": True}).get_json()
    assert not d.get("privacy_hold")
    assert TEXT in _sent_text(model), "the user chose to send and it was not sent"


def test_a_packaged_build_without_layer3_still_sends(client, layer3, model):
    """Absent by design (the .exe excludes sentence_transformers): no hold."""
    layer3["expected"] = False
    d = client.post("/api/chat", json={"message": TEXT}).get_json()
    assert not d.get("privacy_hold")
    assert TEXT in _sent_text(model)


def test_a_hold_outside_any_turn_is_announced(layer3, monkeypatch):
    pushed = []

    class Engine:
        def push(self, **k):
            pushed.append(k)

    from agent_friday.services import notifications
    monkeypatch.setattr(notifications, "_notif_engine", Engine())
    monkeypatch.setattr(sc, "_BG_NOTIFIED_AT", [-1e9])
    assert eg._classify_cloud(TEXT) >= sc.Tier.PRIVATE
    assert pushed and "privacy check" in pushed[0]["title"].lower()

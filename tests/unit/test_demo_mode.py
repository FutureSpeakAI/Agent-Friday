"""Unit tests for services.demo_mode — the no-provider exploration fallback."""
from agent_friday.services import demo_mode as dm


def test_explicit_overrides():
    assert dm.is_demo({"demo_mode": True}) is True
    assert dm.is_demo({"demo_mode": False}) is False


def test_auto_demo_follows_provider_availability(monkeypatch):
    monkeypatch.setattr(dm, "_any_provider_available", lambda: False)
    assert dm.is_demo({"demo_mode": None}) is True
    monkeypatch.setattr(dm, "_any_provider_available", lambda: True)
    assert dm.is_demo({"demo_mode": None}) is False


def test_demo_response_is_labelled():
    r = dm.demo_response("chat", "hello there")
    assert r.startswith("[DEMO]") and "hello there" in r
    assert dm.demo_response("image").startswith("[DEMO]")
    assert dm.demo_response("voice").startswith("[DEMO]")
    assert dm.demo_response("nope").startswith("[DEMO]")  # unknown kind → generic


def test_demo_status_shape():
    s = dm.demo_status({"demo_mode": True})
    assert s["demo_mode"] is True and "DEMO MODE" in s["banner"]

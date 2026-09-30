"""Pilot measurements are available without loading a model or changing gates."""
from agent_friday.services import laya_backend, laya_pilot


def test_status_is_cheap_even_while_pilot_waits_for_laya(client, monkeypatch, patch_app):
    def no_load(*args, **kwargs):
        raise AssertionError("status must not load a model")

    patch_app("_load_settings", lambda: {"laya_pilot_enabled": True, "off_record": True})
    monkeypatch.setattr(laya_backend, "start_warming", no_load)
    monkeypatch.setattr(laya_backend, "is_ready", lambda: False)
    monkeypatch.setattr(laya_pilot, "snapshot", lambda: {
        "scope": "since_restart", "count": 0, "capacity": 256, "groups": [], "rows": []})
    response = client.get("/api/decisions/laya_pilot")
    assert response.status_code == 200
    result = response.get_json()
    assert result["enabled"] is True
    assert result["ready"] is False
    assert result["off_record"] is True
    assert result["groups"] == []


def test_status_failure_is_visible(client, monkeypatch):
    def unavailable():
        raise RuntimeError("synthetic diagnostic failure")

    monkeypatch.setattr(laya_pilot, "snapshot", unavailable)
    response = client.get("/api/decisions/laya_pilot")
    assert response.status_code == 500
    assert response.get_json()["status"] == "error"

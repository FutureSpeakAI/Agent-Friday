"""A2 — /api/health must be able to fail (decision D1).

Before this change `friday_health` returned a literal `"status": "ok"`, and
provider health for the two primary cloud providers was `bool(api_key)`. A
revoked key, an out-of-credit account or a stopped Ollama daemon all reported
healthy. These tests pin the opposite property: when the backend cannot
generate, health says so.
"""
from __future__ import annotations

import pytest

from agent_friday.services import provider_health


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    provider_health.reset_probe_cache()
    yield
    provider_health.reset_probe_cache()


def _health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    return resp.get_json()


def test_health_reports_down_when_backend_cannot_generate(client, monkeypatch):
    """A downed backend must surface as status != ok — the core D1 property."""
    monkeypatch.setattr(provider_health, "inference_probe",
                        lambda name, prov=None, use_cache=True: {
                            "provider": name, "status": "down",
                            "detail": "simulated: revoked key",
                            "config": "ok", "proved_inference": False},
                        raising=False)

    data = _health(client)
    assert data["status"] == "down", (
        "health reported healthy while no provider could generate")
    assert data["inference"]["status"] == "down"
    assert all(p["proved_inference"] is False
               for p in data["inference"]["providers"])


def test_health_is_ok_only_when_a_probe_actually_generated(client, monkeypatch):
    monkeypatch.setattr(provider_health, "inference_probe",
                        lambda name, prov=None, use_cache=True: {
                            "provider": name, "status": "ok",
                            "detail": "generated in 5ms",
                            "config": "ok", "proved_inference": True},
                        raising=False)

    data = _health(client)
    assert data["status"] == "ok"
    assert data["inference"]["providers"]
    assert all(p["proved_inference"] for p in data["inference"]["providers"])


def test_key_presence_is_configuration_not_health(client, monkeypatch):
    """A present key must never on its own make health `ok` (D1)."""
    monkeypatch.setattr(provider_health, "inference_probe",
                        lambda name, prov=None, use_cache=True: {
                            "provider": name, "status": "down",
                            "detail": "simulated", "config": "ok",
                            "proved_inference": False},
                        raising=False)
    import agent_friday.core as core
    monkeypatch.setattr(core, "ANTHROPIC_API_KEY", "sk-present", raising=False)

    data = _health(client)
    assert data["configuration"]["anthropic_key"] is True
    assert data["status"] == "down", (
        "a configured key was allowed to stand in for working inference")


def test_shallow_check_never_claims_to_have_proved_inference():
    """The cheap path must advertise that it proved nothing."""
    res = provider_health._check("anthropic", deep=False)
    assert res.get("proved_inference") is False


def test_ollama_probe_uses_the_real_generation_call_site(monkeypatch):
    """D1 requires ollama_manager.health_check — previously zero call sites."""
    called = {}

    class _Mgr:
        def health_check(self, model):
            called["model"] = model
            return True

        def list_models(self):
            return [{"name": "gemma4:e4b", "size_gb": 3.4}]

    monkeypatch.setattr("agent_friday.routing.ollama_manager.get_manager",
                        lambda *a, **k: _Mgr(), raising=False)

    res = provider_health.inference_probe(
        "ollama-local",
        prov={"name": "ollama-local", "type": "ollama",
              "base_url": "http://localhost:11434"},
        use_cache=False)

    assert called, "health_check was not called — the probe is not real"
    assert res["status"] == "ok"
    assert res["proved_inference"] is True


def test_probe_failure_is_down_not_an_exception(monkeypatch):
    """A blowing-up backend must degrade to `down`, never raise into the route."""
    class _Mgr:
        def health_check(self, model):
            raise ConnectionRefusedError("ollama not running")

        def list_models(self):
            return [{"name": "gemma4:e4b", "size_gb": 3.4}]

    monkeypatch.setattr("agent_friday.routing.ollama_manager.get_manager",
                        lambda *a, **k: _Mgr(), raising=False)

    res = provider_health.inference_probe(
        "ollama-local",
        prov={"name": "ollama-local", "type": "ollama",
              "base_url": "http://localhost:11434"},
        use_cache=False)

    assert res["status"] == "down"
    assert res["proved_inference"] is False
    assert "ConnectionRefusedError" in res["detail"]

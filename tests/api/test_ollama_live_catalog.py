"""Spec A1 — dynamic local catalog: live Ollama inventory + running state in
the model catalog, pull disk-space preflight, and the short-TTL guarantee
that a mid-session `ollama pull` surfaces without a restart. All offline:
the Ollama manager is faked; disk_usage is monkeypatched.
"""
from __future__ import annotations

from collections import namedtuple

import pytest


class _FakeOllamaManager:
    """Daemon-up fake: two installed models, one loaded in memory."""
    base_url = "http://localhost:11434"

    def is_available(self):
        return True

    def list_models(self):
        return [
            {"name": "brandnew:7b", "model": "brandnew:7b", "size_gb": 4.2,
             "parameter_size": "7B", "family": "llama", "quantization": "Q4",
             "modified_at": "2026-08-13T10:00:00Z"},
            {"name": "idle:3b", "model": "idle:3b", "size_gb": 2.0,
             "parameter_size": "3B", "family": "llama", "quantization": "Q4",
             "modified_at": "2026-08-13T09:00:00Z"},
        ]

    def list_running(self):
        return [{"name": "brandnew:7b", "model": "brandnew:7b",
                 "size_vram": 5000000000, "expires_at": ""}]

    def detect_hardware(self):
        return {"gpu": None, "vram_gb": 0, "ram_gb": 32, "platform": "test"}

    def recommend_models(self, hw=None):
        return []


@pytest.fixture
def fake_ollama(monkeypatch):
    import agent_friday.routing.ollama_manager as om
    mgr = _FakeOllamaManager()
    monkeypatch.setattr(om, "get_manager", lambda *a, **k: mgr)
    return mgr


# ── Live catalog merge + running flag ────────────────────────────────────────

def test_catalog_shows_freshly_pulled_model(client, fake_ollama):
    data = client.get("/api/models").get_json()
    entry = next(m for m in data["models"] if m["id"] == "brandnew:7b")
    assert entry["provider"] == "ollama-local"
    assert entry["available"] is True
    assert entry["local"] is True
    # Curated → it lands in the orchestrator picker too, no restart needed.
    orch_ids = {e["id"] for e in data["roles"]["orchestrator"]}
    assert "brandnew:7b" in orch_ids
    # Descriptor statics are replaced by daemon truth.
    ollama_ids = {m["id"] for m in data["models"]
                  if m["provider"] == "ollama-local"}
    assert ollama_ids == {"brandnew:7b", "idle:3b"}


def test_catalog_reports_running_state_from_api_ps(client, fake_ollama):
    data = client.get("/api/models").get_json()
    by_id = {m["id"]: m for m in data["models"]
             if m["provider"] == "ollama-local"}
    assert by_id["brandnew:7b"]["running"] is True
    assert by_id["idle:3b"]["running"] is False


def test_cloud_entries_carry_no_running_flag(client, fake_ollama):
    data = client.get("/api/models").get_json()
    cloud = next(m for m in data["models"] if m["provider"] == "anthropic")
    assert "running" not in cloud


# ── Manager /api/ps plumbing ─────────────────────────────────────────────────

def test_manager_list_running_parses_ps(monkeypatch):
    from agent_friday.routing.ollama_manager import OllamaManager
    mgr = OllamaManager("http://localhost:11434")
    monkeypatch.setattr(mgr, "_get", lambda path, timeout=5: {
        "models": [{"name": "a:1", "model": "a:1", "size_vram": 123,
                    "expires_at": "soon"}]})
    out = mgr.list_running()
    assert out == [{"name": "a:1", "model": "a:1", "size_vram": 123,
                    "expires_at": "soon"}]
    # Short-TTL cache: an immediate second call must not re-fetch.
    def _boom(*a, **k):
        raise AssertionError("re-fetched inside the TTL window")
    monkeypatch.setattr(mgr, "_get", _boom)
    assert mgr.list_running()[0]["name"] == "a:1"


def test_manager_list_running_graceful_when_down(monkeypatch):
    from agent_friday.routing.ollama_manager import OllamaManager
    mgr = OllamaManager("http://localhost:11434")

    def _down(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(mgr, "_get", _down)
    assert mgr.list_running() == []


def test_installed_models_ttl_is_short_enough_for_mid_session_pulls():
    """A model pulled mid-session must show up within ~5s (spec A1.3) —
    /api/ollama/models and the catalog both read through this cache."""
    from agent_friday.routing.ollama_manager import OllamaManager
    mgr = OllamaManager("http://localhost:11434")
    assert mgr._models_ttl <= 5
    assert mgr._running_ttl <= 5


# ── Pull preflight (disk space, no network) ──────────────────────────────────

_Usage = namedtuple("usage", "total used free")


def test_preflight_reports_free_disk(client, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: _Usage(500 * 2**30, 100 * 2**30, 400 * 2**30))
    resp = client.get("/api/ollama/pull/preflight?name=qwen3:8b")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "qwen3:8b"
    assert data["free_bytes"] == 400 * 2**30
    assert data["total_bytes"] == 500 * 2**30
    assert data["free_gb"] == 400.0
    assert data["warning"] is None


def test_preflight_warns_under_15gb(client, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: _Usage(500 * 2**30, 495 * 2**30, 5 * 2**30))
    data = client.get("/api/ollama/pull/preflight?name=qwen3:32b").get_json()
    assert data["warning"] is not None
    assert "Low disk space" in data["warning"]


def test_preflight_requires_name(client):
    assert client.get("/api/ollama/pull/preflight").status_code == 400

"""POST /api/models/refresh + hosted-catalog surfacing in GET /api/models
(spec A2). All offline: fetchers/key-resolution monkeypatched, discovery
cache redirected to tmp_path.

SPEC ACCEPTANCE pinned here: "Opus 5 and Fable 5 appear via live Anthropic
fetch" — a seeded discovery cache containing claude-opus-5 + claude-fable-5
must surface both in the catalog (and the curated orchestrator picker),
replacing the shipped statics.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.services.hosted_catalog as hc
import agent_friday.services.model_discovery as md


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Isolated discovery-cache dir so seeded caches never leak across tests."""
    monkeypatch.setattr(md, "CACHE_DIR", tmp_path)
    return tmp_path


def _anthropic_cache_models():
    base = {"context_window": None, "max_output": None,
            "modalities": ["text", "vision", "tools"], "supports_tools": True,
            "price_in": None, "price_out": None, "free": False,
            "source": "discovery"}
    return [
        {"id": "claude-opus-5", "label": "Claude Opus 5", **base},
        {"id": "claude-fable-5", "label": "Claude Fable 5", **base},
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", **base},
    ]


# ── POST /api/models/refresh ─────────────────────────────────────────────────

def test_refresh_route_single_provider(client, cache_dir, monkeypatch):
    monkeypatch.setattr(hc, "_resolve_api_key", lambda name: "test-key")
    monkeypatch.setattr(
        hc, "fetch_anthropic_models",
        lambda key, **kw: [{"id": "claude-opus-5",
                            "display_name": "Claude Opus 5",
                            "created_at": "2026-05-01T00:00:00Z"}])
    resp = client.post("/api/models/refresh", json={"provider": "anthropic"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    res = data["results"]["anthropic"]
    assert res["status"] == "refreshed"
    assert res["count"] == 1
    assert res["fetched_at"]
    # Written into the shared discovery cache the catalog reads.
    assert [m["id"] for m in md.cached_models("anthropic")[0]] == ["claude-opus-5"]


def test_refresh_route_all_providers(client, cache_dir, monkeypatch):
    monkeypatch.setattr(hc, "_resolve_api_key", lambda name: "test-key")
    monkeypatch.setattr(hc, "fetch_anthropic_models",
                        lambda key, **kw: [{"id": "claude-opus-5",
                                            "display_name": "Opus 5"}])
    monkeypatch.setattr(hc, "fetch_openrouter_models",
                        lambda key=None, **kw: [{"id": "a/b", "name": "AB",
                                                 "pricing": {}}])
    data = client.post("/api/models/refresh", json={}).get_json()
    assert set(data["results"]) == set(hc.HOSTED_PROVIDERS)
    for res in data["results"].values():
        assert res["status"] == "refreshed"


def test_refresh_route_no_key_path(client, cache_dir, monkeypatch):
    monkeypatch.setattr(hc, "_resolve_api_key", lambda name: None)
    data = client.post("/api/models/refresh",
                       json={"provider": "anthropic"}).get_json()
    res = data["results"]["anthropic"]
    assert res["status"] == "no_key"
    assert res["count"] == 0
    assert md.read_cache("anthropic") is None  # never writes on no_key


def test_refresh_route_rejects_unknown_provider(client, cache_dir):
    resp = client.post("/api/models/refresh", json={"provider": "skynet"})
    assert resp.status_code == 400


# ── GET /api/models catalog_meta + cache preference ──────────────────────────

def test_models_route_includes_catalog_meta(client, cache_dir):
    data = client.get("/api/models").get_json()
    assert "catalog_meta" in data
    assert "anthropic" in data["catalog_meta"]
    for meta in data["catalog_meta"].values():
        assert set(meta) == {"fetched_at", "stale"}
    # Nothing fetched in this isolated cache → honestly stale.
    assert data["catalog_meta"]["anthropic"]["stale"] is True


def test_seeded_anthropic_cache_surfaces_opus5_and_fable5(client, cache_dir):
    """SPEC ACCEPTANCE: Opus 5 and Fable 5 appear via live Anthropic fetch."""
    md.write_cache("anthropic", _anthropic_cache_models())
    data = client.get("/api/models").get_json()
    ids = {m["id"] for m in data["models"]}
    assert "claude-opus-5" in ids
    assert "claude-fable-5" in ids
    assert "claude-haiku-4-5" in ids
    # They are CURATED picker entries (the live list replaces the statics)...
    orch_ids = {e["id"] for e in data["roles"]["orchestrator"]}
    assert {"claude-opus-5", "claude-fable-5"} <= orch_ids
    # ...and the statics that the live fetch no longer reports are gone.
    anthropic_ids = {m["id"] for m in data["models"]
                     if m["provider"] == "anthropic"}
    assert "claude-opus-4-8" not in anthropic_ids
    # Freshly fetched → not stale, and entries don't carry the stale flag.
    assert data["catalog_meta"]["anthropic"]["stale"] is False
    opus5 = next(m for m in data["models"] if m["id"] == "claude-opus-5")
    assert opus5.get("catalog_stale") is not True
    assert opus5["curated"] is True


def test_no_cache_falls_back_to_statics_flagged_stale(client, cache_dir):
    data = client.get("/api/models").get_json()
    anthropic = [m for m in data["models"] if m["provider"] == "anthropic"]
    assert anthropic, "registry statics must survive a missing cache"
    ids = {m["id"] for m in anthropic}
    assert "claude-sonnet-5" in ids  # shipped static
    assert all(m.get("catalog_stale") is True for m in anthropic), \
        "statics fallback must be flagged catalog_stale (honest degradation)"


# ── Custom-model escape hatch ────────────────────────────────────────────────

def test_custom_models_escape_hatch(client, cache_dir):
    # Use the app's OWN settings path (core.SETTINGS_FILE) — the hermetic-home
    # fixtures can point at a different temp home than the imported app.
    from agent_friday import core
    settings_file = core.SETTINGS_FILE
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    orig = settings_file.read_text(encoding="utf-8") if settings_file.exists() else None
    try:
        data = json.loads(orig) if orig else {}
        data["custom_models"] = [
            {"provider": "anthropic", "id": "claude-experimental-9"}]
        settings_file.write_text(json.dumps(data), encoding="utf-8")
        core._invalidate_settings_cache()
        resp = client.get("/api/models").get_json()
        entry = next(m for m in resp["models"]
                     if m["id"] == "claude-experimental-9")
        assert entry["unverified"] is True
        assert entry["curated"] is False
        assert entry["provider"] == "anthropic"
        # Non-curated → never floods the role pickers.
        for role_entries in resp["roles"].values():
            assert all(e["id"] != "claude-experimental-9" for e in role_entries)
    finally:
        if orig is None:
            settings_file.unlink(missing_ok=True)
        else:
            settings_file.write_text(orig, encoding="utf-8")
        core._invalidate_settings_cache()

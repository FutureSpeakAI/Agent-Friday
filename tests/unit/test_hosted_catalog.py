"""services/hosted_catalog.py — live hosted model catalogs (spec A2).

All offline: the single network seam (_http_get_json) is monkeypatched with
canned wire payloads; cache I/O is redirected to tmp_path so nothing leaks
into the shared hermetic home across tests.
"""
from __future__ import annotations

import json
import time

import pytest

import agent_friday.services.hosted_catalog as hc
import agent_friday.services.model_discovery as md


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Isolated discovery-cache dir (shared by hosted_catalog + model_discovery)."""
    monkeypatch.setattr(md, "CACHE_DIR", tmp_path)
    return tmp_path


# ── Canned wire payloads ─────────────────────────────────────────────────────

ANTHROPIC_PAGE_1 = {
    "data": [
        {"id": "claude-opus-5", "display_name": "Claude Opus 5",
         "created_at": "2026-05-01T00:00:00Z", "type": "model"},
        {"id": "claude-fable-5", "display_name": "Claude Fable 5",
         "created_at": "2026-04-01T00:00:00Z", "type": "model"},
    ],
    "has_more": True,
    "first_id": "claude-opus-5",
    "last_id": "claude-fable-5",
}
ANTHROPIC_PAGE_2 = {
    "data": [
        {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5",
         "created_at": "2025-10-01T00:00:00Z", "type": "model"},
    ],
    "has_more": False,
    "first_id": "claude-haiku-4-5",
    "last_id": "claude-haiku-4-5",
}

OPENROUTER_PAYLOAD = {
    "data": [
        {"id": "anthropic/claude-sonnet-5", "name": "Anthropic: Claude Sonnet 5",
         "context_length": 200000,
         "pricing": {"prompt": "0.000003", "completion": "0.000015"},
         "supported_parameters": ["tools", "temperature"],
         "architecture": {"input_modalities": ["text", "image"],
                          "output_modalities": ["text"]},
         "top_provider": {"max_completion_tokens": 64000}},
        # The "pricing varies" sentinel — must read as UNKNOWN, never a price.
        {"id": "openrouter/fusion", "name": "OpenRouter Fusion",
         "context_length": 128000,
         "pricing": {"prompt": "-1", "completion": "-1"},
         "supported_parameters": [], "architecture": {}, "top_provider": {}},
        {"id": "meta/llama-free:free", "name": "Llama (free)",
         "context_length": 8192,
         "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": [], "architecture": {}, "top_provider": {}},
    ]
}


# ── Fetch parsers ────────────────────────────────────────────────────────────

def test_fetch_anthropic_parses_and_paginates(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, dict(headers or {})))
        return ANTHROPIC_PAGE_2 if "after_id=" in url else ANTHROPIC_PAGE_1

    monkeypatch.setattr(hc, "_http_get_json", fake_get)
    out = hc.fetch_anthropic_models("test-key")
    assert [m["id"] for m in out] == [
        "claude-opus-5", "claude-fable-5", "claude-haiku-4-5"]
    assert out[0]["display_name"] == "Claude Opus 5"
    assert out[0]["created_at"] == "2026-05-01T00:00:00Z"
    # Auth headers: x-api-key + pinned anthropic-version, NOT Bearer auth.
    assert calls[0][1]["x-api-key"] == "test-key"
    assert calls[0][1]["anthropic-version"] == "2023-06-01"
    # Pagination cursor from last_id.
    assert len(calls) == 2
    assert "after_id=claude-fable-5" in calls[1][0]


def test_fetch_openrouter_shape_and_pricing_sentinel(monkeypatch):
    monkeypatch.setattr(hc, "_http_get_json",
                        lambda url, headers=None, timeout=None: OPENROUTER_PAYLOAD)
    out = hc.fetch_openrouter_models()
    by_id = {m["id"]: m for m in out}
    sonnet = by_id["anthropic/claude-sonnet-5"]
    assert sonnet["name"] == "Anthropic: Claude Sonnet 5"
    assert sonnet["context_length"] == 200000
    assert sonnet["pricing"]["prompt"] == pytest.approx(0.000003)
    # -1 sentinel → None (unknown ≠ free)
    fusion = by_id["openrouter/fusion"]
    assert fusion["pricing"]["prompt"] is None
    assert fusion["pricing"]["completion"] is None


def test_fetch_openrouter_sends_bearer_only_with_key(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = dict(headers or {})
        return OPENROUTER_PAYLOAD

    monkeypatch.setattr(hc, "_http_get_json", fake_get)
    hc.fetch_openrouter_models()
    assert "Authorization" not in seen["headers"]
    hc.fetch_openrouter_models("or-key")
    assert seen["headers"]["Authorization"] == "Bearer or-key"


def test_normalize_openrouter_prices_per_million_and_flags():
    normalized = hc._normalize_openrouter([
        {"id": "anthropic/claude-sonnet-5", "name": "Sonnet 5",
         "context_length": 200000,
         "pricing": {"prompt": 0.000003, "completion": 0.000015},
         "supported_parameters": ["tools"],
         "architecture": {"input_modalities": ["text", "image"]},
         "top_provider": {"max_completion_tokens": 64000}},
        {"id": "openrouter/fusion", "name": "Fusion",
         "pricing": {"prompt": None, "completion": None}},
        {"id": "meta/llama-free:free", "name": "Free",
         "pricing": {"prompt": 0.0, "completion": 0.0}},
    ])
    by_id = {m["id"]: m for m in normalized}
    sonnet = by_id["anthropic/claude-sonnet-5"]
    assert sonnet["price_in"] == pytest.approx(3.0)     # per-1M convention
    assert sonnet["price_out"] == pytest.approx(15.0)
    assert sonnet["supports_tools"] is True
    assert "vision" in sonnet["modalities"]
    assert by_id["openrouter/fusion"]["price_in"] is None
    assert by_id["openrouter/fusion"]["free"] is False   # unknown ≠ free
    assert by_id["meta/llama-free:free"]["free"] is True


# ── refresh / cache / age ────────────────────────────────────────────────────

def test_refresh_writes_shared_cache_and_reports_age(cache_dir, monkeypatch):
    monkeypatch.setattr(hc, "_resolve_api_key", lambda name: "test-key")
    monkeypatch.setattr(
        hc, "fetch_anthropic_models",
        lambda key, **kw: [{"id": "claude-opus-5",
                            "display_name": "Claude Opus 5",
                            "created_at": "2026-05-01T00:00:00Z"}])
    res = hc.refresh("anthropic")
    assert res["status"] == "refreshed"
    assert res["count"] == 1
    assert res["fetched_at"]
    # Lands in the SAME store the catalog builder reads.
    models, stale = md.cached_models("anthropic")
    assert models[0]["id"] == "claude-opus-5"
    assert models[0]["label"] == "Claude Opus 5"
    assert stale is False
    age = hc.cache_age("anthropic")
    assert age is not None and 0 <= age < 60


def test_refresh_anthropic_without_key_is_no_key_not_exception(cache_dir, monkeypatch):
    monkeypatch.setattr(hc, "_resolve_api_key", lambda name: None)
    res = hc.refresh("anthropic")
    assert res["status"] == "no_key"
    assert res["count"] == 0
    assert md.read_cache("anthropic") is None  # nothing written


def test_refresh_failure_keeps_previous_cache(cache_dir, monkeypatch):
    md.write_cache("anthropic", [{"id": "claude-old", "label": "Old"}])

    def boom(key, **kw):
        raise RuntimeError("wire down")

    monkeypatch.setattr(hc, "_resolve_api_key", lambda name: "test-key")
    monkeypatch.setattr(hc, "fetch_anthropic_models", boom)
    res = hc.refresh("anthropic")
    assert res["status"] == "error"
    assert "RuntimeError" in res["error"]
    # Stale-while-revalidate: the old cache survives.
    assert [m["id"] for m in md.cached_models("anthropic")[0]] == ["claude-old"]


def test_refresh_unknown_provider(cache_dir):
    res = hc.refresh("nonsense")
    assert res["status"] == "error"


def test_refresh_all_covers_hosted_providers(cache_dir, monkeypatch):
    monkeypatch.setattr(hc, "_resolve_api_key", lambda name: "test-key")
    monkeypatch.setattr(hc, "fetch_anthropic_models",
                        lambda key, **kw: [{"id": "claude-opus-5",
                                            "display_name": "Opus 5"}])
    monkeypatch.setattr(hc, "fetch_openrouter_models",
                        lambda key=None, **kw: [{"id": "a/b", "name": "AB",
                                                 "pricing": {}}])
    results = hc.refresh_all()
    assert set(results) == set(hc.HOSTED_PROVIDERS)
    assert all(r["status"] == "refreshed" for r in results.values())


def test_cache_age_none_without_cache(cache_dir):
    assert hc.cache_age("anthropic") is None


# ── staleness / catalog_meta ─────────────────────────────────────────────────

def _backdate(provider, seconds):
    path = md._cache_path(provider)
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["fetched_at"] = time.time() - seconds
    path.write_text(json.dumps(blob), encoding="utf-8")


def test_catalog_meta_stale_after_24h(cache_dir):
    md.write_cache("anthropic", [{"id": "claude-opus-5"}])
    meta = hc.catalog_meta()
    assert meta["anthropic"]["stale"] is False
    assert meta["anthropic"]["fetched_at"]

    _backdate("anthropic", 25 * 3600)
    meta = hc.catalog_meta()
    assert meta["anthropic"]["stale"] is True
    assert hc.cache_age("anthropic") > 24 * 3600


def test_catalog_meta_never_fetched_reads_stale(cache_dir):
    meta = hc.catalog_meta()
    assert meta["anthropic"] == {"fetched_at": None, "stale": True}
    assert "openrouter" in meta
    assert meta["openrouter"]["stale"] is True

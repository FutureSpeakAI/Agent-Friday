"""API tests for the provider management surface (model-agnostic provider
layer P1): enriched GET /api/providers, POST validation + secret rejection,
PATCH, DELETE revert-to-builtin, /test probe, models/refresh, and
GET /api/models/search. All network seams are mocked.
"""
from __future__ import annotations

import pytest


# ── GET /api/providers (enriched) ────────────────────────────────────────────

def test_providers_list_enriched(client):
    data = client.get("/api/providers").get_json()
    provs = {p["name"]: p for p in data["providers"]}
    assert "openrouter" in provs, "OpenRouter must ship as a built-in"
    orr = provs["openrouter"]
    assert orr["origin"] == "builtin"
    assert "model_count" in orr
    assert "spend_today_usd" in orr
    assert "health" in orr
    # No secret material anywhere in the payload: descriptors may name the env
    # VAR (e.g. "OPENROUTER_API_KEY") but never carry key VALUES.
    for p in data["providers"]:
        assert "api_key" not in p, p["name"]
        assert "api_key" not in (p.get("auth") or {}), p["name"]


def test_new_builtin_templates_present(client):
    t = client.get("/api/providers/templates").get_json()["templates"]
    for name in ("openrouter", "huggingface", "groq", "together", "fireworks",
                 "mistral", "deepseek", "xai", "perplexity", "cohere", "custom"):
        assert name in t, name


# ── POST /api/providers validation ───────────────────────────────────────────

def test_add_provider_rejects_raw_api_key(client):
    res = client.post("/api/providers", json={
        "name": "leaky", "type": "openai-compatible",
        "base_url": "https://api.x.test/v1",
        "api_key": "sk-oops",
    })
    assert res.status_code == 400
    assert "credential" in res.get_json()["error"].lower() or \
        "api key" in res.get_json()["error"].lower()


def test_add_provider_rejects_unknown_adapter(client):
    res = client.post("/api/providers", json={
        "name": "weird", "type": "quantum", "base_url": "https://x.test/v1"})
    assert res.status_code == 400


def test_add_provider_normalizes_to_v2(client):
    res = client.post("/api/providers", json={
        "name": "v1-style", "type": "openai-compatible",
        "base_url": "http://localhost:9099/v1", "auth": {"type": "none"},
        "models": ["m"], "enabled": True})
    assert res.status_code == 200
    provs = {p["name"]: p for p in
             client.get("/api/providers").get_json()["providers"]}
    got = provs["v1-style"]
    assert got["schema_version"] == 2
    assert got["adapter"] == "openai-compatible"
    assert got["classification"] == "cloud"  # default cloud even on localhost
    assert got["origin"] in ("ui", "file")
    client.delete("/api/providers/v1-style")


def test_validate_endpoint_dry_run(client):
    res = client.post("/api/providers/validate", json={
        "name": "BAD NAME", "type": "openai-compatible",
        "base_url": "https://x.test/v1"})
    body = res.get_json()
    assert res.status_code == 200
    assert body["ok"] is False and body["errors"]
    # Nothing was persisted.
    names = {p["name"] for p in
             client.get("/api/providers").get_json()["providers"]}
    assert "BAD NAME" not in names


# ── PATCH /api/providers/<name> ──────────────────────────────────────────────

def test_patch_enables_template_provider(client):
    res = client.patch("/api/providers/groq", json={"enabled": True})
    assert res.status_code == 200
    provs = {p["name"]: p for p in
             client.get("/api/providers").get_json()["providers"]}
    assert provs["groq"]["enabled"] is True
    # Revert to the shipped default (delete removes the override file).
    res = client.delete("/api/providers/groq")
    assert res.status_code == 200
    assert res.get_json().get("reverted_to_builtin") is True
    provs = {p["name"]: p for p in
             client.get("/api/providers").get_json()["providers"]}
    assert provs["groq"]["enabled"] is False
    assert provs["groq"]["origin"] == "builtin"


def test_patch_unknown_provider_404(client):
    assert client.patch("/api/providers/nope-x", json={"enabled": True}).status_code == 404


# ── DELETE semantics ─────────────────────────────────────────────────────────

def test_delete_user_added_provider_gone(client):
    client.post("/api/providers", json={
        "name": "byebye", "type": "openai-compatible",
        "base_url": "http://localhost:9", "auth": {"type": "none"}})
    assert client.delete("/api/providers/byebye").status_code == 200
    names = {p["name"] for p in
             client.get("/api/providers").get_json()["providers"]}
    assert "byebye" not in names
    assert client.delete("/api/providers/byebye").status_code == 404


# ── POST /api/providers/<name>/test ──────────────────────────────────────────

def test_provider_test_openai_compatible(client, monkeypatch):
    import requests

    class _Resp:
        status_code = 200
        def json(self):
            return {"data": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-real")
    res = client.post("/api/providers/openrouter/test", json={})
    body = res.get_json()
    assert res.status_code == 200
    assert body["status"] == "ok"
    assert body["models_seen"] == 3
    assert body["latency_ms"] is not None
    assert body["auth"] == "valid"


def test_provider_test_unauthorized(client, monkeypatch):
    import requests

    class _Resp:
        status_code = 401
        def json(self):
            return {"error": "bad key"}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setenv("OPENROUTER_API_KEY", "bad-key")
    body = client.post("/api/providers/openrouter/test", json={}).get_json()
    assert body["status"] == "unauthorized"
    assert body["auth"] == "invalid"


def test_provider_test_unknown_404(client):
    assert client.post("/api/providers/never-heard/test", json={}).status_code == 404


def test_provider_test_never_echoes_key(client, monkeypatch):
    import requests, json as _json

    class _Resp:
        status_code = 200
        def json(self):
            return {"data": []}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-super-secret-value-xyz")  # pragma: allowlist secret
    res = client.post("/api/providers/openrouter/test", json={})
    assert "sk-super-secret-value-xyz" not in res.get_data(as_text=True)  # pragma: allowlist secret


# ── POST /api/providers/<name>/models/refresh ────────────────────────────────

def test_models_refresh_populates_catalog(client, monkeypatch):
    import requests

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"data": [{
                "id": "meta-llama/llama-4-maverick:free",
                "name": "Llama 4 Maverick (free)",
                "context_length": 256000,
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": ["tools"],
            }]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    res = client.post("/api/providers/openrouter/models/refresh")
    body = res.get_json()
    assert res.status_code == 200 and body["ok"] and body["count"] == 1

    # The discovered model appears in the catalog with metadata…
    cat = client.get("/api/models").get_json()
    entry = next((m for m in cat["models"]
                  if m["id"] == "meta-llama/llama-4-maverick:free"), None)
    assert entry is not None
    assert entry["provider"] == "openrouter"
    assert entry.get("free") is True
    assert entry.get("source") == "discovery"
    assert entry.get("curated") is False

    # …but NOT in the curated role pickers — the discovery long tail is
    # Model-Browser/search material, never quick-switcher noise.
    for role, entries in cat["roles"].items():
        assert all(m["id"] != "meta-llama/llama-4-maverick:free"
                   for m in entries), f"discovery model leaked into {role}"

    # …and in the search endpoint.
    hits = client.get("/api/models/search?q=maverick").get_json()
    assert hits["count"] >= 1
    assert any(m["id"] == "meta-llama/llama-4-maverick:free"
               for m in hits["models"])

    # Free filter works.
    hits = client.get("/api/models/search?q=maverick&free=1").get_json()
    assert all(m["free"] for m in hits["models"])

    # Clean up the cache so other tests see the pristine state.
    from agent_friday.services.model_discovery import invalidate_cache
    invalidate_cache("openrouter")


def test_models_refresh_static_provider_400(client):
    res = client.post("/api/providers/perplexity/models/refresh")
    assert res.status_code == 400


def test_models_refresh_unknown_404(client):
    assert client.post("/api/providers/never-heard/models/refresh").status_code == 404


# ── GET /api/models/search over statics ──────────────────────────────────────

def test_search_finds_static_models(client):
    hits = client.get("/api/models/search?q=claude").get_json()
    assert any(m["provider"] == "anthropic" for m in hits["models"])


def test_search_provider_filter(client):
    hits = client.get("/api/models/search?q=&provider=anthropic").get_json()
    assert hits["models"] and all(m["provider"] == "anthropic"
                                  for m in hits["models"])


def test_search_perplexity_statics_present(client):
    """Perplexity is disabled by default — enable, search, revert."""
    client.patch("/api/providers/perplexity", json={"enabled": True})
    try:
        hits = client.get("/api/models/search?q=sonar").get_json()
        assert any(m["provider"] == "perplexity" for m in hits["models"])
    finally:
        client.delete("/api/providers/perplexity")


def test_search_rows_carry_modalities_and_local(client):
    """The Model Browser filters on capability and shows the on-device
    marker — every row must carry modalities[] and local."""
    hits = client.get("/api/models/search?q=").get_json()
    assert hits["models"]
    for m in hits["models"]:
        assert isinstance(m.get("modalities"), list) and m["modalities"]
        assert isinstance(m.get("local"), bool)


def test_search_modality_filter(client):
    """modality=image returns only image-capable models (Gemini's Nano
    Banana statics carry the tag via model_meta)."""
    hits = client.get("/api/models/search?q=&modality=image").get_json()
    assert hits["models"], "no image-capable models found"
    assert all("image" in m["modalities"] for m in hits["models"])


def test_search_local_filter(client):
    """local=1 keeps only on-device providers, filtered SERVER-side so the
    rows survive sort+limit truncation (Ollama entries are unpriced and
    would fall off the page under a client-side price sort)."""
    hits = client.get("/api/models/search?q=&local=1&sort=price").get_json()
    assert hits["models"], "no local models found"
    assert all(m["local"] for m in hits["models"])
    assert any(m["provider"] == "ollama-local" for m in hits["models"])


def test_search_price_sort_unpriced_last(client):
    """sort=price orders cheapest-first with unpriced entries last
    (unknown ≠ free) — and sorts BEFORE the limit truncates."""
    hits = client.get("/api/models/search?q=&sort=price").get_json()
    models = hits["models"]
    assert models
    seen_unpriced = False
    last_price = None
    for m in models:
        if m.get("price_in") is None:
            seen_unpriced = True
            continue
        assert not seen_unpriced, "priced entry after an unpriced one"
        if last_price is not None:
            assert m["price_in"] >= last_price
        last_price = m["price_in"]


# ── Health endpoint enrichment ───────────────────────────────────────────────

def test_health_endpoint_carries_stats_block(client):
    data = client.get("/api/providers/health").get_json()
    assert data["providers"]
    for p in data["providers"]:
        assert "stats" in p

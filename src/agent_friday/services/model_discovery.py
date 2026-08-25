"""
services/model_discovery.py — live model discovery + on-disk cache (spec §6).

Fetches each provider's model list from its own API (per-parser), normalizes
to ModelInfo dicts, and caches at ~/.friday/cache/models/{provider}.json with a
TTL. The catalog builder reads ONLY the cache (never the network) so discovery
is never on the chat hot path; a background sweep and the explicit
POST /api/providers/<name>/models/refresh endpoint do the fetching.

Failure policy: stale-while-revalidate — a provider with an expired cache and
a failing fetch keeps serving the stale list, flagged `catalog_stale` so the
UI can show a subtle warning instead of an empty picker.

ModelInfo shape (spec §6.3):
    {id, label, context_window, max_output, modalities[], supports_tools,
     price_in, price_out, free, source: "discovery"}
    price_* are USD per 1M tokens; None = unknown (unknown ≠ free).
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

_log = logging.getLogger("friday.model_discovery")

CACHE_DIR = Path.home() / ".friday" / "cache" / "models"
CACHE_SCHEMA = 1

#: Boot-window retry for connector-backed providers that are not up yet.
#: 10 attempts, 30 s apart — five minutes of grace, which comfortably covers
#: MCP connector startup (and its OAuth refresh) without ever blocking boot,
#: since this runs on the discovery daemon thread.
_BOOT_RETRY_ATTEMPTS = 10
_BOOT_RETRY_INTERVAL_S = 30.0
DEFAULT_TTL_S = 86400

_FETCH_LOCK = threading.Lock()
_BG_STARTED = False


def _cache_path(provider_name: str) -> Path:
    safe = "".join(c for c in str(provider_name)
                   if c.isalnum() or c in "-_") or "provider"
    return CACHE_DIR / f"{safe}.json"


# ── Parsers ──────────────────────────────────────────────────────────────────

def _f(val):
    """float() that returns None for absent/invalid input."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _price(val):
    """A wire price, or None. Negative values are sentinels ("pricing
    varies" — OpenRouter reports -1 on its dynamic-routing meta-models like
    openrouter/fusion) and must read as unknown, never as a real price:
    they would otherwise win every cheapest-first sort and poison cost
    metering. Unknown ≠ free."""
    p = _f(val)
    return None if p is None or p < 0 else p


def parse_openrouter(payload: dict) -> list:
    """OpenRouter GET /api/v1/models — the richest discovery source: pricing
    (USD per TOKEN, converted to per-1M here), context, modalities, and
    `supported_parameters` (the authoritative tool-support signal)."""
    out = []
    for item in (payload or {}).get("data") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mid = str(item["id"])
        pricing = item.get("pricing") or {}
        price_in = _price(pricing.get("prompt"))
        price_out = _price(pricing.get("completion"))
        if price_in is not None:
            price_in = round(price_in * 1_000_000, 6)
        if price_out is not None:
            price_out = round(price_out * 1_000_000, 6)
        arch = item.get("architecture") or {}
        modalities = ["text"]
        if "image" in (arch.get("input_modalities") or []):
            modalities.append("vision")
        # Output modalities drive the creative pickers and the Model Browser's
        # image/video capability filter — image INPUT is vision, image OUTPUT
        # is generation.
        for mod in ("image", "video"):
            if mod in (arch.get("output_modalities") or []):
                modalities.append(mod)
        supported = item.get("supported_parameters") or []
        supports_tools = "tools" in supported
        if supports_tools:
            modalities.append("tools")
        top = item.get("top_provider") or {}
        free = mid.endswith(":free") or (price_in == 0 and price_out == 0
                                         and price_in is not None)
        out.append({
            "id": mid,
            "label": item.get("name") or mid,
            "context_window": item.get("context_length"),
            "max_output": top.get("max_completion_tokens"),
            "modalities": modalities,
            "supports_tools": supports_tools,
            "price_in": price_in,
            "price_out": price_out,
            "free": bool(free),
            "source": "discovery",
        })
    return out


def parse_openai(payload: dict) -> list:
    """Generic OpenAI-compatible GET /models — ids only (Groq, Mistral,
    DeepSeek, xAI, Cohere-compat, vLLM, LM Studio…). Together and some others
    add pricing/context extras, captured when present."""
    out = []
    if isinstance(payload, list):  # some servers return a bare list
        data = payload
    else:
        data = (payload or {}).get("data")
    for item in data or []:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mid = str(item["id"])
        pricing = item.get("pricing") or {}
        out.append({
            "id": mid,
            "label": item.get("display_name") or item.get("name") or mid,
            "context_window": item.get("context_length") or item.get("context_window"),
            "max_output": None,
            "modalities": ["text"],
            "supports_tools": None,  # unknown — generic /models has no signal
            "price_in": _price(pricing.get("input")),
            "price_out": _price(pricing.get("output")),
            "free": False,
            "source": "discovery",
        })
    return out


def parse_hf_router(payload: dict) -> list:
    """HuggingFace Inference Providers router GET /v1/models — chat-capable Hub
    ids, each optionally backed by several providers."""
    out = []
    for item in (payload or {}).get("data") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mid = str(item["id"])
        providers = item.get("providers") or []
        supports_tools = None
        for p in providers:
            if isinstance(p, dict) and p.get("supports_tools"):
                supports_tools = True
                break
        out.append({
            "id": mid,
            "label": mid.split("/")[-1],
            "context_window": None,
            "max_output": None,
            "modalities": ["text"],
            "supports_tools": supports_tools,
            "price_in": None,
            "price_out": None,
            "free": False,
            "source": "discovery",
        })
    return out


PARSERS = {
    "openrouter": parse_openrouter,
    "openai": parse_openai,
    "hf-router": parse_hf_router,
}


# ── Cache I/O ────────────────────────────────────────────────────────────────

def read_cache(provider_name: str) -> dict | None:
    """The raw cache blob for a provider, or None. Pure disk read."""
    path = _cache_path(provider_name)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
        if isinstance(blob, dict) and isinstance(blob.get("models"), list):
            return blob
    except Exception as e:
        _log.warning("model cache for %s unreadable: %s", provider_name, e)
    return None


def write_cache(provider_name: str, models: list, ttl_s: int = DEFAULT_TTL_S) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(provider_name)
    blob = {"schema": CACHE_SCHEMA, "provider": provider_name,
            "fetched_at": time.time(), "ttl_s": int(ttl_s), "models": models}
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=1)
    tmp.replace(path)
    return path


def cache_is_stale(blob: dict | None) -> bool:
    if not blob:
        return True
    ttl = blob.get("ttl_s") or DEFAULT_TTL_S
    return (time.time() - (blob.get("fetched_at") or 0)) > ttl


def cached_models(provider_name: str) -> tuple:
    """(models list, stale bool) for a provider — disk only, never network.

    Sanitizes negative wire prices (the "pricing varies" sentinel) to None at
    this single read choke point: caches written by pre-fix versions carry
    -1000000.0 for up to a TTL after upgrade, and every consumer (catalog,
    search sort/filter, pricing) must read those as unknown, never as the
    cheapest model on the list.
    """
    blob = read_cache(provider_name)
    if not blob:
        return [], True
    models = []
    for m in blob.get("models") or []:
        if isinstance(m, dict):
            for k in ("price_in", "price_out"):
                v = m.get(k)
                if isinstance(v, (int, float)) and v < 0:
                    m = {**m, k: None}
            models.append(m)
    return models, cache_is_stale(blob)


def cached_model_ids(provider_name: str) -> list:
    return [m.get("id") for m in cached_models(provider_name)[0] if m.get("id")]


def invalidate_cache(provider_name: str) -> bool:
    path = _cache_path(provider_name)
    if path.exists():
        try:
            path.unlink()
            return True
        except Exception:
            return False
    return False


# ── Fetch ────────────────────────────────────────────────────────────────────

def refresh_models(provider, timeout: float = 20.0) -> dict:
    """Fetch a provider's live model list and write the cache.

    `provider` is a descriptor dict or a registry name. Returns
    {ok, provider, count, error?}. On failure the previous cache (if any) is
    left in place — stale-while-revalidate.
    """
    prov = provider
    if not isinstance(prov, dict):
        try:
            from agent_friday.services.provider_registry import get_provider_registry
            prov = get_provider_registry().get_provider(str(provider))
        except Exception:
            prov = None
    if not prov:
        return {"ok": False, "provider": str(provider), "count": 0,
                "error": "no such provider"}
    name = prov.get("name", "")
    disc = prov.get("discovery") or {}
    if disc.get("mode") != "api":
        return {"ok": False, "provider": name, "count": 0,
                "error": "provider has no API discovery (static model list)"}

    from agent_friday.routing.provider_descriptors import (
        provider_api_key, auth_headers)
    api_key = provider_api_key(prov)  # pragma: allowlist secret
    feats = prov.get("features") or {}
    auth = prov.get("auth") or {}
    needs_key = auth.get("type", "env_var") == "env_var" \
        and not feats.get("keyless_discovery")
    if needs_key and not api_key:
        return {"ok": False, "provider": name, "count": 0,
                "error": "no API key configured — add one in Settings → Providers"}

    base = (prov.get("base_url") or "").rstrip("/")
    endpoint = disc.get("endpoint") or "/models"
    url = base + endpoint
    parser = PARSERS.get(disc.get("parser") or "openai", parse_openai)

    import requests
    t0 = time.time()
    try:
        r = requests.get(url, headers=auth_headers(prov, api_key), timeout=timeout)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        _log.warning("model discovery failed for %s (%s): %s", name, url, e)
        return {"ok": False, "provider": name, "count": 0,
                "error": f"{type(e).__name__}: {e}"[:300]}

    try:
        models = parser(payload)
    except Exception as e:
        return {"ok": False, "provider": name, "count": 0,
                "error": f"parser error: {e}"[:300]}

    max_models = int(disc.get("max_models") or 0)
    if max_models > 0:
        models = models[:max_models]
    write_cache(name, models, ttl_s=int(disc.get("ttl_s") or DEFAULT_TTL_S))
    _log.info("discovered %d models for %s in %dms", len(models), name,
              int((time.time() - t0) * 1000))
    return {"ok": True, "provider": name, "count": len(models),
            "latency_ms": int((time.time() - t0) * 1000)}


def refresh_all_stale(force: bool = False) -> list:
    """Refresh every enabled api-discovery provider whose cache is stale.
    Serialized behind a lock so concurrent sweeps don't double-fetch."""
    results = []
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        providers = get_provider_registry().get_enabled_providers()
    except Exception:
        return results
    with _FETCH_LOCK:
        for prov in providers:
            mode = (prov.get("discovery") or {}).get("mode")
            if mode not in ("api", "mcp"):
                continue
            name = prov.get("name", "")
            if not force and not cache_is_stale(read_cache(name)):
                continue
            if mode == "mcp":
                results.append(_refresh_via_module(prov))
            else:
                results.append(refresh_models(prov))
    return results


def _refresh_via_module(prov: dict) -> dict:
    """Refresh a provider that enumerates over a connector rather than an
    HTTP /models endpoint, by delegating to the module its descriptor names.

    Keeps the sweep declarative: a provider says how it enumerates and the
    sweep obeys, rather than the sweep carrying a list of provider names.
    """
    name = prov.get("name", "")
    module = (prov.get("discovery") or {}).get("module") or ""
    if not module or not re.fullmatch(r"[a-z0-9_]+", module):
        return {"status": "error", "provider": name,
                "error": f"descriptor names no usable discovery module ({module!r})"}
    try:
        mod = importlib.import_module(f"agent_friday.services.{module}")
        return mod.refresh()
    except Exception as e:
        # Never let one provider's connector take the whole sweep down.
        _log.warning("mcp discovery refresh failed for %s: %s", name, e)
        return {"status": "error", "provider": name,
                "error": f"{type(e).__name__}: {e}"[:200]}


def next_sweep_delay(results, attempts: int) -> float:
    """Seconds to wait before the next background sweep.

    Connector-backed providers (Higgsfield over MCP) are usually NOT ready
    when the first sweep runs ~3 s after boot: MCP servers start
    asynchronously, so the sweep finds no manager and reports `unavailable`.
    Waiting the full hour would leave the picker empty for the entire first
    session after a restart — a catalogue that looks broken when it is merely
    early.

    So: while something is still coming up, retry on a short cadence for a
    bounded window; otherwise settle into the hourly steady state. `error` is
    deliberately NOT retried fast — that is a real failure, not a slow start,
    and hammering it would neither fix it nor tell anyone.
    """
    waiting = any(isinstance(r, dict) and r.get("status") == "unavailable"
                  for r in (results or []))
    if waiting and attempts < _BOOT_RETRY_ATTEMPTS:
        return _BOOT_RETRY_INTERVAL_S
    return 3600.0


def ensure_background_refresh(initial_delay_s: float = 3.0) -> bool:
    """Kick a one-shot daemon thread that refreshes stale discovery caches
    shortly after boot, then hourly. No-op under tests (FRIDAY_TESTING) and
    idempotent across calls."""
    global _BG_STARTED
    if os.environ.get("FRIDAY_TESTING"):
        return False
    if _BG_STARTED:
        return False
    _BG_STARTED = True

    def _loop():
        time.sleep(initial_delay_s)
        attempts = 0
        while True:
            results = []
            try:
                results = refresh_all_stale() or []
            except Exception as e:
                _log.warning("background model discovery sweep failed: %s", e)
            attempts += 1
            delay = next_sweep_delay(results, attempts)
            if delay >= 3600:
                attempts = _BOOT_RETRY_ATTEMPTS   # steady state reached
            time.sleep(delay)

    threading.Thread(target=_loop, name="friday-model-discovery",
                     daemon=True).start()
    return True

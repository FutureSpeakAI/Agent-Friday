"""
services/hosted_catalog.py — live hosted-provider model catalogs (spec A2).

Fetches the model list straight from the hosted providers' own APIs —
Anthropic GET /v1/models (native API, paginated) and OpenRouter
GET /api/v1/models — and writes the results into the SAME on-disk discovery
cache that services/model_discovery.cached_models() (and therefore the model
catalog / picker) already reads. This kills the hardcoded hosted lists: a new
Claude id appears in the picker after one refresh, no code change.

Design notes:
  * The single network seam is _http_get_json() — plain requests, NOT the
    anthropic SDK client (the test suite sentinels the SDK client, and this
    must stay monkeypatchable at one choke point).
  * API keys resolve exactly the way the rest of the codebase does:
    provider_descriptors.provider_api_key() (env chain → encrypted credential
    store). Missing key → {"status": "no_key"}, never an exception.
  * OpenRouter's -1 "pricing varies" sentinel reads as None (unknown ≠ free),
    matching model_discovery's convention.
  * Failure policy is stale-while-revalidate: a failed or empty fetch never
    clobbers a previously working cache.

Refreshes happen via POST /api/models/refresh (manual) — the generic hourly
discovery sweep still only covers OpenAI-compatible api-discovery providers.
GET /api/models exposes catalog_meta so the UI can render "catalog stale,
showing cached" honestly instead of pretending the list is live.
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger("friday.hosted_catalog")

ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Providers this module knows how to refresh.
HOSTED_PROVIDERS = ("anthropic", "openrouter")

# A cache older than this renders as "catalog stale, showing cached".
STALE_AFTER_S = 24 * 3600

# Pagination hard stop — Anthropic pages at 100/models; 20 pages is far beyond
# any plausible catalog and bounds a buggy has_more loop.
_MAX_PAGES = 20


def _http_get_json(url: str, headers: dict | None = None,
                   timeout: float = 20.0) -> dict:
    """Plain-HTTPS JSON GET — the single network seam for this module.
    Tests monkeypatch this; nothing here may go through a provider SDK."""
    import requests
    r = requests.get(url, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _price(val):
    """A wire price, or None. Negative values are sentinels (OpenRouter
    reports -1 for "pricing varies") and must read as unknown, never as a
    real price. Unknown ≠ free."""
    try:
        p = float(val)
    except (TypeError, ValueError):
        return None
    return None if p < 0 else p


# ── Fetchers (network) ───────────────────────────────────────────────────────

def fetch_anthropic_models(api_key: str, timeout: float = 20.0) -> list:
    """Anthropic GET /v1/models, following has_more/last_id pagination.

    Returns [{id, display_name, created_at}] in wire order (newest first).
    """
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    out, after_id = [], None
    for _page in range(_MAX_PAGES):
        url = f"{ANTHROPIC_MODELS_URL}?limit=100"
        if after_id:
            url += f"&after_id={after_id}"
        payload = _http_get_json(url, headers=headers, timeout=timeout) or {}
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                out.append({
                    "id": str(item["id"]),
                    "display_name": item.get("display_name") or str(item["id"]),
                    "created_at": item.get("created_at"),
                })
        if not payload.get("has_more") or not payload.get("last_id"):
            break
        after_id = payload["last_id"]
    return out


def fetch_openrouter_models(api_key: str | None = None,
                            timeout: float = 20.0) -> list:
    """OpenRouter GET /api/v1/models (keyless-capable public endpoint).

    Returns [{id, name, context_length, pricing}] plus the extra wire fields
    the cache normalizer needs (supported_parameters, architecture,
    top_provider). `pricing` values are USD per TOKEN with the -1 sentinel
    already sanitized to None.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = _http_get_json(OPENROUTER_MODELS_URL, headers=headers,
                             timeout=timeout) or {}
    out = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        pricing = item.get("pricing") or {}
        out.append({
            "id": str(item["id"]),
            "name": item.get("name") or str(item["id"]),
            "context_length": item.get("context_length"),
            "pricing": {"prompt": _price(pricing.get("prompt")),
                        "completion": _price(pricing.get("completion"))},
            "supported_parameters": item.get("supported_parameters") or [],
            "architecture": item.get("architecture") or {},
            "top_provider": item.get("top_provider") or {},
        })
    return out


# ── Normalizers (fetcher shape → discovery-cache ModelInfo shape) ────────────

def _normalize_anthropic(models: list) -> list:
    out = []
    for m in models or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        out.append({
            "id": str(m["id"]),
            "label": m.get("display_name") or str(m["id"]),
            "context_window": None,   # /v1/models doesn't report these
            "max_output": None,
            "modalities": ["text", "vision", "tools"],
            "supports_tools": True,
            "price_in": None,
            "price_out": None,
            "free": False,
            "source": "discovery",
            "created_at": m.get("created_at"),
        })
    return out


def _normalize_openrouter(models: list) -> list:
    out = []
    for m in models or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        mid = str(m["id"])
        pricing = m.get("pricing") or {}
        price_in = _price(pricing.get("prompt"))
        price_out = _price(pricing.get("completion"))
        # Cache convention is USD per 1M tokens (wire is per token).
        if price_in is not None:
            price_in = round(price_in * 1_000_000, 6)
        if price_out is not None:
            price_out = round(price_out * 1_000_000, 6)
        arch = m.get("architecture") or {}
        modalities = ["text"]
        if "image" in (arch.get("input_modalities") or []):
            modalities.append("vision")
        for mod in ("image", "video"):
            if mod in (arch.get("output_modalities") or []):
                modalities.append(mod)
        supports_tools = "tools" in (m.get("supported_parameters") or [])
        if supports_tools:
            modalities.append("tools")
        free = mid.endswith(":free") or (price_in == 0 and price_out == 0
                                         and price_in is not None)
        out.append({
            "id": mid,
            "label": m.get("name") or mid,
            "context_window": m.get("context_length"),
            "max_output": (m.get("top_provider") or {}).get("max_completion_tokens"),
            "modalities": modalities,
            "supports_tools": supports_tools,
            "price_in": price_in,
            "price_out": price_out,
            "free": bool(free),
            "source": "discovery",
        })
    return out


# ── Key resolution ───────────────────────────────────────────────────────────

def _resolve_api_key(provider_name: str):
    """Resolve a provider key the way the rest of the codebase does:
    provider_descriptors.provider_api_key (env chain incl. aliases → encrypted
    credential store), with a bare-env fallback if the registry itself is
    broken. Never log or echo the value."""
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        from agent_friday.routing.provider_descriptors import provider_api_key
        prov = get_provider_registry().get_provider(provider_name)
        if prov:
            return provider_api_key(prov)  # pragma: allowlist secret
    except Exception:
        pass
    import os
    env_names = {"anthropic": ("ANTHROPIC_API_KEY",),
                 "openrouter": ("OPENROUTER_API_KEY", "OR_API_KEY")}
    for env in env_names.get(provider_name, ()):
        if os.environ.get(env):
            return os.environ[env]  # pragma: allowlist secret
    return None


# ── Refresh / cache API ──────────────────────────────────────────────────────

def refresh(provider: str) -> dict:
    """Fetch one hosted provider's live model list into the discovery cache.

    Returns {status: "refreshed"|"no_key"|"error", provider, count,
    fetched_at?, error?}. Never raises; a failed or empty fetch leaves any
    existing cache untouched (stale-while-revalidate).
    """
    name = str(provider or "").strip().lower()
    if name not in HOSTED_PROVIDERS:
        return {"status": "error", "provider": name, "count": 0,
                "error": f"unsupported provider {name!r} — "
                         f"one of: {', '.join(HOSTED_PROVIDERS)}"}
    api_key = _resolve_api_key(name)  # pragma: allowlist secret
    if name == "anthropic" and not api_key:
        # OpenRouter's /models is public; Anthropic's is not.
        return {"status": "no_key", "provider": name, "count": 0,
                "error": "no ANTHROPIC_API_KEY configured — add one in "
                         "Settings → Providers"}
    try:
        if name == "anthropic":
            normalized = _normalize_anthropic(fetch_anthropic_models(api_key))
        else:
            normalized = _normalize_openrouter(fetch_openrouter_models(api_key))
    except Exception as e:
        _log.warning("hosted catalog refresh failed for %s: %s", name, e)
        return {"status": "error", "provider": name, "count": 0,
                "error": f"{type(e).__name__}: {e}"[:300]}
    if not normalized:
        return {"status": "error", "provider": name, "count": 0,
                "error": "provider returned an empty model list — "
                         "keeping the previous cache"}
    from agent_friday.services.model_discovery import read_cache, write_cache
    write_cache(name, normalized)
    blob = read_cache(name) or {}
    return {"status": "refreshed", "provider": name, "count": len(normalized),
            "fetched_at": blob.get("fetched_at") or time.time()}


def refresh_all() -> dict:
    """Refresh every hosted provider. Returns {provider: refresh-result}."""
    return {name: refresh(name) for name in HOSTED_PROVIDERS}


def cache_age(provider: str):
    """Seconds since the provider's catalog cache was fetched, or None when
    no cache exists (or it carries no timestamp)."""
    from agent_friday.services.model_discovery import read_cache
    blob = read_cache(str(provider or ""))
    fetched_at = (blob or {}).get("fetched_at")
    if not fetched_at:
        return None
    try:
        return max(0.0, time.time() - float(fetched_at))
    except (TypeError, ValueError):
        return None


def catalog_meta() -> dict:
    """{provider: {fetched_at, stale}} for the UI's honesty banner —
    stale=True means older than 24h OR never fetched ("showing cached/built-in
    list"). Covers the hosted providers plus every enabled api-discovery
    provider (OpenRouter et al share the same cache store)."""
    from agent_friday.services.model_discovery import read_cache
    names = set(HOSTED_PROVIDERS)
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        from agent_friday.services.model_catalog import HOSTED_NATIVE_TYPES
        for prov in get_provider_registry().get_enabled_providers():
            if (prov.get("discovery") or {}).get("mode") == "api":
                names.add(prov.get("name", ""))
            # Hosted-native providers (Higgsfield) ship an empty model list and
            # live entirely off this cache, so a stale cache is exactly what
            # the banner exists to disclose. Without this they were absent
            # from the meta and rendered as though freshly fetched.
            elif prov.get("type") in HOSTED_NATIVE_TYPES:
                names.add(prov.get("name", ""))
    except Exception:
        pass
    meta = {}
    now = time.time()
    for name in sorted(n for n in names if n):
        fetched_at = (read_cache(name) or {}).get("fetched_at")
        try:
            age = None if not fetched_at else max(0.0, now - float(fetched_at))
        except (TypeError, ValueError):
            age = None
        meta[name] = {"fetched_at": fetched_at,
                      "stale": age is None or age > STALE_AFTER_S}
    return meta

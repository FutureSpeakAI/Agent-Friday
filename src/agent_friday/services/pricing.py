"""
services/pricing.py — one price lookup for routing, metering, and the UI
(spec §6.4).

price(provider, model) -> {"in_per_1m", "out_per_1m", "source"} | None

Tier order (first hit wins):
  1. discovery cache   — OpenRouter (and Together et al.) report live prices
  2. descriptor        — pricing.static per-1M entries in the provider JSON
  3. legacy static     — cost_meter.PRICING per-1K table (×1000)
  4. None              — unknown ≠ free: callers must treat None as "unpriced",
                         never as $0.

Local providers are always 0/0 (data never leaves, nothing is billed).
"""
from __future__ import annotations


def _f(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _provider_descriptor(provider):
    if isinstance(provider, dict):
        return provider
    if not provider:
        return None
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        return get_provider_registry().get_provider(str(provider))
    except Exception:
        return None


def discovery_price(provider_name: str, model: str):
    """Tier 1: the discovery cache's live price for (provider, model)."""
    try:
        from agent_friday.services.model_discovery import cached_models
        models, _stale = cached_models(provider_name)
    except Exception:
        return None
    for m in models:
        if m.get("id") == model:
            pin, pout = _f(m.get("price_in")), _f(m.get("price_out"))
            # Negative = "pricing varies" sentinel (pre-fix caches may still
            # carry it) — unknown, never a real (or negative!) spend figure.
            pin = None if pin is not None and pin < 0 else pin
            pout = None if pout is not None and pout < 0 else pout
            if pin is None and pout is None:
                return None
            return {"in_per_1m": pin if pin is not None else 0.0,
                    "out_per_1m": pout if pout is not None else 0.0,
                    "source": "discovery"}
    return None


def price(provider, model):
    """Resolve {"in_per_1m", "out_per_1m", "source"} for a (provider, model)
    pair, or None when the price is unknown."""
    if not model:
        return None
    prov = _provider_descriptor(provider)
    pname = (prov or {}).get("name") or (provider if isinstance(provider, str)
                                         else "")

    # Local providers: free by definition.
    if prov is not None:
        try:
            from agent_friday.routing.provider_descriptors import classification_of
            if classification_of(prov) == "local":
                return {"in_per_1m": 0.0, "out_per_1m": 0.0, "source": "local"}
        except Exception:
            pass

    # Tier 1 — discovery cache.
    if pname:
        hit = discovery_price(pname, model)
        if hit:
            return hit

    # Tier 2 — descriptor pricing.static (per-1M in/out; the user's word).
    if prov:
        static = ((prov.get("pricing") or {}).get("static") or {})
        entry = static.get(model)
        if isinstance(entry, dict):
            pin, pout = _f(entry.get("in")), _f(entry.get("out"))
            if pin is not None or pout is not None:
                return {"in_per_1m": pin or 0.0, "out_per_1m": pout or 0.0,
                        "source": "static"}

    # Tier 3 — the per-direction PRICING table in cost_meter (the dataset).
    try:
        from agent_friday.services.cost_meter import PRICING
        entry = PRICING.get(model)
        if entry:
            return {"in_per_1m": float(entry["in"]) * 1000.0,
                    "out_per_1m": float(entry["out"]) * 1000.0,
                    "source": "dataset"}
    except Exception:
        pass

    # Tier 4 — v1 blended cost_per_1k, read as the LAST fallback per the
    # migration contract (spec §13.1: "read as blended fallback until v+2;
    # discovery/dataset pricing takes precedence").
    if prov:
        blended = _f((prov.get("cost_per_1k") or {}).get(model))
        if blended is not None:
            return {"in_per_1m": blended * 1000.0, "out_per_1m": blended * 1000.0,
                    "source": "static-blended"}
    return None


def blended_per_1k(provider, model):
    """A blended per-1K display figure for catalog entries, or None."""
    p = price(provider, model)
    if not p:
        return None
    return round((p["in_per_1m"] + p["out_per_1m"]) / 2.0 / 1000.0, 6)


def cost_usd(provider, model, input_tokens: int, output_tokens: int):
    """Exact cost for a call, or None when the model is unpriced (callers log
    tokens with cost=None rather than pretending $0)."""
    p = price(provider, model)
    if not p:
        return None
    return round((input_tokens / 1_000_000.0) * p["in_per_1m"]
                 + (output_tokens / 1_000_000.0) * p["out_per_1m"], 6)

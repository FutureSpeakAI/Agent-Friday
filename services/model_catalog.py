"""
Agent Friday — Model Catalog

Single source of truth for the model picker. Reads the declarative
ProviderRegistry, enriches each model with presentation + role metadata,
merges in live-detected Ollama models, computes availability from the env keys,
and groups everything by UI role. The UI renders entirely from this (via
GET /api/models) — no model list is hardcoded in the frontend, so adding a
provider/model on the backend (or dropping a provider JSON in
~/.friday/providers/) surfaces it automatically.
"""
from services.provider_registry import (
    get_provider_registry, ALL_ROLES,
    ROLE_ORCHESTRATOR, ROLE_SUBAGENT, ROLE_CREATIVE, ROLE_VOICE,
)

# Importing the router family helper is cheap and dependency-free.
try:
    from model_router import provider_family
except Exception:  # pragma: no cover - router always importable in practice
    def provider_family(_):
        return None


def _humanize(model_id: str) -> dict:
    """Inferred presentation for a model that has no explicit model_meta.

    Keeps the catalog fully extensible: a custom provider's models still render
    with a sensible label/short and a best-guess role, even with zero metadata.
    """
    # Default roles for a model with no explicit model_meta. All families fall
    # back to the agent (text) roles — including Gemini, whose roles are mixed
    # (2.5 Pro = text, 2.5 Flash = voice, Nano Banana / Veo = creative) and so
    # are always declared per-model in model_meta rather than inferred here.
    roles = [ROLE_ORCHESTRATOR, ROLE_SUBAGENT]
    # A readable label. Ollama tags (gemma4:12b) keep their tag so size variants
    # stay distinct; everything else gets a title-cased stem.
    if ":" in model_id:
        stem, tag = model_id.split(":", 1)
        base = stem.replace("-", " ").replace("_", " ").strip()
        base = base[:1].upper() + base[1:] if base else stem
        label = base if tag in ("latest", "") else f"{base} {tag}"
        short = model_id[:16]
    else:
        pretty = model_id.replace("-", " ").replace("_", " ").strip()
        label = pretty[:1].upper() + pretty[1:] if pretty else model_id
        short = model_id[:14]
    return {"label": label, "short": short, "roles": roles,
            "modalities": ["text"]}


def _live_ollama_models(base_url: str):
    """Installed Ollama models, newest API first; empty if the daemon is down."""
    try:
        from ollama_manager import get_manager
        mgr = get_manager(base_url or "http://localhost:11434")
        if not mgr.is_available():
            return []
        out = []
        for m in mgr.list_models() or []:
            name = m.get("name") if isinstance(m, dict) else str(m)
            if name and not str(name).endswith(":cloud"):
                out.append(name)
        return out
    except Exception:
        return []


def _model_entries_for(provider: dict, registry) -> list:
    """Expand one provider into per-model catalog entries."""
    pname = provider.get("name", "")
    plabel = provider.get("label") or pname
    ptype = provider.get("type", "")
    prov_roles = provider.get("roles") or [ROLE_ORCHESTRATOR, ROLE_SUBAGENT]
    meta = provider.get("model_meta") or {}
    costs = provider.get("cost_per_1k") or {}
    available = registry.is_provider_available(pname)
    is_local = ptype in ("ollama", "local-voice", "nemo-local")

    ids = list(provider.get("models") or [])
    if is_local:
        # Merge live-installed models so the picker reflects reality.
        for live in _live_ollama_models(provider.get("base_url")):
            if live not in ids:
                ids.append(live)
        # Local provider is "available" if any model is actually installed.
        available = available and bool(ids)

    entries = []
    for mid in ids:
        m = dict(_humanize(mid))
        m.update({k: v for k, v in (meta.get(mid) or {}).items() if v is not None})
        roles = m.get("roles") or prov_roles
        entries.append({
            "id": mid,
            "label": m.get("label") or mid,
            "short": m.get("short") or mid,
            "provider": pname,
            "provider_label": plabel,
            "roles": list(roles),
            "modalities": m.get("modalities") or ["text"],
            "local": is_local,
            "available": bool(available),
            "cost_per_1k": costs.get(mid),
        })
    return entries


def build_catalog() -> dict:
    """Return the full model catalog grouped by UI role.

    Shape:
      {
        "roles": { "orchestrator": [entry, ...], "subagent": [...],
                   "creative": [...], "voice": [...] },
        "models": [entry, ...],          # flat, de-duplicated by (id, provider)
        "providers": [ {name, label, type, available}, ... ],
      }
    Each entry: id, label, short, provider, provider_label, roles, modalities,
    local, available, cost_per_1k.
    """
    registry = get_provider_registry()
    flat, seen = [], set()
    for provider in registry.get_enabled_providers():
        for e in _model_entries_for(provider, registry):
            key = (e["id"], e["provider"])
            if key in seen:
                continue
            seen.add(key)
            flat.append(e)

    roles = {r: [] for r in ALL_ROLES}
    for e in flat:
        for r in e["roles"]:
            if r in roles:
                roles[r].append(e)

    # Stable, useful ordering: available first, then by provider then label.
    def _sort_key(e):
        return (0 if e["available"] else 1, e["provider_label"], e["label"])
    for r in roles:
        roles[r].sort(key=_sort_key)
    flat.sort(key=_sort_key)

    providers = [{
        "name": p.get("name"),
        "label": p.get("label") or p.get("name"),
        "type": p.get("type"),
        "available": registry.is_provider_available(p.get("name", "")),
    } for p in registry.get_enabled_providers()]

    return {"roles": roles, "models": flat, "providers": providers}

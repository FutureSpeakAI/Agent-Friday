"""
Agent Friday — Model Catalog

Single source of truth for the model picker. Reads the declarative
ProviderRegistry, enriches each model with presentation + role metadata,
merges in live-detected Ollama models, computes availability from the env keys,
and groups everything by UI role. The UI renders entirely from this (via
GET /api/models) — no model list is hardcoded in the frontend, so adding a
provider/model on the backend (or dropping a provider JSON in
~/.friday/providers/) surfaces it automatically.

Picker-hygiene invariants (regression: "tons of models, many in there twice,
some grayed out"):
  * Live Ollama models merge ONLY into providers of type "ollama" — never into
    the voice engine backends (local-voice / nemo-local), which used to
    triplicate every installed model in the agent roles.
  * Voice engine backends contribute NO per-model picker entries; they surface
    as availability on the `voice_engines` list instead.
  * Each role list carries at most ONE entry per model id (the picker stores
    only the id, so a second provider offering the same id is noise).
  * Unavailable entries carry `needs_key` + a human `hint` so the UI can dim
    them, block the click, and say exactly which key to add.
"""
from agent_friday.services.provider_registry import (
    get_provider_registry, ALL_ROLES,
    ROLE_ORCHESTRATOR, ROLE_SUBAGENT, ROLE_CREATIVE, ROLE_VOICE,
)

# Importing the router family helper is cheap and dependency-free.
try:
    from agent_friday.routing.model_router import provider_family
except Exception:  # pragma: no cover - router always importable in practice
    def provider_family(_):
        return None

# Voice ENGINE backends — ASR/TTS component stacks, not pickable chat/creative
# models. Their models (whisper/piper/nemo ids) never enter the role lists.
VOICE_ENGINE_PROVIDER_TYPES = ("local-voice", "nemo-local")


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
    """Installed Ollama models, in daemon order.

    Returns None when the daemon is unreachable (so callers can distinguish
    "Ollama not running" from "running with nothing installed", which is []).
    """
    try:
        from agent_friday.routing.ollama_manager import get_manager
        mgr = get_manager(base_url or "http://localhost:11434")
        if not mgr.is_available():
            return None
        out = []
        for m in mgr.list_models() or []:
            name = m.get("name") if isinstance(m, dict) else str(m)
            if name and not str(name).endswith(":cloud"):
                out.append(name)
        return out
    except Exception:
        return None


def _needs_key(provider: dict):
    """Env-var name a provider needs, or None (local / keyless providers)."""
    auth = provider.get("auth") or {}
    return auth.get("key") if auth.get("type") == "env_var" else None


def _discovered_models(provider: dict):
    """Discovery-cache models for an api-discovery provider (disk only, never
    the network — discovery fetches happen on the background sweep / explicit
    refresh). Returns (models list, stale bool); ([], False) when the provider
    has no API discovery."""
    if (provider.get("discovery") or {}).get("mode") != "api":
        return [], False
    try:
        from agent_friday.services.model_discovery import cached_models
        return cached_models(provider.get("name", ""))
    except Exception:
        return [], False


def _model_entries_for(provider: dict, registry) -> list:
    """Expand one provider into per-model catalog entries."""
    pname = provider.get("name", "")
    plabel = provider.get("label") or pname
    ptype = provider.get("type", "")
    prov_roles = provider.get("roles") or [ROLE_ORCHESTRATOR, ROLE_SUBAGENT]
    meta = provider.get("model_meta") or {}
    costs = provider.get("cost_per_1k") or {}
    available = registry.is_provider_available(pname)
    needs_key = _needs_key(provider)
    is_local = ptype in ("ollama",) + VOICE_ENGINE_PROVIDER_TYPES
    engine_backend = ptype in VOICE_ENGINE_PROVIDER_TYPES

    ids = list(provider.get("models") or [])
    hint = None
    if ptype == "ollama":
        # Live truth from the daemon — ONLY for real Ollama providers. The old
        # code merged these into every local-typed provider, which is exactly
        # what showed each installed model three times (once greyed under NeMo).
        live = _live_ollama_models(provider.get("base_url"))
        if live:              # daemon up, models installed → reality only
            ids = list(live)
        elif live is None:    # daemon down → static hints, dimmed
            available = False
            hint = "Ollama not running — start Ollama to use local models"
        else:                 # daemon up, nothing installed → static hints, dimmed
            available = False
            hint = "No local models installed — e.g. `ollama pull gemma3:4b`"
    elif is_local:
        available = available and bool(ids)

    if not available and hint is None:
        if needs_key:
            hint = f"Add {needs_key} in Settings → Providers"
        elif engine_backend:
            hint = "Run the Voice Setup Wizard to enable this engine"

    # Live-discovered models (OpenRouter's 300+, HF router's warm set, …) merge
    # AFTER the statics: statics keep their declared order (and any model_meta
    # overrides), discovery adds the long tail with wire-reported metadata.
    discovered, disc_stale = _discovered_models(provider)
    disc_by_id = {m.get("id"): m for m in discovered if m.get("id")}

    entries = []
    seen_ids = set()
    for mid in ids + [m for m in disc_by_id if m not in set(ids)]:
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        m = dict(_humanize(mid))
        disc = disc_by_id.get(mid)
        if disc:
            if disc.get("label"):
                m["label"] = disc["label"]
                m["short"] = disc["label"][:16]
            if disc.get("modalities"):
                m["modalities"] = list(disc["modalities"])
        m.update({k: v for k, v in (meta.get(mid) or {}).items() if v is not None})
        # Respect an explicit `roles: []` (e.g. Lyria — picked in the Studio
        # Music panel via `music_model`, never via the creative_model picker).
        roles = m["roles"] if isinstance(m.get("roles"), list) else list(prov_roles)
        if engine_backend:
            roles = []  # engine components are not pickable models
        entry = {
            "id": mid,
            "label": m.get("label") or mid,
            "short": m.get("short") or mid,
            "provider": pname,
            "provider_label": plabel,
            "roles": list(roles),
            "modalities": m.get("modalities") or ["text"],
            "local": is_local,
            "available": bool(available),
            "needs_key": needs_key,
            "hint": hint,
            "cost_per_1k": costs.get(mid),
        }
        if disc:
            # Discovery metadata (spec §6.3) — additive fields; the UI contract
            # (id/label/roles/available…) is untouched.
            entry.update({
                "context_window": disc.get("context_window"),
                "max_output": disc.get("max_output"),
                "supports_tools": disc.get("supports_tools"),
                "price_in": disc.get("price_in"),
                "price_out": disc.get("price_out"),
                "free": bool(disc.get("free")),
                "source": "discovery",
                "catalog_stale": bool(disc_stale),
            })
            if entry.get("cost_per_1k") is None and disc.get("price_in") is not None:
                # Blended per-1K display figure from the per-1M wire prices.
                try:
                    entry["cost_per_1k"] = round(
                        ((disc.get("price_in") or 0) + (disc.get("price_out") or 0))
                        / 2.0 / 1000.0, 6)
                except Exception:
                    pass
        entries.append(entry)
    return entries


def _voice_engines(registry) -> list:
    """The four voice ENGINE choices (settings key `voice_engine`), with live
    availability. Auto is always selectable — it resolves at session time."""
    local_ok = bool(registry.is_provider_available("local-voice-lite"))
    gpu_ok = bool(registry.is_provider_available("nvidia-nemo"))
    gemini_ok = bool(registry.is_provider_available("google-gemini"))
    return [
        {"id": "auto", "label": "Auto", "short": "Auto", "available": True,
         "hint": "GPU tier when ready, else CPU; local preferred over cloud"},
        {"id": "local", "label": "Local CPU (Whisper + Piper)",
         "short": "Local CPU", "available": local_ok,
         "hint": None if local_ok else
         "Local voice deps missing — run the Voice Setup Wizard"},
        {"id": "local-gpu", "label": "Local GPU (NeMo)",
         "short": "Local GPU", "available": gpu_ok,
         "hint": None if gpu_ok else
         "Needs torch + NeMo + a CUDA GPU — see the Voice Setup Wizard"},
        {"id": "gemini", "label": "Gemini Live (cloud)",
         "short": "Gemini Live", "available": gemini_ok,
         "hint": None if gemini_ok else
         "Add GEMINI_API_KEY in Settings → Providers"},
    ]


def build_catalog() -> dict:
    """Return the full model catalog grouped by UI role.

    Shape:
      {
        "roles": { "orchestrator": [entry, ...], "subagent": [...],
                   "creative": [...], "voice": [...] },   # deduped by id
        "models": [entry, ...],          # flat, de-duplicated by (id, provider)
        "providers": [ {name, label, type, available, needs_key}, ... ],
        "voice_engines": [ {id, label, short, available, hint}, ... ],
      }
    Each entry: id, label, short, provider, provider_label, roles, modalities,
    local, available, needs_key, hint, cost_per_1k.
    """
    registry = get_provider_registry()
    # Kick the model-discovery background sweep (async, off the hot path, no-op
    # under tests) so api-discovery providers (OpenRouter…) populate their
    # caches shortly after first use and the picker fills in without a restart.
    try:
        from agent_friday.services.model_discovery import ensure_background_refresh
        ensure_background_refresh()
    except Exception:
        pass
    flat, seen = [], set()
    for provider in registry.get_enabled_providers():
        for e in _model_entries_for(provider, registry):
            key = (e["id"], e["provider"])
            if key in seen:
                continue
            seen.add(key)
            e["_ord"] = len(flat)  # declaration order — models render as declared
            flat.append(e)

    # Stable, useful ordering: available first, then provider, then the order
    # the provider declared its models in (Sonnet 5 leads the Claude lineup —
    # alphabetical label sort would bury the default at the bottom).
    def _sort_key(e):
        return (0 if e["available"] else 1, e["provider_label"], e["_ord"])
    flat.sort(key=_sort_key)

    # Role lists: at most ONE entry per model id. The picker stores only the
    # id, so a second provider offering the same id is pure noise; the
    # available copy wins because flat is sorted available-first.
    roles = {r: [] for r in ALL_ROLES}
    for r in roles:
        seen_ids = set()
        for e in flat:
            if r in e["roles"] and e["id"] not in seen_ids:
                seen_ids.add(e["id"])
                roles[r].append(e)

    for e in flat:
        e.pop("_ord", None)

    providers = [{
        "name": p.get("name"),
        "label": p.get("label") or p.get("name"),
        "type": p.get("type"),
        "available": registry.is_provider_available(p.get("name", "")),
        "needs_key": _needs_key(p),
    } for p in registry.get_enabled_providers()]

    return {"roles": roles, "models": flat, "providers": providers,
            "voice_engines": _voice_engines(registry)}

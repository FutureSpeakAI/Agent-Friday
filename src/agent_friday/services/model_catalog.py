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
  * Role lists carry ONLY curated entries — models a provider declares in its
    descriptor (or live Ollama installs). The discovery long tail (OpenRouter's
    300+) stays in the flat `models` list for the Model Browser and
    /api/models/search; it used to flood every role picker with hundreds of
    grayed-out entries. The top-bar quick switcher renders from these curated
    role lists, which is what keeps it under ~15 entries.
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


def _live_ollama_running(base_url: str):
    """Names of models currently loaded in Ollama memory (GET /api/ps via the
    manager's short-TTL cache). Empty set when unreachable / none running."""
    try:
        from agent_friday.routing.ollama_manager import get_manager
        mgr = get_manager(base_url or "http://localhost:11434")
        names = set()
        for m in mgr.list_running() or []:
            name = m.get("name") if isinstance(m, dict) else str(m)
            if name:
                names.add(str(name))
        return names
    except Exception:
        return set()


def _custom_models() -> list:
    """User-declared custom model ids — settings key `custom_models`:
    [{"provider": ..., "id": ...}]. Prefer core.SETTINGS_FILE — the ONE
    canonical settings path, snapshotted at core import — over a fresh
    Path.home() lookup: Path.home() reads USERPROFILE at call time, and the
    test suite's hermetic-home redirection makes the two diverge mid-run
    (root cause of an order-dependent full-suite failure). Falls back to
    Path.home() only when core is unimportable, keeping this module usable
    from dependency-light contexts."""
    try:
        import json
        try:
            from agent_friday.core import SETTINGS_FILE as path
        except Exception:
            from pathlib import Path
            path = Path.home() / ".friday" / "settings.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("custom_models") or []
        return [i for i in items if isinstance(i, dict)]
    except Exception:
        return []


def _needs_key(provider: dict):
    """Env-var name a provider needs, or None (local / keyless providers)."""
    auth = provider.get("auth") or {}
    return auth.get("key") if auth.get("type") == "env_var" else None


def _discovered_models(provider: dict):
    """Discovery-cache models for an api-discovery provider (disk only, never
    the network — discovery fetches happen on the background sweep / explicit
    refresh). Returns (models list, stale bool); ([], False) when the provider
    has no API discovery.

    Hosted providers with a native (non-OpenAI-compatible) API — Anthropic —
    share the same disk cache; theirs is written by
    services/hosted_catalog.refresh() (POST /api/models/refresh) instead of
    the generic discovery sweep."""
    hosted_native = provider.get("type") == "anthropic"
    if (provider.get("discovery") or {}).get("mode") != "api" and not hosted_native:
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
    running_names = None
    if ptype == "ollama":
        # Live truth from the daemon — ONLY for real Ollama providers. The old
        # code merged these into every local-typed provider, which is exactly
        # what showed each installed model three times (once greyed under NeMo).
        live = _live_ollama_models(provider.get("base_url"))
        if live:              # daemon up, models installed → reality only
            ids = list(live)
            # /api/ps: which of these are loaded in memory right now (spec A1).
            running_names = _live_ollama_running(provider.get("base_url"))
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

    # Hosted-native catalog preference (spec A2): when hosted_catalog has
    # cached the provider's own live /v1/models list, that list REPLACES the
    # shipped statics — new ids (claude-opus-5, claude-haiku-4-5, …) surface
    # as curated picker entries with zero code changes. No cache → statics,
    # each flagged catalog_stale so the UI can say "showing built-in list".
    hosted_native = ptype == "anthropic"
    hosted_fallback = False
    if hosted_native:
        if disc_by_id:
            ids = [m.get("id") for m in discovered if m.get("id")]
        else:
            hosted_fallback = True

    # `ids` is final here: descriptor statics, or the live Ollama install list.
    # These are the curated, human-sized set that may enter role pickers; the
    # discovery-only tail is browse/search material.
    curated_ids = set(ids)

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
            "curated": mid in curated_ids,
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
        if hosted_native:
            if disc:
                # The provider's OWN authoritative list, not an aggregator's
                # long tail — distinct source so the role-picker curation rule
                # ("no `discovery` entries in role lists") keeps meaning what
                # it means while these stay curated and pickable.
                entry["source"] = "hosted"
            elif hosted_fallback:
                # Statics-only fallback: no live fetch has ever landed.
                entry["catalog_stale"] = True
        if running_names is not None:
            entry["running"] = mid in running_names
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
                   "creative": [...], "voice": [...] },   # curated, deduped by id
        "models": [entry, ...],          # flat, de-duplicated by (id, provider)
        "providers": [ {name, label, type, available, needs_key}, ... ],
        "voice_engines": [ {id, label, short, available, hint}, ... ],
      }
    Each entry: id, label, short, provider, provider_label, roles, modalities,
    local, available, needs_key, hint, cost_per_1k, curated.
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

    # Custom-model escape hatch (spec A2): settings `custom_models` —
    # [{"provider", "id"}] pairs the catalogs don't (yet) know about. Emitted
    # `unverified` and NON-curated: they appear in the flat list (Model
    # Browser / search / direct selection) but never flood the role pickers.
    providers_by_name = {p.get("name"): p for p in registry.get_enabled_providers()}
    for cm in _custom_models():
        pname = str(cm.get("provider") or "").strip()
        mid = str(cm.get("id") or "").strip()
        if not pname or not mid or (mid, pname) in seen:
            continue
        seen.add((mid, pname))
        prov = providers_by_name.get(pname)
        m = _humanize(mid)
        flat.append({
            "id": mid,
            "label": m["label"],
            "short": m["short"],
            "provider": pname,
            "provider_label": (prov or {}).get("label") or pname,
            "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
            "modalities": ["text"],
            "local": bool(prov and prov.get("type") == "ollama"),
            "available": bool(prov and registry.is_provider_available(pname)),
            "needs_key": _needs_key(prov or {}),
            "hint": None if prov else
                    f"Unknown provider '{pname}' — enable it in Settings → Providers",
            "cost_per_1k": None,
            "curated": False,
            "unverified": True,   # user-asserted id; nothing has confirmed it exists
            "source": "custom",
            "_ord": len(flat),
        })

    # Stable, useful ordering: available first, then provider, then the order
    # the provider declared its models in (Sonnet 5 leads the Claude lineup —
    # alphabetical label sort would bury the default at the bottom).
    def _sort_key(e):
        return (0 if e["available"] else 1, e["provider_label"], e["_ord"])
    flat.sort(key=_sort_key)

    # Role lists: curated entries only (discovery's long tail stays in `models`
    # for the Model Browser), at most ONE entry per model id. The picker stores
    # only the id, so a second provider offering the same id is pure noise; the
    # available copy wins because flat is sorted available-first.
    roles = {r: [] for r in ALL_ROLES}
    for r in roles:
        seen_ids = set()
        for e in flat:
            if r in e["roles"] and e.get("curated") and e["id"] not in seen_ids:
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

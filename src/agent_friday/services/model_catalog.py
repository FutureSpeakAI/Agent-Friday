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

# Providers whose OWN live catalog replaces their shipped statics (spec A2).
# These have native, non-OpenAI-compatible APIs, so the generic api-discovery
# sweep does not cover them; a dedicated module writes their list into the
# shared discovery cache instead:
#   anthropic  → services/hosted_catalog.refresh()
#   higgsfield → services/higgsfield_catalog.refresh()
# A provider listed here ships an EMPTY `models` list on purpose — what the
# picker offers is what the provider was last seen to actually have.
HOSTED_NATIVE_TYPES = ("anthropic", "higgsfield")


# ── Context-window lookup (decision D3) ──────────────────────────────────────
# Real per-model context windows have been fetched and cached by
# model_discovery since it shipped, and surfaced in the Model Browser — but no
# context-management layer ever read them. compaction.py assumed a flat 200_000
# tokens for every model and model_router assumed 2_000_000 characters, so a
# 4K-window local model got Claude-Opus-sized thresholds and would overflow
# before compaction ever fired.
#
# This is the single lookup those layers now consult. It is deliberately
# lightweight: the discovery disk cache only, never a catalog rebuild and never
# the network, because it sits on the hot path of every assembled model call.
_CTX_CACHE: dict = {}
_CTX_CACHE_TTL_S = 300.0


def context_window_for(model_id: str):
    """Real context window (tokens) for `model_id`, or None if unknown.

    None is meaningful and must be preserved: it means "the catalog has no
    value for this model", which is the signal for callers to fall back to
    their documented constant rather than inventing a number.
    """
    import time as _t

    mid = (model_id or "").strip()
    if not mid:
        return None
    hit = _CTX_CACHE.get(mid)
    if hit and (_t.time() - hit[0]) < _CTX_CACHE_TTL_S:
        return hit[1]

    win = None
    try:
        from agent_friday.services.model_discovery import cached_models
        registry = get_provider_registry()
        for prov in registry.list_providers():
            pname = prov.get("name", "")
            # Descriptor-declared metadata wins — it is hand-maintained.
            meta = (prov.get("model_meta") or {}).get(mid) or {}
            if meta.get("context_window"):
                win = int(meta["context_window"])
                break
            for m in (cached_models(pname)[0] or []):
                if m.get("id") == mid and m.get("context_window"):
                    win = int(m["context_window"])
                    break
            if win:
                break
    except Exception:
        win = None

    # Local models are the one class with no other source: descriptors don't
    # declare windows and API discovery doesn't cover Ollama — and they are
    # exactly the class with SMALL windows, i.e. the case D3 exists for. The
    # daemon knows (GGUF `<arch>.context_length`), so ask it.
    if win is None:
        try:
            from agent_friday.routing.ollama_manager import get_manager
            mgr = get_manager()
            if any((m.get("name") == mid or m.get("model") == mid)
                   for m in (mgr.list_models() or [])):
                got = mgr.context_length(mid)
                if got:
                    win = int(got)
        except Exception:
            pass

    _CTX_CACHE[mid] = (_t.time(), win)
    return win


def reset_context_window_cache():
    """Clear the memoised windows (tests, and after a discovery refresh)."""
    _CTX_CACHE.clear()


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
    hosted_native = provider.get("type") in HOSTED_NATIVE_TYPES
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
    # Ask the single authority rather than matching on type strings. A
    # hardcoded list is what made local openai-compatible providers render as
    # cloud (fixed in 53dd414) and would have done the same to the on-device
    # ComfyUI image provider — `classification_of` already enforces
    # local-capable-adapter AND private-base-url, so a descriptor cannot claim
    # local without earning it.
    try:
        from agent_friday.routing.provider_descriptors import classification_of
        is_local = classification_of(provider) == "local"
    except Exception:
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
        elif live is None:
            # Daemon down → it has NO models, and saying otherwise is invention.
            #
            # This used to fall back to the static list, which is how a stopped
            # daemon kept advertising "gemma4:latest" and "llama3.1:8b" —
            # neither installed, one not even a real tag. Stephen saw gemma4
            # listed as an Ollama model while the daemon was retired and the
            # real seat was answering two ports away. The provider row still
            # appears with the hint; it just stops naming models it does not
            # have.
            ids = []
            available = False
            hint = "Ollama not running — start Ollama to use its models"
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
        elif ptype == "higgsfield":
            # Degrade honestly. The connector being down is a different fact
            # from the account having no models, and the row says which —
            # rather than presenting the last enumeration as a live list.
            hint = ("Higgsfield connector not connected — authorize it in "
                    "Settings → Connectors")

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
    hosted_native = ptype in HOSTED_NATIVE_TYPES
    # An aggregator ships NO static model list -- its catalog IS the live one,
    # and there is no second list to fall back to. OpenRouter is the case:
    # services/hosted_catalog names it in HOSTED_PROVIDERS and fetches its
    # /api/v1/models, but this promotion gate was keyed on HOSTED_NATIVE_TYPES
    # ("anthropic", "higgsfield") and OpenRouter's type is "openai-compatible".
    # So its ~400 live models all landed curated=False, role lists take curated
    # entries ONLY, and the one provider whose catalog is entirely live became
    # the one provider absent from every picker -- models fetched, cached,
    # marked available, and unselectable. Stephen, 2026-08-30: "I added my
    # OpenRouter API key to Friday and it can't seem to switch on their models."
    #
    # Keyed on the SHAPE, not a second name list: "declares no statics and
    # discovers over the wire" is exactly the condition under which the
    # discovered list is the whole catalog, so the next aggregator works
    # without an edit here.
    live_catalog = (not ids
                    and (provider.get("discovery") or {}).get("mode") == "api")
    hosted_fallback = False
    if hosted_native or live_catalog:
        if disc_by_id:
            ids = [m.get("id") for m in discovered if m.get("id")]
        elif hosted_native:
            # Statics remain as the fallback list; an aggregator has none, so
            # an empty discovery cache correctly leaves it with no models
            # rather than inventing some.
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
            # A hosted-native catalog classifies its OWN models — Higgsfield
            # knows which of its ids generate images, which are post-processors
            # (roles: []), and which are speech rather than music. Without
            # this the enumerated entries inherited the provider's blanket
            # roles and an upscaler would have been offered as your image
            # generation model.
            if isinstance(disc.get("roles"), list):
                m["roles"] = list(disc["roles"])
            if disc.get("note") and not m.get("note"):
                m["note"] = disc["note"]
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
            # Stated on every row, because the UI was inferring it and getting
            # it wrong: app.html tested `provider.classification === 'local'`
            # against a field the API never sent, so EVERY model — including
            # on-device ones — rendered with a "cloud" badge.
            "classification": "local" if is_local else "cloud",
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
            # Per-model generation constraints (aspect ratios, durations,
            # resolutions, required inputs) as the provider publishes them —
            # a picker that offers a duration field for a model with no
            # duration parameter is guessing. Additive; absent for providers
            # that publish nothing.
            for extra in ("constraints", "kind", "note"):
                if disc.get(extra) is not None and entry.get(extra) is None:
                    entry[extra] = disc[extra]
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


def _arbiter_seat_entries() -> list:
    """The local models the residency Arbiter actually serves, as catalog rows.

    These are real processes on real ports, health-checked before they are
    trusted (runtime/residency/endpoints.json). They are marked `local` because
    they ARE local — served over loopback by a process Friday owns — and they
    carry `seat` so the picker can say which chair a model is sitting in.

    Availability is earned here too: a seat in the plan that has no live
    endpoint is offered but flagged, never silently presented as ready.
    """
    try:
        from agent_friday.services.residency_arbiter import (
            get_arbiter, owned_endpoint)
    except Exception:
        return []
    seats = {}
    try:
        arb = get_arbiter()
        if arb is not None:
            seats = (arb.plan or {}).get("seats") or {}
    except Exception:
        seats = {}
    if not seats:
        # No in-process Arbiter — read the seat map off disk instead of
        # returning nothing. Suppressing the dead Ollama daemon's invented list
        # is right, but it must not cost Stephen every local model in the
        # picker when the Arbiter simply isn't governing THIS process. The
        # endpoints file is written by the Arbiter that is, and health-checked
        # before it is trusted.
        try:
            import json as _json
            from agent_friday.core import runtime_dir
            p = runtime_dir() / "residency" / "endpoints.json"
            if p.exists():
                raw = _json.loads(p.read_text(encoding="utf-8")) or {}
                entries = raw.get("seats") or raw
                if isinstance(entries, dict):
                    for k, v in entries.items():
                        if isinstance(v, dict) and v.get("model_id"):
                            seats[k] = v
                        elif isinstance(v, str):
                            seats[k] = {"model_id": k}
        except Exception:
            seats = seats or {}
    if not seats:
        return []
    out, seen_ids = [], set()
    for seat, s in seats.items():
        if not isinstance(s, dict):
            continue
        mid = s.get("model_id")
        # Only language seats belong in a MODEL picker. The image seat has its
        # own provider entry, and stt/tts are voice engines.
        if not mid or seat in ("stt", "tts", "image") or mid in seen_ids:
            continue
        seen_ids.add(mid)
        try:
            live = bool(owned_endpoint(mid))
        except Exception:
            live = False
        m = _humanize(mid)
        ctx = s.get("num_ctx")
        out.append({
            "id": mid,
            "label": m["label"],
            "short": m["short"],
            "provider": "arbiter-local",
            "provider_label": "Local (Friday's own seats)",
            "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
            "modalities": ["text", "tools"],
            "local": True,
            "classification": "local",
            "available": True,
            "needs_key": None,
            "hint": ("%s seat · %s%s" % (
                seat, s.get("device") or "?",
                (" · %s ctx" % ctx) if ctx else ""))
            + ("" if live else " · not currently loaded"),
            "seat": seat,
            "resident": live,
            "cost_per_1k": 0.0,
            "curated": True,
        })
    return out


def _friday_store_entries(exclude: set | None = None) -> list:
    """Every model Friday HOLDS, whether or not it is seated right now.

    `_arbiter_seat_entries` reports the residency PLAN, which is a statement
    about what should be hot -- not about what exists. Stephen's store holds
    gemma4:e2b, :12b, :e4b and :26b; the plan pins two of them; so two of his
    own models were absent from his own picker. One of the absent pair was
    serving a live seat on 127.0.0.1:8090 while he was looking at a list that
    did not mention it, which is the screenshot he sent.

    A model on disk with a template beside it is a model he can pick. Whether
    it is loaded is a `resident` flag, not a reason to hide it.
    """
    exclude = exclude or set()
    out = []
    try:
        import json as _json
        import os as _os
        import pathlib as _pl
        home = _os.environ.get("USERPROFILE") or _os.path.expanduser("~")
        raw = _pl.Path(home, ".friday", "runtime", "models",
                       "models.json").read_text(encoding="utf-8")
        rows = (_json.loads(raw) or {}).get("models") or {}
    except Exception:
        return []

    try:
        from agent_friday.services.residency_arbiter import owned_endpoint
    except Exception:
        owned_endpoint = lambda _m: None            # noqa: E731

    for mid, rec in rows.items():
        if mid in exclude or not isinstance(rec, dict):
            continue
        if rec.get("is_embedding") or rec.get("can_generate") is False:
            continue
        path = rec.get("path")
        if path:
            try:
                import pathlib as _pl2
                if not _pl2.Path(path).exists():
                    continue                        # listed is not present
            except Exception:
                pass
        try:
            live = bool(owned_endpoint(mid))
        except Exception:
            live = False
        m = _humanize(mid)
        gb = float(rec.get("size_bytes") or 0) / 1e9
        out.append({
            "id": mid,
            "label": m["label"],
            "short": m["short"],
            "provider": "arbiter-local",
            "provider_label": "Local (Friday's own seats)",
            "roles": [ROLE_ORCHESTRATOR, ROLE_SUBAGENT],
            "modalities": ["text", "tools"],
            "local": True,
            "classification": "local",
            "available": True,
            "needs_key": None,
            "hint": ("in Friday's store · %.1f GB" % gb)
                    + ("" if live else " · not currently loaded"),
            "seat": None,
            "resident": live,
            "cost_per_1k": 0.0,
            "curated": True,
        })
    return out


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
    # The seats Friday ACTUALLY serves go in FIRST, so they win the dedupe.
    #
    # 2026-08-16, Stephen: "listing Gemma4 as a cloud model, and as an Ollama
    # model (it is neither)". Both entries came from the retired `ollama-local`
    # provider, whose hardcoded fallback list still named gemma4 while the
    # daemon it describes is stopped — and the models he is really running,
    # served by llama-server processes the Arbiter owns, appeared nowhere at
    # all. The picker was showing a dead daemon's guesses instead of the live
    # residency plan.
    for e in _arbiter_seat_entries():
        seen.add((e["id"], e["provider"]))
        e["_ord"] = len(flat)
        flat.append(e)
    # Then everything else Friday holds on disk but has not seated.
    for e in _friday_store_entries(exclude={f["id"] for f in flat}):
        seen.add((e["id"], e["provider"]))
        e["_ord"] = len(flat)
        flat.append(e)

    _local_ids = {f["id"] for f in flat}
    for provider in registry.get_enabled_providers():
        for e in _model_entries_for(provider, registry):
            key = (e["id"], e["provider"])
            if key in seen:
                continue
            # ONE model, ONE row. `embeddinggemma:300m` is in both stores and
            # appeared twice -- once as `arbiter-local`, once as
            # `ollama-local` -- because the dedupe key carried the provider.
            # Two rows for one model is a question he has to answer before he
            # can pick, about a difference that does not affect the answer.
            # Friday's own runtime wins; the daemon copy is the same weights.
            if e.get("provider") == "ollama-local" and e["id"] in _local_ids:
                continue
            # A retired daemon must not name models. Ollama's daemon is stopped
            # and Friday does not need it, so its STATIC fallback list is pure
            # fiction — the same model, real and resident two ports away, is
            # already in `flat` from the residency plan above.
            if (e.get("provider") == "ollama-local" and not e.get("available")
                    and any(f["id"] == e["id"] for f in flat)):
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

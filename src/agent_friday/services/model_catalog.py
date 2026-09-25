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


def max_output_for(model_id: str):
    """The model's own maximum output, in tokens, or None if unknown.

    None is meaningful and must be preserved, exactly as in
    `context_window_for`: it means the catalog has no figure, and the caller
    decides what to do rather than being handed an invented number.

    This exists because a `max_tokens` we choose is a cap WE impose. Sending
    4096 to a model that can write 128,000 silently truncates a long answer and
    looks like the model stopping early. The figures come from the same two
    places the windows do -- the hand-maintained descriptor metadata, and the
    provider's own listing (OpenRouter reports `top_provider.max_completion_
    tokens`) -- so they track the providers rather than a table here.
    """
    mid = (model_id or "").strip()
    if not mid:
        return None
    try:
        from agent_friday.services.model_discovery import cached_models
        registry = get_provider_registry()
        for prov in registry.list_providers():
            pname = prov.get("name", "")
            meta = (prov.get("model_meta") or {}).get(mid) or {}
            if meta.get("max_output"):
                return int(meta["max_output"])
            for m in (cached_models(pname)[0] or []):
                if m.get("id") == mid and m.get("max_output"):
                    return int(m["max_output"])
    except Exception:
        pass
    return None


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

    # WHAT IS SERVED BEATS WHAT IS PLANNED OR DECLARED.
    #
    # Measured on bonsai2:27b — three different numbers for one model:
    #   models.json declares  262,144   (the architecture's maximum)
    #   the residency rung     65,536   (the PLAN)
    #   llama-server serves    49,152   (/props default_generation_settings.n_ctx)
    #
    # The plan is 33% above what the running process will accept. That
    # is the same shape as the e4b 400s (a planned 65,536 against a served
    # 32,768) — a prompt budgeted to the planned window gets rejected. So when a
    # seat is actually up, its own answer wins. It is the only one of the three
    # that can be wrong in a way the user feels.
    served = _served_context_window(mid)
    if served:
        _CTX_CACHE[mid] = (_t.time(), served)
        return served

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


def _served_context_window(model_id: str):
    """The context window the live seat for `model_id` is actually serving.

    Read from the owned llama-server's ``/props`` (its
    ``default_generation_settings.n_ctx``), through a snapshot so this never
    costs a request anything: a seat that is gone would otherwise pay a
    connection timeout on the hot path that assembles every prompt.

    None when no seat is up, when it has not been read yet, or when the endpoint
    does not report one -- in which case the declared/planned value is used, as
    before.
    """
    try:
        from agent_friday.services import machine_probe as _mp
        got, _at, _state = _mp.snapshot(
            "models:served_ctx:" + model_id,
            lambda: _served_context_window_uncached(model_id),
            fresh_for=60.0, budget=0.0, default=None)
        return got or None
    except Exception:
        return None


def _served_context_window_uncached(model_id: str):
    """Ask the owned endpoint for its n_ctx. Background use only."""
    try:
        import json as _json
        import urllib.request as _ur

        from agent_friday.services.residency_arbiter import owned_endpoint
        ep = owned_endpoint(model_id)
        if not ep:
            return None
        base = ep if isinstance(ep, str) else (ep.get("base_url") or "")
        if not base:
            return None
        base = base.rstrip("/")
        for suffix in ("/v1", ""):
            if base.endswith(suffix) and suffix:
                base = base[: -len(suffix)]
                break
        with _ur.urlopen(base + "/props", timeout=3.0) as r:
            props = _json.load(r) or {}
        dg = props.get("default_generation_settings") or {}
        n = dg.get("n_ctx") or props.get("n_ctx")
        return int(n) if n else None
    except Exception:
        return None


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
    """Installed Ollama models, in daemon order, read through a snapshot.

    Returns None when the daemon is unreachable OR has not been read yet (so
    callers can still distinguish it from "running with nothing installed",
    which is []).

    Measured 4.05s cold with no daemon running: `is_available()` waits out a
    connection timeout, and this sits inside `build_catalog`, so unsnapshotted
    it is 4 of an 18-second model picker open. A picker must not wait on a daemon
    that is not there.
    """
    from agent_friday.services import machine_probe as _mp
    got, _at, _state = _mp.snapshot(
        "models:ollama_installed:" + (base_url or "default"),
        lambda: _live_ollama_models_uncached(base_url),
        fresh_for=30.0, budget=0.0, default=None)
    return got


def _live_ollama_models_uncached(base_url: str):
    """The real Ollama probe. Background use only -- see `_live_ollama_models`."""
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
    friday_home() lookup: friday_home() reads USERPROFILE (or FRIDAY_HOME) at
    call time, and the test suite's hermetic-home redirection makes the two
    diverge mid-run (root cause of an order-dependent full-suite failure).
    Falls back to friday_home() (from the dependency-light agent_friday.paths
    module, not agent_friday.core) only when core is unimportable, keeping
    this module usable from dependency-light contexts."""
    try:
        import json
        try:
            from agent_friday.core import SETTINGS_FILE as path
        except Exception:
            from agent_friday.paths import friday_home
            path = friday_home() / "settings.json"
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
    # Availability is a cheap env-key check for cloud providers and a MACHINE
    # PROBE for the local engine ones. Measured cold:
    # nvidia-nemo 2.69s (imports torch/NeMo), ollama-local 4.05s. Only those
    # are snapshotted, so a cloud row still resolves synchronously and the
    # picker opens with every shipped and discovered model present.
    if ptype in ("ollama",) + VOICE_ENGINE_PROVIDER_TYPES:
        from agent_friday.services import machine_probe as _mp
        available, _av_at, _av_state = _mp.snapshot(
            "models:provider_available:" + pname,
            lambda: bool(registry.is_provider_available(pname)),
            fresh_for=60.0, budget=0.0, default=None)
        if available is None:
            # Not read yet. Offered-but-dimmed with a reason that says so,
            # rather than claimed ready or silently dropped.
            available = False
            _unread_provider = True
        else:
            _unread_provider = False
    else:
        available = registry.is_provider_available(pname)
        _unread_provider = False
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
            # Falling back to the static list here would let a stopped daemon
            # keep advertising tags that are not installed (or not even real)
            # while the real seat is answering on another port. The provider
            # row still appears with the hint; it just stops naming models it
            # does not have.
            ids = []
            available = False
            hint = "Ollama not running — start Ollama to use its models"
        else:                 # daemon up, nothing installed → static hints, dimmed
            available = False
            hint = "No local models installed — e.g. `ollama pull gemma3:4b`"
    elif is_local:
        available = available and bool(ids)

    if _unread_provider and hint is None:
        hint = ("Still reading this engine from the machine — reopen in a "
                "moment for a verdict.")
    if not available and hint is None:
        if needs_key:
            hint = f"Add {needs_key} in Settings → Accounts & Keys"
        elif engine_backend:
            hint = "Run the Voice Setup Wizard to enable this engine"
        elif ptype == "higgsfield":
            # Degrade honestly. The connector being down is a different fact
            # from the account having no models, and the row says which —
            # rather than presenting the last enumeration as a live list.
            hint = ("Higgsfield connector not connected — authorize it in "
                    "Settings → Accounts & Keys")

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
    hosted_fallback = False
    if hosted_native:
        if disc_by_id:
            # A CACHED LIST MUST NOT HIDE A MODEL WE SHIP.
            #
            # This replaced the statics outright, and the cache has a 24h TTL.
            # So on the day Claude Opus 5.5 was added to the shipped list, the
            # picker did not offer it: the cached /v1/models list predated the
            # release, was not yet stale, and won. Its declared metadata still
            # applied (the corrected Sonnet 5 rate showed up fine), which made
            # the omission look like a typo in the id rather than the cache
            # deciding what the product offers.
            #
            # Discovery still LEADS — it is live truth and it carries the long
            # tail — but a statically shipped id is our own claim that the
            # model exists, and it is appended rather than dropped. Retired ids
            # are removed from the statics when they retire (see the anthropic
            # descriptor), so this cannot resurrect one.
            live = [m.get("id") for m in discovered if m.get("id")]
            ids = live + [m for m in ids if m not in disc_by_id]
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
            # Descriptor-declared presentation must reach the UI: e.g.
            # sd3.5-medium-fp8's model_meta carries a `licence`, and the picker
            # can only say "commercial use needs $1M+ revenue" if that field
            # leaves this function. `note` may be overwritten below by a
            # DISCOVERY-sourced one only when the descriptor didn't set one
            # (see the `disc.get(extra) is not None and entry.get(extra) is
            # None` guard a few lines down) — declared metadata wins.
            "note": m.get("note"),
            "licence": m.get("licence"),
            "licence_note": m.get("licence_note"),
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
    """The voice MODE choices (settings key `voice_engine`), with live
    availability.

    `auto` is deliberately NOT offered (voice-system-clean-sheet.md §8.1 A):
    it is a synonym for `local` (it never reaches the cloud),
    so a picker entry for it is a second name for the same thing. The value
    is still accepted on write (`_VOICE_ENUMS`) and read as local, so an
    existing settings.json keeps working."""
    # Measured 3.44s inside `build_catalog` -- the availability
    # checks for the local voice engines probe for torch/NeMo. Snapshotted for
    # the same reason `_tts_engines` is: a picker must not wait on an import.
    # Cold reads render every row unavailable WITH A REASON that says the check
    # has not finished, rather than guessing either way.
    from agent_friday.services import machine_probe as _mp

    def _probe():
        return {
            "local": bool(registry.is_provider_available("local-voice-lite")),
            "gpu": bool(registry.is_provider_available("nvidia-nemo")),
            "gemini": bool(registry.is_provider_available("google-gemini")),
        }

    # budget=0 for the same reason as Kokoro: measured 3.4s, so no affordable
    # wait reaches an answer.
    _av, _at, _state = _mp.snapshot("voice:engine_availability", _probe,
                                    fresh_for=300.0, budget=0.0, default=None)
    if _av is None:
        _unread = ("Couldn't read this engine's dependencies yet -- checking in "
                   "the background. Reopen in a moment.")
        return [
            {"id": "local", "label": "Local CPU (Whisper + Piper)",
             "short": "Local CPU", "available": False,
             "reading": _mp.STATE_UNKNOWN, "hint": _unread},
            {"id": "local-gpu", "label": "Local GPU (NeMo)",
             "short": "Local GPU", "available": False,
             "reading": _mp.STATE_UNKNOWN, "hint": _unread},
            {"id": "gemini", "label": "Gemini Live (cloud)",
             "short": "Gemini Live", "available": False,
             "reading": _mp.STATE_UNKNOWN, "hint": _unread},
        ]
    local_ok, gpu_ok, gemini_ok = _av["local"], _av["gpu"], _av["gemini"]
    return [
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
         "Add GEMINI_API_KEY in Settings → Accounts & Keys"},
    ]


def _tts_engines() -> list:
    """The Tier-1 synthesizer choices (settings key `local_voice_tts_engine`).

    Shape matches `_voice_engines()` so the settings UI can render both with the
    same greyed-with-a-reason control. Availability comes from the engine's own
    health block, not from a filename: `kokoro_available()` performs the import
    (see kokoro_voice.kokoro_import_status), because a package that resolves by
    name and raises on import is exactly the state a `--no-deps` install leaves
    behind, and reporting it as ready is how a picker starts lying.

    `hint` is the remediation shown on the disabled control. It says what would
    make the option work, per the spec's rule that an unavailable option must
    explain itself rather than disappear.
    """
    out = [{"id": "piper", "label": "Piper (CPU)", "short": "Piper",
            "available": True,
            "hint": "On-device, CPU-capable. GPL-3.0 since October 2025."}]
    # NEVER CALL kokoro_health() ON THE REQUEST PATH.
    #
    # It imports kokoro deliberately (see the docstring above), and that pulls
    # in torch. Measured: 28.9s on the first call, 0.00s after. This runs
    # inside `build_catalog`, so called inline that import would dominate the
    # model picker's first open after every restart.
    #
    # Read through a snapshot with a hard budget instead: the cached verdict if
    # there is one, otherwise a row that says it has not been read yet while the
    # import proceeds in the background. Unknown renders as
    # unavailable-with-a-reason and never as ready, because calling an
    # unimportable package ready is the exact lie this probe exists to prevent.
    from agent_friday.services import machine_probe as _mp
    # budget=0: this probe imports torch and takes ~29s, so waiting 1.5s for it
    # can only ever cost 1.5s and still return "unknown". Boot warming is what
    # makes this row populated in practice.
    h, _at, _state = _mp.snapshot(
        "voice:kokoro_health", _kokoro_health_uncached,
        fresh_for=300.0, budget=0.0, default=None)
    if h is None:
        out.append({
            "id": "kokoro", "label": "Kokoro-82M (GPU)", "short": "Kokoro",
            "available": False, "status": "unknown", "default": False,
            "reading": _mp.STATE_UNKNOWN,
            "hint": ("Couldn't read the GPU voice engine yet -- it is being "
                     "checked in the background (the check imports torch, which "
                     "is slow on a cold start). Reopen in a moment."),
        })
        return out
    _ok = bool(h.get("available")) and h.get("status") == "ok"
    _hint = h.get("detail") or "Kokoro status unknown"
    _age = _mp.age_note(_at, _state)
    if _age:
        _hint = "%s (%s)" % (_hint, _age)
    if _ok:
        # Selectable, deliberately not the default. Kokoro is fast on a GPU
        # (~12x realtime measured) but it is newer on this path than Piper and
        # its phonemisation runs third-party code over arbitrary text. Say so
        # where the choice is made, rather than letting the user infer that
        # "available" means "as proven as the default".
        _hint = (_hint + " \u2014 newer than Piper on this path; Piper stays the "
                 "default. A synthesis failure refuses with a reason and offers "
                 "Piper rather than substituting it silently.")
    out.append({
        "id": "kokoro", "label": "Kokoro-82M (GPU)", "short": "Kokoro",
        "available": _ok,
        "status": h.get("status", "error"),
        "default": False,
        "reading": _state,
        "hint": _hint,
    })
    return out


def register_warmers() -> None:
    """Tell `machine_probe` what to warm at boot.

    Registered rather than called so `warm_all()` does not have to import the
    heavy paths itself merely to know they exist -- importing them is the cost
    being avoided.
    """
    from agent_friday.services import machine_probe as _mp
    _mp.register_warmer("voice:kokoro_health",
                        lambda: _mp.snapshot("voice:kokoro_health",
                                             _kokoro_health_uncached,
                                             fresh_for=300.0, budget=120.0))

    def _voice_av():
        reg = get_provider_registry()
        return _mp.snapshot(
            "voice:engine_availability",
            lambda: {"local": bool(reg.is_provider_available("local-voice-lite")),
                     "gpu": bool(reg.is_provider_available("nvidia-nemo")),
                     "gemini": bool(reg.is_provider_available("google-gemini"))},
            fresh_for=300.0, budget=120.0)

    _mp.register_warmer("voice:engine_availability", _voice_av)
    def _slow_provider_probes():
        reg = get_provider_registry()
        for prov in reg.get_enabled_providers():
            nm, ty = prov.get("name"), prov.get("type")
            if ty not in ("ollama",) + VOICE_ENGINE_PROVIDER_TYPES:
                continue
            _mp.snapshot("models:provider_available:" + nm,
                         lambda nm=nm: bool(reg.is_provider_available(nm)),
                         fresh_for=60.0, budget=30.0)
            if ty == "ollama":
                base = prov.get("base_url") or "default"
                _mp.snapshot("models:ollama_installed:" + base,
                             lambda b=prov.get("base_url"):
                                 _live_ollama_models_uncached(b),
                             fresh_for=30.0, budget=30.0)

    _mp.register_warmer("models:slow_provider_probes", _slow_provider_probes)
    def _overlays():
        reg = get_provider_registry()
        for prov in reg.get_enabled_providers():
            _mp.snapshot("models:creative_overlay:" + str(prov.get("name")),
                         lambda prov=prov: _overlay_uncached(prov),
                         fresh_for=60.0, budget=20.0)

    _mp.register_warmer("models:creative_overlays", _overlays)
    _mp.register_warmer("models:friday_store",
                        lambda: _mp.snapshot("models:friday_store",
                                             _friday_store_entries_uncached,
                                             fresh_for=30.0, budget=30.0))
    _mp.register_warmer("models:arbiter_seats",
                        lambda: _mp.snapshot("models:arbiter_seats",
                                             _arbiter_seat_entries_uncached,
                                             fresh_for=20.0, budget=60.0))


def _overlay_uncached(provider: dict) -> dict:
    """Apply the per-machine creative overlay. Background use only: it probes
    ComfyUI over loopback and pays a connection timeout when it is not up."""
    try:
        from agent_friday.services.local_creative_overrides import (
            merge_local_creative_overlay)
        return merge_local_creative_overlay(provider)
    except Exception:
        return provider


def _kokoro_health_uncached() -> dict:
    """The real Kokoro probe, for the background snapshot only.

    Never call this from a request: the import inside it took 28.9s on this
    machine. A failure is returned as data rather than raised, so the snapshot
    caches "it does not import" instead of retrying the 29-second import on
    every menu open.
    """
    try:
        from agent_friday.services.kokoro_voice import kokoro_health
        return kokoro_health() or {}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "detail": str(e)[:160], "available": False}


def _arbiter_seat_entries() -> list:
    """Snapshotted view of the seats the Arbiter serves.

    Measured 4.07s cold: the real builder health-checks each seat
    over loopback, and a seat whose process is gone costs a connection timeout.
    The picker must not wait on that, so it is read through `machine_probe`. A
    cold read returns [] -- the ARBITER rows are simply absent for a moment
    rather than invented or guessed -- and the shipped/discovered models still
    render, so the picker opens complete enough to use.
    """
    from agent_friday.services import machine_probe as _mp
    rows, _at, _state = _mp.snapshot("models:arbiter_seats",
                                     _arbiter_seat_entries_uncached,
                                     fresh_for=20.0, budget=0.0, default=None)
    if rows is None:
        return []
    if _state != _mp.STATE_FRESH:
        note = _mp.age_note(_at, _state)
        rows = [dict(r, hint=("%s (%s)" % (r.get("hint") or "", note)).strip())
                for r in rows]
    return rows


def _arbiter_seat_entries_uncached() -> list:
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
        # is right, but it must not cost the user every local model in the
        # picker when the Arbiter simply isn't governing THIS process. The
        # endpoints file is written by the Arbiter that is, and health-checked
        # before it is trusted.
        try:
            import json as _json
            from agent_friday.core import runtime_dir
            p = runtime_dir() / "residency" / "endpoints.json"
            if p.exists():
                raw = _json.loads(p.read_text(encoding="utf-8")) or {}
                # THE SHAPE THE ARBITER ACTUALLY WRITES IS `endpoints`.
                #
                # This read `raw.get("seats") or raw` and then looked for dict
                # values carrying model_id, or plain strings. The real file is
                #
                #   {"pid": ..., "updated_at": ..., "endpoints": {"<model>": "<url>"}}
                #
                # so iterating `raw` yields pid (an int), updated_at (a float)
                # and endpoints (a dict with no model_id) - all three skipped,
                # and the seat sitting one level down is never seen. The
                # fallback that exists precisely so a running local model still
                # reaches the picker extracted ZERO seats from a file naming
                # one, which is why bonsai2 was absent from the catalogue while
                # llama-server was serving it on 8090.
                #
                # `endpoints` is checked first now, and the older shapes are
                # still accepted so an Arbiter writing either keeps working.
                entries = (raw.get("endpoints") or raw.get("seats") or raw)
                if isinstance(entries, dict):
                    for k, v in entries.items():
                        if k in ("pid", "updated_at"):
                            continue
                        if isinstance(v, dict) and v.get("model_id"):
                            seats[k] = v
                        elif isinstance(v, str):
                            # {model_id: endpoint_url} - the key is the model.
                            seats[k] = {"model_id": k, "endpoint": v}
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
    """Snapshotted wrapper -- the real builder measures 4.09s cold.

    The real builder asks a guarded presence probe about each model Friday
    holds, and a model whose endpoint is gone costs a timeout. A cold read
    returns [] rather than a guess: the store rows are briefly absent while the
    shipped and discovered models still render, so the picker opens usable.
    """
    from agent_friday.services import machine_probe as _mp
    rows, _at, _state = _mp.snapshot("models:friday_store",
                                     _friday_store_entries_uncached,
                                     fresh_for=30.0, budget=0.0, default=None)
    if rows is None:
        return []
    ex = exclude or set()
    return [r for r in rows if r.get("id") not in ex]


def _friday_store_entries_uncached(exclude: set | None = None) -> list:
    """Every model Friday HOLDS, whether or not it is seated right now.

    `_arbiter_seat_entries` reports the residency PLAN, which is a statement
    about what should be hot -- not about what exists. A store holding
    gemma4:e2b, :12b, :e4b and :26b with a plan that pins two of them would
    otherwise leave two of the user's own models absent from the picker, even
    while one of them is serving a live seat on 127.0.0.1:8090.

    A model on disk with a template beside it is a model the user can pick.
    Whether it is loaded is a `resident` flag, not a reason to hide it.
    """
    exclude = exclude or set()
    out = []
    try:
        import json as _json
        import pathlib as _pl
        from agent_friday.paths import friday_home
        # Honours FRIDAY_HOME. Still assumes the default "runtime"
        # segment rather than calling runtime_dir(): resolving that
        # imports core, a ~4s Flask bootstrap, on the CLI path. So
        # this still ignores FRIDAY_RUNTIME_DIR -- pre-existing, and
        # tracked separately from the FRIDAY_HOME fix.
        raw = _pl.Path(friday_home(), "runtime", "models",
                       "models.json").read_text(encoding="utf-8")
        rows = (_json.loads(raw) or {}).get("models") or {}
    except Exception:
        return []

    try:
        from agent_friday.services.residency_arbiter import owned_endpoint
    except Exception:
        owned_endpoint = lambda _m: None            # noqa: E731

    # The same presence question local_seats._friday_store asks, answered
    # the same way: through the guarded probe, never `Path.exists()` inline.
    # Profiled with the WSL share wedged: an inline stat on the FridayWeaver
    # record costs 25.4 s of a 27.2 s /api/intelligence request,
    # the endpoint the top-bar model pill reads. Retired records and
    # fine-tunes without their adapter are not pickable either.
    try:
        from agent_friday.services import path_probe as _probe
    except Exception:
        _probe = None
    store_dir = _pl.Path(friday_home(), "runtime", "models", "gguf")

    def _reachable(rec, key):
        p = rec.get(key)
        if not p:
            return True
        local = (rec.get("local_files") or {}).get(key)
        cands = [c for c in (local, str(store_dir / _pl.Path(str(p)).name), p)
                 if c]
        if _probe is None:
            return any(_pl.Path(c).exists() for c in cands)
        return any(_probe.exists(c) for c in cands)

    for mid, rec in rows.items():
        if mid in exclude or not isinstance(rec, dict):
            continue
        if rec.get("is_embedding") or rec.get("can_generate") is False:
            continue
        if rec.get("retired"):
            continue
        try:
            if not _reachable(rec, "path"):
                continue                            # listed is not present
            if rec.get("lora") and not _reachable(rec, "lora"):
                continue                            # base without its adapter
        except Exception:
            pass
        try:
            live = bool(owned_endpoint(mid))
        except Exception:
            live = False
        m = _humanize(mid)
        # A registry entry may carry its own display name (set at `register`
        # time via `label=`) — e.g. a fine-tune given a human-chosen name that
        # humanizing model_id would mangle (hyphens -> spaces, only the first
        # letter cased). That name renders verbatim; everything else keeps the
        # inferred label.
        display_label = rec.get("label") or m["label"]
        gb = float(rec.get("size_bytes") or 0) / 1e9
        out.append({
            "id": mid,
            "label": display_label,
            "short": rec.get("label") or m["short"],
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
    # Otherwise a retired `ollama-local` provider's hardcoded fallback list can
    # name a model as both a cloud model and an Ollama model (it is neither)
    # while the daemon it describes is stopped — and the models really
    # running, served by llama-server processes the Arbiter owns, appear
    # nowhere at all. The picker must show the live residency plan, not a
    # dead daemon's guesses.
    # Rows are COPIED before the sort key is written. The seat and store rows
    # come from a shared snapshot cache, and two builds at once (the top-bar
    # menu and Settings ask together) wrote and deleted `_ord` on the same
    # dicts, so one build's cleanup broke the other's sort (KeyError '_ord').
    for e in _arbiter_seat_entries():
        e = dict(e)
        seen.add((e["id"], e["provider"]))
        e["_ord"] = len(flat)
        flat.append(e)
    # Then everything else Friday holds on disk but has not seated.
    for e in _friday_store_entries(exclude={f["id"] for f in flat}):
        e = dict(e)
        seen.add((e["id"], e["provider"]))
        e["_ord"] = len(flat)
        flat.append(e)

    _local_ids = {f["id"] for f in flat}
    for provider in registry.get_enabled_providers():
        # Per-machine creative overlay (FLUX.1 dev and anything else too
        # licence-restricted to ship in provider_registry.py's shipped
        # defaults) — a no-op copy when no overlay file exists, so a fresh
        # install's local-comfyui descriptor passes through unchanged. See
        # services/local_creative_overrides.py for why this can't live in
        # provider_registry.py itself.
        try:
            # Measured 4.08s cold for local-comfyui: the overlay probes
            # ComfyUI on :8188, which is usually not running, so it pays a
            # connection timeout. Snapshotted; a cold read leaves the
            # descriptor un-overlaid, which is the same thing that happens when
            # there is no overlay file -- the shipped creative models still
            # render.
            from agent_friday.services import machine_probe as _mp
            _ov, _ov_at, _ov_state = _mp.snapshot(
                "models:creative_overlay:" + str(provider.get("name")),
                lambda provider=provider: _overlay_uncached(provider),
                fresh_for=60.0, budget=0.0, default=None)
            if _ov is not None:
                provider = _ov
        except Exception:
            pass
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
            e = dict(e)
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
                    f"Unknown provider '{pname}' — enable it in Settings → Accounts & Keys",
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
            "voice_engines": _voice_engines(registry),
            "tts_engines": _tts_engines()}

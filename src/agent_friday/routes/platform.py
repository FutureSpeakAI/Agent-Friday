"""Platform endpoints — the Goose-inspired extensibility layer.

Exposes the seven extensibility services over the web API:
  * recipes            — YAML workflows (list / save / dry-run / run)
  * providers          — declarative model-provider registry
  * hints              — .fridayhints inspection
  * prompt manager     — composable prompt segment preview
  * distros            — custom distribution profiles
  * subagents          — scope-restricted background delegation
  * extension security — MCP server risk assessment + allowlist

All state lives under ~/.friday/; nothing here talks to a model directly —
recipe/subagent runs delegate to the canonical background-task pipeline.
"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from agent_friday.services import distributions, extension_security, hints, recipes, subagents
from agent_friday.services.prompt_manager import SEGMENT_KEYS, PromptManager
from agent_friday.services.provider_registry import get_provider_registry

platform_bp = Blueprint("platform", __name__)


# ── Recipes ──────────────────────────────────────────────────────────────────

def _find_recipe(name: str):
    path = recipes.RECIPES_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    return recipes.load_recipe(str(path))


@platform_bp.route("/api/recipes", methods=["GET"])
def api_recipes_list():
    return jsonify({"recipes": recipes.list_recipes()})


@platform_bp.route("/api/recipes", methods=["POST"])
def api_recipes_save():
    data = request.get_json(silent=True) or {}
    try:
        recipes.Recipe(data).validate()
    except recipes.RecipeValidationError as e:
        return jsonify({"error": str(e)}), 400
    path = recipes.save_recipe(data)
    return jsonify({"ok": True, "path": path})


@platform_bp.route("/api/recipes/<name>/validate", methods=["GET"])
def api_recipes_validate(name):
    r = _find_recipe(name)
    if r is None:
        return jsonify({"error": f"recipe '{name}' not found"}), 404
    try:
        r.validate()
        return jsonify({"valid": True, "steps": len(r.steps)})
    except recipes.RecipeValidationError as e:
        return jsonify({"valid": False, "errors": str(e)})


@platform_bp.route("/api/recipes/<name>/dry-run", methods=["POST"])
def api_recipes_dry_run(name):
    r = _find_recipe(name)
    if r is None:
        return jsonify({"error": f"recipe '{name}' not found"}), 404
    data = request.get_json(silent=True) or {}
    return jsonify({"recipe": r.name, "plan": r.dry_run(data.get("variables"))})


@platform_bp.route("/api/recipes/<name>/run", methods=["POST"])
def api_recipes_run(name):
    """Run a recipe as a scoped background task (default scope: recipe-runner)."""
    r = _find_recipe(name)
    if r is None:
        return jsonify({"error": f"recipe '{name}' not found"}), 404
    data = request.get_json(silent=True) or {}
    variables = data.get("variables") or {}
    missing = [k for k, v in r.variables.items()
               if v.get("required") and not variables.get(k) and not v.get("default")]
    if missing:
        return jsonify({"error": f"missing required variables: {', '.join(missing)}"}), 400

    plan = r.dry_run(variables)
    step_lines = []
    for p in plan:
        desc = p.get("description") or p.get("action")
        params = ", ".join(f"{k}={v}" for k, v in (p.get("params") or {}).items())
        step_lines.append(f"{p['step']}. {desc}" + (f" — using {p['action']}({params})"
                                                    if p.get("action") != "generate" else ""))
    prompt = (
        f"Execute the recipe '{r.name}' ({r.description}).\n"
        "Work through these steps in order, using your tools, and finish with a "
        "single consolidated result the user can read:\n" + "\n".join(step_lines)
    )
    try:
        spawned = subagents.spawn_scoped_subagent(
            name=f"Recipe: {r.name}", prompt=prompt,
            scope=data.get("scope") or "recipe-runner",
            description=r.description,
        )
    except KeyError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **spawned, "plan": plan})


# ── Providers ────────────────────────────────────────────────────────────────

def _provider_spend_today():
    """provider name -> USD spent today (from the cost ledger). Best-effort."""
    try:
        from agent_friday.services import cost_meter
        summ = cost_meter.summary("today") or {}
        return {name: round(float((row or {}).get("usd") or 0), 4)
                for name, row in (summ.get("by_provider") or {}).items()}
    except Exception:
        return {}


@platform_bp.route("/api/providers", methods=["GET"])
def api_providers_list():
    """All registered providers, enriched with availability, live health stats
    (latency/error-rate ring buffers), catalog size, spend today, and origin
    (builtin / file / ui)."""
    reg = get_provider_registry()
    spend = _provider_spend_today()
    include_stats = (request.args.get("stats") or "1").lower() not in ("0", "false")
    out = []
    for p in reg.list_providers():
        name = p.get("name", "")
        entry = {**p, "available": reg.is_provider_available(name),
                 "origin": reg.provider_origin(name)}
        # Model count: statics ∪ discovery cache (disk only, no network).
        model_count = len(p.get("models") or [])
        catalog_stale = False
        if (p.get("discovery") or {}).get("mode") == "api":
            try:
                from agent_friday.services.model_discovery import cached_models
                cached, stale = cached_models(name)
                model_count = len({*(p.get("models") or []),
                                   *(m.get("id") for m in cached if m.get("id"))})
                catalog_stale = bool(cached) and stale
            except Exception:
                pass
        entry["model_count"] = model_count
        entry["catalog_stale"] = catalog_stale
        entry["spend_today_usd"] = spend.get(name, 0.0)
        if include_stats:
            try:
                from agent_friday.services import provider_health
                entry["health"] = provider_health.stats(name)
            except Exception:
                entry["health"] = None
        out.append(entry)
    resp = {"providers": out}
    errors = reg.load_errors()
    if errors:
        resp["descriptor_errors"] = errors
    return jsonify(resp)


@platform_bp.route("/api/providers/templates", methods=["GET"])
def api_providers_templates():
    return jsonify({"templates": get_provider_registry().get_templates()})


@platform_bp.route("/api/providers", methods=["POST"])
def api_providers_add():
    data = request.get_json(silent=True) or {}
    if not (data.get("name") or "").strip():
        return jsonify({"error": "provider requires a 'name'"}), 400
    try:
        path = get_provider_registry().add_provider(data)
    except ValueError as e:
        return jsonify({"error": str(e),
                        "errors": [s.strip() for s in str(e).split(";")]}), 400
    # A new/changed provider may serve models the picker should show.
    try:
        from agent_friday.services.model_discovery import invalidate_cache
        invalidate_cache(data.get("name", ""))
    except Exception:
        pass
    return jsonify({"ok": True, "path": path})


@platform_bp.route("/api/providers/validate", methods=["POST"])
def api_providers_validate():
    """Dry-run descriptor validation for the Add Provider form — no write."""
    from agent_friday.routing.provider_descriptors import validate_descriptor
    data = request.get_json(silent=True) or {}
    ok, errors, warnings = validate_descriptor(data)
    return jsonify({"ok": ok, "errors": errors, "warnings": warnings})


@platform_bp.route("/api/providers/<name>", methods=["PATCH"])
def api_providers_patch(name):
    """Partial descriptor update (enable/disable, base_url, budget, …).
    Persists the merged descriptor as a user override."""
    data = request.get_json(silent=True) or {}
    data.pop("name", None)  # the URL is authoritative
    try:
        updated = get_provider_registry().update_provider(name, data)
    except KeyError:
        return jsonify({"error": f"provider '{name}' not found"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "provider": updated})


@platform_bp.route("/api/providers/<name>", methods=["DELETE"])
def api_providers_remove(name):
    """Remove a user-added provider; a customized BUILT-IN reverts to its
    shipped default instead (its override file is deleted)."""
    reg = get_provider_registry()
    was_builtin = reg.is_builtin(name)
    if not reg.remove_provider(name):
        return jsonify({"error": f"provider '{name}' not found"}), 404
    try:
        from agent_friday.services.model_discovery import invalidate_cache
        invalidate_cache(name)
    except Exception:
        pass
    return jsonify({"ok": True, "reverted_to_builtin": was_builtin})


@platform_bp.route("/api/providers/health", methods=["GET"])
def api_providers_health():
    """Per-provider reachability/auth status for the wizard + Settings provider step.
    Shallow by default (offline-safe); pass ?deep=1 for a light endpoint probe.
    Each entry also carries the measured stats block (latency percentiles,
    error rate, availability) when any calls have been recorded."""
    from agent_friday.services import provider_health
    deep = (request.args.get("deep") or "").lower() in ("1", "true", "yes")
    checks = provider_health.check_all(deep=deep)
    for c in checks:
        try:
            c["stats"] = provider_health.stats(c.get("provider", ""))
        except Exception:
            c["stats"] = None
    return jsonify({"providers": checks})


@platform_bp.route("/api/providers/<name>/test", methods=["POST"])
def api_provider_test(name):
    """Test Connection: a deep, adapter-aware probe returning latency and the
    number of models the endpoint reports. Body {"ping": true} additionally
    runs a 1-token completion (openai-compatible providers only) to verify the
    key end-to-end. Never echoes key material."""
    import time as _t
    reg = get_provider_registry()
    prov = reg.get_provider(name)
    if not prov:
        return jsonify({"error": f"provider '{name}' not found"}), 404
    body = request.get_json(silent=True) or {}
    do_ping = bool(body.get("ping"))
    ptype = prov.get("type", "")
    from agent_friday.routing.provider_descriptors import (
        provider_api_key, auth_headers)
    result = {"provider": name, "status": "error", "latency_ms": None,
              "auth": None, "models_seen": None, "ping": None, "detail": None}

    api_key = provider_api_key(prov)  # pragma: allowlist secret
    auth_required = (prov.get("auth") or {}).get("type", "env_var") == "env_var"
    result["auth"] = "configured" if (api_key or not auth_required) else "missing"

    if ptype == "ollama":
        try:
            from agent_friday.routing.ollama_manager import get_manager
            t0 = _t.time()
            mgr = get_manager(prov.get("base_url") or "http://localhost:11434")
            ok = mgr.is_available()
            models = mgr.list_models() if ok else []
            result.update(status="ok" if ok else "down",
                          latency_ms=int((_t.time() - t0) * 1000),
                          models_seen=len(models or []),
                          detail=None if ok else "Ollama daemon not reachable")
        except Exception as e:
            result["detail"] = str(e)[:200]
        return jsonify(result)

    if ptype in ("local-voice", "nemo-local"):
        from agent_friday.services import provider_health
        chk = provider_health.check_provider(name, deep=True, use_cache=False)
        result.update(status=chk.get("status"), detail=chk.get("detail"))
        return jsonify(result)

    import requests
    base = (prov.get("base_url") or "").rstrip("/")
    if not base:
        result["detail"] = "provider has no base_url"
        return jsonify(result), 400
    headers = auth_headers(prov, api_key)
    t0 = _t.time()
    try:
        if ptype == "anthropic":
            r = requests.get(base + "/v1/models",
                             headers={"x-api-key": api_key or "",
                                      "anthropic-version": "2023-06-01"},
                             timeout=10)
        elif ptype == "google":
            r = requests.get(base + "/v1beta/models",
                             params={"key": api_key or ""}, timeout=10)
        else:  # openai-compatible
            r = requests.get(base + "/models", headers=headers, timeout=10)
        latency = int((_t.time() - t0) * 1000)
        result["latency_ms"] = latency
        if r.status_code in (401, 403):
            result.update(status="unauthorized", auth="invalid",
                          detail=f"HTTP {r.status_code} — check the API key")
        elif r.status_code >= 400:
            result.update(status="error", detail=f"HTTP {r.status_code}")
        else:
            try:
                payload = r.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if data is None and isinstance(payload, dict):
                    data = payload.get("models")
                result["models_seen"] = len(data) if isinstance(data, list) else None
            except Exception:
                result["models_seen"] = None
            result["status"] = "ok"
            if api_key:
                result["auth"] = "valid"
        try:
            from agent_friday.services import provider_health
            provider_health.record(name, result["status"] == "ok",
                                   latency_ms=latency, status=r.status_code,
                                   kind="probe")
        except Exception:
            pass
    except Exception as e:
        result.update(status="down",
                      latency_ms=int((_t.time() - t0) * 1000),
                      detail=f"{type(e).__name__}: {e}"[:200])

    if do_ping and result["status"] == "ok" and ptype == "openai-compatible":
        model = body.get("model") or (prov.get("models") or [None])[0]
        if not model:
            try:
                from agent_friday.services.model_discovery import cached_model_ids
                ids = cached_model_ids(name)
                model = ids[0] if ids else None
            except Exception:
                model = None
        if model:
            try:
                t0 = _t.time()
                r = requests.post(
                    base + "/chat/completions", headers=headers,
                    json={"model": model, "max_tokens": 1,
                          "messages": [{"role": "user", "content": "ping"}]},
                    timeout=30)
                result["ping"] = {"model": model, "ok": r.status_code < 400,
                                  "latency_ms": int((_t.time() - t0) * 1000),
                                  "status": r.status_code}
            except Exception as e:
                result["ping"] = {"model": model, "ok": False,
                                  "error": str(e)[:200]}
        else:
            result["ping"] = {"ok": False,
                              "error": "no model available to ping — refresh "
                                       "the model list first"}
    return jsonify(result)


@platform_bp.route("/api/providers/<name>/models/refresh", methods=["POST"])
def api_provider_models_refresh(name):
    """Force model discovery for one provider NOW (the ⟳ Refresh list button)."""
    from agent_friday.services.model_discovery import refresh_models
    res = refresh_models(name)
    status = 200 if res.get("ok") else 502
    if res.get("error") == "no such provider":
        status = 404
    elif (res.get("error") or "").startswith(("provider has no", "no API key")):
        status = 400
    return jsonify(res), status


@platform_bp.route("/api/models/search", methods=["GET"])
def api_models_search():
    """Search models across ALL registered providers (statics + discovery
    caches, live Ollama installs). Filters: q (substring on id/label),
    provider, free=1, tools=1, local=1 (on-device providers only), modality
    (exact member of the modalities list, e.g. vision/image/video),
    max_price_in (USD per 1M), min_context. sort=price|price_desc|context
    orders BEFORE the limit truncates, so "cheapest 50 of 350" is exact —
    which is also why every filter (including local) must run server-side.
    Powers the Model Browser."""
    q = (request.args.get("q") or "").strip().lower()
    want_provider = (request.args.get("provider") or "").strip()
    want_free = (request.args.get("free") or "") in ("1", "true", "yes")
    want_tools = (request.args.get("tools") or "") in ("1", "true", "yes")
    want_local = (request.args.get("local") or "") in ("1", "true", "yes")
    want_modality = (request.args.get("modality") or "").strip().lower()
    sort = (request.args.get("sort") or "").strip().lower()
    try:
        max_price_in = float(request.args.get("max_price_in"))
    except (TypeError, ValueError):
        max_price_in = None
    try:
        min_context = int(request.args.get("min_context"))
    except (TypeError, ValueError):
        min_context = None
    try:
        limit = max(1, min(int(request.args.get("limit") or 100), 500))
    except (TypeError, ValueError):
        limit = 100

    reg = get_provider_registry()
    from agent_friday.routing.provider_descriptors import classification_of
    results = []
    for prov in reg.get_enabled_providers():
        name = prov.get("name", "")
        if want_provider and name != want_provider:
            continue
        if prov.get("type") in ("local-voice", "nemo-local"):
            continue  # engine backends are not pickable chat models
        available = reg.is_provider_available(name)
        is_local = classification_of(prov) == "local"
        if want_local and not is_local:
            continue
        meta = prov.get("model_meta") or {}
        ids = list(prov.get("models") or [])
        if prov.get("type") == "ollama":
            # Live daemon truth, like the catalog builder — the browser must
            # list what is actually installed, not the descriptor's hints.
            try:
                from agent_friday.services.model_catalog import _live_ollama_models
                live = _live_ollama_models(prov.get("base_url"))
                if live is not None:
                    ids = live
            except Exception:
                pass
        rows = {}
        for mid in ids:
            mm = meta.get(mid) or {}
            rows[mid] = {"id": mid, "label": mm.get("label") or mid,
                         "modalities": mm.get("modalities") or ["text"],
                         "source": "static"}
        if (prov.get("discovery") or {}).get("mode") == "api":
            try:
                from agent_friday.services.model_discovery import cached_models
                for m in cached_models(name)[0]:
                    if m.get("id"):
                        rows[m["id"]] = {**m, "source": "discovery"}
            except Exception:
                pass
        for mid, m in rows.items():
            label = str(m.get("label") or mid)
            if q and q not in mid.lower() and q not in label.lower():
                continue
            if want_free and not m.get("free"):
                continue
            if want_tools and m.get("supports_tools") is False:
                continue
            if want_modality and want_modality not in (m.get("modalities")
                                                       or ["text"]):
                continue
            if max_price_in is not None and (m.get("price_in") or 0) > max_price_in:
                continue
            if min_context is not None and (m.get("context_window") or 0) < min_context:
                continue
            results.append({
                "id": mid, "label": label, "provider": name,
                "provider_label": prov.get("label") or name,
                "context_window": m.get("context_window"),
                "price_in": m.get("price_in"), "price_out": m.get("price_out"),
                "free": bool(m.get("free")),
                # Per-generation cost, for providers that charge that way
                # (Higgsfield credits) rather than per token. Passed through as
                # None when unknown so the browser can say "cost unknown"
                # instead of implying free; this list is where a 0.15-credit
                # and a 22.5-credit model sit one click apart.
                "credits": m.get("credits"),
                "supports_tools": m.get("supports_tools"),
                "modalities": m.get("modalities") or ["text"],
                "local": is_local,
                "available": available, "source": m.get("source", "static"),
            })
    # Default: available first, then provider, then id — stable and useful.
    # Price sorts put unpriced entries LAST (unknown ≠ free, pricing.py's rule).
    if sort in ("price", "price_desc"):
        rev = sort == "price_desc"
        results.sort(key=lambda r: (r.get("price_in") is None,
                                    (r.get("price_in") or 0)
                                    * (-1 if rev else 1)))
    elif sort == "context":
        results.sort(key=lambda r: -(r.get("context_window") or 0))
    else:
        results.sort(key=lambda r: (0 if r["available"] else 1,
                                    r["provider_label"], r["id"]))
    return jsonify({"query": q, "count": len(results),
                    "models": results[:limit]})


@platform_bp.route("/api/providers/<name>/key", methods=["POST", "DELETE"])
def api_provider_key(name):
    """Store (POST) or remove (DELETE) a provider's API key, encrypted at rest via
    the credential store. The key is hot-reloaded into the running process and is
    NEVER echoed back — only a connected/missing status is returned."""
    from agent_friday.services import credential_store as cs
    if request.method == "DELETE":
        removed = cs.delete_provider_key(name)
        cs.clear_provider_key_live(name)
        return jsonify({"ok": True, "provider": name, "removed": removed,
                        "status": cs.provider_key_status(name)})
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or data.get("api_key") or "").strip()
    if not key:
        return jsonify({"error": "'key' is required"}), 400
    method = cs.set_provider_key(name, key)
    cs.hot_reload_provider_key(name, key)
    return jsonify({"ok": True, "provider": name, "status": "connected",
                    "protection": method})


@platform_bp.route("/api/providers/<name>/reload-key", methods=["POST"])
def api_provider_reload_key(name):
    """Re-read the stored key for <name> from the credential store (or env) and
    reinitialize the provider client — without requiring the caller to re-submit
    the key value. Returns the provider's live status after reload.

    Useful after rotating a key externally: the new value is already stored,
    and this endpoint applies it to the running process without a restart.
    """
    from agent_friday.services import credential_store as cs
    stored = cs.get_provider_key(name)
    if stored:
        cs.hot_reload_provider_key(name, stored)
        return jsonify({"ok": True, "provider": name, "status": "connected",
                        "source": "credential_store"})
    # Fall back to env var (user may have rotated start.bat manually)
    env_map = {"anthropic": "ANTHROPIC_API_KEY", "google-gemini": "GEMINI_API_KEY",
               "openai": "OPENAI_API_KEY", "ollama": None}
    env_key = env_map.get(name) or f"{name.upper().replace('-', '_')}_API_KEY"
    env_val = (os.environ.get(env_key) or "").strip()
    if env_val:
        cs.hot_reload_provider_key(name, env_val)
        return jsonify({"ok": True, "provider": name, "status": "connected",
                        "source": "environment"})
    return jsonify({"ok": False, "provider": name,
                    "status": cs.provider_key_status(name),
                    "message": f"No stored or env key found for provider '{name}'"}), 404


# ── Capabilities (lock/unlock badges + graceful degradation) ─────────────────

@platform_bp.route("/api/capabilities", methods=["GET"])
def api_capabilities():
    """Each capability resolved to provider+model+availability — the UI renders
    lock/unlock badges and 'Connect X to unlock Y' hints from this."""
    from agent_friday.services import capability_router
    return jsonify({"capabilities": capability_router.route_table()})


# ── Hints ────────────────────────────────────────────────────────────────────

@platform_bp.route("/api/hints", methods=["GET"])
def api_hints():
    """Resolved hints: global ~/.friday/.fridayhints merged with ?path=… chain."""
    merged = hints.get_global_hints()
    target = request.args.get("path")
    if target:
        merged = merged.merge(hints.find_hints_for_path(target))
    return jsonify({
        "system_prompt_additions": merged.system_prompt_additions,
        "preferred_model": merged.preferred_model,
        "tools_enabled": merged.tools_enabled,
        "tools_disabled": merged.tools_disabled,
        "personality_overrides": merged.personality_overrides,
        "workspace_config": merged.workspace_config,
        "context_notes": merged.context_notes,
        "source": merged.source,
    })


# ── Prompt manager ───────────────────────────────────────────────────────────

@platform_bp.route("/api/prompts/segments", methods=["GET"])
def api_prompt_segments():
    return jsonify({"standard_segments": SEGMENT_KEYS})


@platform_bp.route("/api/prompts/preview", methods=["POST"])
def api_prompt_preview():
    """Assemble posted segments and return the built prompt (debug/preview)."""
    data = request.get_json(silent=True) or {}
    pm = PromptManager(total_budget=int(data.get("budget", 8000)))
    for seg in data.get("segments") or []:
        key = (seg.get("key") or "").strip()
        if not key:
            continue
        pm.set(key, seg.get("content", ""),
               priority=int(seg.get("priority", SEGMENT_KEYS.get(key, 50))),
               max_tokens=seg.get("max_tokens"))
    return jsonify({"prompt": pm.build(), "segments": pm.list_segments()})


# ── Distributions ────────────────────────────────────────────────────────────

@platform_bp.route("/api/distros", methods=["GET"])
def api_distros_list():
    return jsonify({"distros": distributions.list_distros(),
                    "active": distributions.get_active_distro()})


@platform_bp.route("/api/distros/<name>", methods=["GET"])
def api_distros_get(name):
    try:
        return jsonify(distributions.load_distro(name).raw)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@platform_bp.route("/api/distros", methods=["POST"])
def api_distros_save():
    data = request.get_json(silent=True) or {}
    if not (data.get("name") or "").strip():
        return jsonify({"error": "distro requires a 'name'"}), 400
    path = distributions.save_distro(data)
    return jsonify({"ok": True, "path": path})


@platform_bp.route("/api/distros/<name>/apply", methods=["POST"])
def api_distros_apply(name):
    """Activate a distribution: write its preset (workspaces/layout + personality)
    into settings.json through the single settings source of truth."""
    import agent_friday.core as core
    try:
        delta = distributions.apply_distro(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    merged = core._save_settings(delta)
    return jsonify({"ok": True, "distribution": name, "applied": delta, "settings": merged})


# ── Aggregate health (onboarding + Settings → Hardware/About + post-install) ──

def _optional_dep_status() -> dict:
    """Which optional dependency groups are importable in this environment."""
    import importlib.util
    groups = {
        "voice": ["google.genai"],
        "voice-local": ["faster_whisper", "piper"],
        "voice-local-gpu": ["torch", "nemo"],
        "creative": ["google.genai"],
        "local": ["requests"],
        "memory": ["chromadb", "sentence_transformers"],
    }
    out = {g: all(importlib.util.find_spec(m) is not None for m in mods)
           for g, mods in groups.items()}
    if out.get("voice-local-gpu"):
        # Importability is not runnability for the GPU tier: a CPU-only torch
        # wheel imports fine but NeMo can never run on it. Require actual CUDA
        # so onboarding never tells the user the GPU tier is ready when it isn't.
        try:
            from agent_friday.services.nemo_voice import gpu_status
            out["voice-local-gpu"] = bool(gpu_status().get("cuda"))
        except Exception:
            out["voice-local-gpu"] = False
    return out


@platform_bp.route("/api/health/full", methods=["GET"])
def api_health_full():
    """Aggregate subsystem status: server, providers, capabilities, demo mode,
    hardware, vault encryption, optional deps, connected services. Every block is
    independently guarded so one failing subsystem never 500s the whole report."""
    import time as _t
    import agent_friday.core as core
    out = {"status": "ok"}
    try:
        out["agent_friday.server"] = {"uptime_seconds": int(_t.time() - getattr(core, "SERVER_START_TS", _t.time()))}
    except Exception as e:
        out["agent_friday.server"] = {"error": str(e)}

    settings = {}
    try:
        settings = core._load_settings()
    except Exception:
        pass
    out["distribution"] = settings.get("distribution", "default")

    try:
        from agent_friday.services import provider_health
        out["providers"] = provider_health.check_all(deep=False)
    except Exception as e:
        out["providers"] = {"error": str(e)}

    # Descriptor files that failed validation/parse this session — surfaced so
    # a typo'd ~/.friday/providers/*.json shows up here instead of vanishing.
    try:
        errs = get_provider_registry().load_errors()
        if errs:
            out["provider_descriptor_errors"] = errs
    except Exception:
        pass

    try:
        from agent_friday.services import capability_router
        out["capabilities"] = capability_router.route_table(settings)
    except Exception as e:
        out["capabilities"] = {"error": str(e)}

    try:
        from agent_friday.services import demo_mode
        out["demo"] = demo_mode.demo_status(settings)
    except Exception as e:
        out["demo"] = {"error": str(e)}

    try:
        from agent_friday.routing.ollama_manager import get_manager
        base = (settings.get("model_routing") or {}).get("ollama_url") or "http://localhost:11434"
        mgr = get_manager(base)
        hw = mgr.detect_hardware()
        avail = mgr.is_available()
        # installed_models (names only) lets the onboarding wizard skip the
        # bundled-Gemma pull prompt when the model is already present (H5).
        try:
            installed = [m.get("name", "") for m in (mgr.list_models() if avail else [])]
        except Exception:
            installed = []
        out["hardware"] = {**hw, "suggested_models": mgr.recommend_models(hw),
                           "ollama_available": avail,
                           "installed_models": installed}
    except Exception as e:
        out["hardware"] = {"error": str(e)}

    try:
        from agent_friday.services import credential_store
        out["vault"] = {"credential_protection": credential_store.protection_method()}
    except Exception as e:
        out["vault"] = {"error": str(e)}

    # Local voice (Tier-1) readiness: deps installed + models downloaded + the
    # resolved engine the mic button will use. Sourced from agent_friday.services.local_voice.
    try:
        from agent_friday.services.local_voice import local_voice_health
        lv = local_voice_health()
        try:
            from agent_friday.routes.voice import _resolve_voice_engine
            lv["resolved_engine"] = _resolve_voice_engine(settings)
        except Exception:
            pass
        out["local_voice"] = lv
    except Exception as e:
        out["local_voice"] = {"error": str(e)}

    out["dependencies"] = _optional_dep_status()

    try:
        from agent_friday.services import google_accounts
        accts = google_accounts.list_accounts()
        out["google_accounts"] = len(accts) if isinstance(accts, (list, tuple)) else 0
    except Exception:
        out["google_accounts"] = 0

    try:
        from agent_friday.services.agent import _load_mcp_servers
        srv = _load_mcp_servers() or {}
        mcp = srv.get("mcpServers", srv) if isinstance(srv, dict) else srv
        out["mcp_servers"] = len(mcp) if hasattr(mcp, "__len__") else 0
    except Exception:
        out["mcp_servers"] = 0

    return jsonify(out)


# ── Scoped subagents ─────────────────────────────────────────────────────────

@platform_bp.route("/api/subagents/scopes", methods=["GET"])
def api_subagent_scopes():
    return jsonify({"scopes": subagents.list_scopes()})


@platform_bp.route("/api/subagents/scopes", methods=["POST"])
def api_subagent_scope_save():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, "scope": subagents.save_custom_scope(data)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@platform_bp.route("/api/subagents", methods=["GET"])
def api_subagents_list():
    return jsonify({"subagents": subagents.list_scoped_tasks()})


@platform_bp.route("/api/subagents/spawn", methods=["POST"])
def api_subagents_spawn():
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "'prompt' is required"}), 400
    try:
        spawned = subagents.spawn_scoped_subagent(
            name=(data.get("name") or prompt[:48]),
            prompt=prompt,
            scope=data.get("scope") or "researcher",
            description=data.get("description") or "",
        )
    except KeyError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **spawned})


# ── Extension security ───────────────────────────────────────────────────────

@platform_bp.route("/api/extensions/security", methods=["GET"])
def api_extension_security():
    from agent_friday.services.agent import _load_mcp_servers
    return jsonify(extension_security.assess_config(_load_mcp_servers()))


@platform_bp.route("/api/extensions/security/assess", methods=["POST"])
def api_extension_security_assess():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "candidate").strip()
    spec = data.get("spec") or {}
    if not isinstance(spec, dict) or not spec.get("command"):
        return jsonify({"error": "'spec' with a 'command' is required"}), 400
    return jsonify(extension_security.assess_server(name, spec))


@platform_bp.route("/api/extensions/allowlist", methods=["GET"])
def api_extension_allowlist():
    return jsonify({"allowlist": extension_security.get_allowlist()})


@platform_bp.route("/api/extensions/allowlist", methods=["POST"])
def api_extension_allowlist_add():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "'name' is required"}), 400
    return jsonify({"allowlist": extension_security.add_to_allowlist(name)})


@platform_bp.route("/api/extensions/allowlist/<name>", methods=["DELETE"])
def api_extension_allowlist_remove(name):
    return jsonify({"allowlist": extension_security.remove_from_allowlist(name)})

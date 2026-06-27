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

from flask import Blueprint, jsonify, request

from services import distributions, extension_security, hints, recipes, subagents
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

@platform_bp.route("/api/providers", methods=["GET"])
def api_providers_list():
    reg = get_provider_registry()
    out = []
    for p in reg.list_providers():
        out.append({**p, "available": reg.is_provider_available(p["name"])})
    return jsonify({"providers": out})


@platform_bp.route("/api/providers/templates", methods=["GET"])
def api_providers_templates():
    return jsonify({"templates": get_provider_registry().get_templates()})


@platform_bp.route("/api/providers", methods=["POST"])
def api_providers_add():
    data = request.get_json(silent=True) or {}
    if not (data.get("name") or "").strip():
        return jsonify({"error": "provider requires a 'name'"}), 400
    path = get_provider_registry().add_provider(data)
    return jsonify({"ok": True, "path": path})


@platform_bp.route("/api/providers/<name>", methods=["DELETE"])
def api_providers_remove(name):
    if not get_provider_registry().remove_provider(name):
        return jsonify({"error": f"provider '{name}' not found"}), 404
    return jsonify({"ok": True})


@platform_bp.route("/api/providers/health", methods=["GET"])
def api_providers_health():
    """Per-provider reachability/auth status for the wizard + Settings provider step.
    Shallow by default (offline-safe); pass ?deep=1 for a light endpoint probe."""
    from services import provider_health
    deep = (request.args.get("deep") or "").lower() in ("1", "true", "yes")
    return jsonify({"providers": provider_health.check_all(deep=deep)})


@platform_bp.route("/api/providers/<name>/key", methods=["POST", "DELETE"])
def api_provider_key(name):
    """Store (POST) or remove (DELETE) a provider's API key, encrypted at rest via
    the credential store. The key is hot-reloaded into the running process and is
    NEVER echoed back — only a connected/missing status is returned."""
    from services import credential_store as cs
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


# ── Capabilities (lock/unlock badges + graceful degradation) ─────────────────

@platform_bp.route("/api/capabilities", methods=["GET"])
def api_capabilities():
    """Each capability resolved to provider+model+availability — the UI renders
    lock/unlock badges and 'Connect X to unlock Y' hints from this."""
    from services import capability_router
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
    return {g: all(importlib.util.find_spec(m) is not None for m in mods)
            for g, mods in groups.items()}


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
        from services import provider_health
        out["providers"] = provider_health.check_all(deep=False)
    except Exception as e:
        out["providers"] = {"error": str(e)}

    try:
        from services import capability_router
        out["capabilities"] = capability_router.route_table(settings)
    except Exception as e:
        out["capabilities"] = {"error": str(e)}

    try:
        from services import demo_mode
        out["demo"] = demo_mode.demo_status(settings)
    except Exception as e:
        out["demo"] = {"error": str(e)}

    try:
        from agent_friday.routing.ollama_manager import get_manager
        base = (settings.get("model_routing") or {}).get("ollama_url") or "http://localhost:11434"
        mgr = get_manager(base)
        hw = mgr.detect_hardware()
        out["hardware"] = {**hw, "suggested_models": mgr.recommend_models(hw),
                           "ollama_available": mgr.is_available()}
    except Exception as e:
        out["hardware"] = {"error": str(e)}

    try:
        from services import credential_store
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
        from services import google_accounts
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

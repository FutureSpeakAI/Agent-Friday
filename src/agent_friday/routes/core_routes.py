import os
import io
import json
import glob
import subprocess
import base64
import secrets
import sys
import traceback
import uuid
import threading
import asyncio
import re
import html
import calendar
import time as _time
import hashlib as _hashlib
import hmac as _hmac
import queue as _queue
import difflib as _difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    CREATIONS_DIR,
    DECISION_BOM_FILE,
    DEFAULT_AGENT_PERSONALITY,
    FRIDAY_DIR,
    SERVER_START_TS,
    SETTINGS_FILE,
    _POPEN_FLAGS,
    _SETUP_MARKER,
    _is_existing_install,
    _load_agent_personality,
    _load_settings,
    _load_settings_raw,
    _network_status,
    _offline_queue_add,
    _offline_queue_list,
    _offline_queue_remove,
    _ollama_available,
    _save_agent_personality,
    _save_settings,
)  # noqa: E501
from agent_friday.services.agent import (
    TOOL_RINGS,
    _MCP_MANAGER,
    _MCP_TOOL_MAP,
    _load_mcp_servers,
    _mcp_register_server_tools,
    _mcp_reload,
    _mcp_unregister_server_tools,
    _save_mcp_servers,
)  # noqa: E501
from agent_friday.services.misc_engine import (
    _spawn_draft_task,
)  # noqa: E501
from agent_friday.services.notifications import (
    _flush_offline_queue,
)  # noqa: E501

core_bp = Blueprint('core_routes', __name__)



# ═══════════════════════════════════════════════════════════════
#  SERVE UI
# ═══════════════════════════════════════════════════════════════

@core_bp.route('/')
def serve_ui():
    """Serve the main UI, injecting the ephemeral per-startup API token into HTML.

    The token is placed in window.__FRIDAY_API_TOKEN so JavaScript can attach it
    to every API request as the X-Friday-Token header.  It lives only in browser
    JS memory and rotates on each server restart — never persisted to disk.
    """
    try:
        with open('index.html', encoding='utf-8') as _f:
            _html = _f.read()
        _token_script = (
            f'<script>window.__FRIDAY_API_TOKEN="{core._current_api_token()}";</script>'  # pragma: allowlist secret
        )
        # Inject early in <head> so the token is available before any fetch calls.
        _html = _html.replace('<head>', f'<head>\n{_token_script}', 1)
        return Response(_html, content_type='text/html')
    except FileNotFoundError:
        return "index.html not found — run: python -m agent_friday.ui.build_ui", 404


@core_bp.route('/static/<path:filename>')
def serve_static_asset(filename):
    # Relative send_from_directory paths resolve against Flask's root_path
    # (inside the package) — not the process cwd that serve_ui's
    # open('index.html') uses — so this route 404'd everything. Anchor to cwd.
    return send_from_directory(os.path.abspath('static'), filename)


@core_bp.route('/assets/<path:filename>')
def serve_asset(filename):
    # The dock loads its workspace icons from assets/icons/<id>.svg; without
    # this route they 404'd and the emoji fallback always showed instead of
    # the designed icon set.
    return send_from_directory(os.path.abspath('assets'), filename)


@core_bp.route('/favicon.ico')
def serve_favicon():
    return send_from_directory(os.path.abspath('static'), 'favicon.ico',
                               mimetype='image/x-icon')


@core_bp.route('/friday-live')
@core_bp.route('/friday-live/')
def serve_friday_live():
    # Same root_path-vs-cwd trap as /static above: a relative directory
    # resolves against src/agent_friday/core/, not the repo root where the
    # Friday Live PWA files live. Anchor to cwd like the other asset routes.
    return send_from_directory(os.path.abspath('.'), 'friday_live.html')


@core_bp.route('/friday-live/manifest.json')
def serve_friday_live_manifest():
    return send_from_directory(os.path.abspath('.'), 'friday_live_manifest.json', mimetype='application/manifest+json')


@core_bp.route('/friday-live/sw.js')
def serve_friday_live_sw():
    resp = send_from_directory(os.path.abspath('.'), 'friday_live_sw.js', mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/friday-live/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@core_bp.route('/api/health')
def friday_health():
    """Return server uptime and system health snapshot for the demo UI."""
    uptime_s = int(_time.time() - SERVER_START_TS)
    creations_today = 0
    if CREATIONS_DIR.exists():
        today = date.today().isoformat()
        for f in CREATIONS_DIR.iterdir():
            try:
                if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime).date().isoformat() == today:
                    creations_today += 1
            except Exception:
                pass
    settings = _load_settings()
    # `active` is CONFIGURATION (a key is present), never health — decision D1.
    # The health verdict comes from _inference below, which proves inference.
    models = [
        {"name": "Claude Opus 5", "active": bool(core.ANTHROPIC_API_KEY)},
        {"name": "Gemini",     "active": bool(core.GEMINI_API_KEY)},
    ]
    ring_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for r in TOOL_RINGS.values():
        ring_counts[r] = ring_counts.get(r, 0) + 1

    _vault_state = core._VAULT_ENCRYPTION_STATE
    _vault_warning = _vault_state.get("warning") or _vault_state.get("error") or ""

    # -- About-panel enrichment (all fail-soft; the UI renders an em-dash for
    # missing values). These keys were referenced by Settings->About since
    # v4.x but never actually served, so Mood / Memory Entries / Vault Entries
    # rendered permanently empty.
    # Source checkouts: pyproject.toml wins (installed egg-info can be stale
    # for editable installs). Frozen/pip installs fall back to package metadata.
    _app_version = None
    try:
        import tomllib as _tomllib
        _pp = Path(__file__).resolve().parents[3] / "pyproject.toml"
        if _pp.exists():
            with _pp.open("rb") as _ppf:
                _app_version = _tomllib.load(_ppf).get("project", {}).get("version")
    except Exception:
        _app_version = None
    if not _app_version:
        try:
            from importlib.metadata import version as _pkg_version
            _app_version = _pkg_version("agent-friday")
        except Exception:
            _app_version = "5.0.0"
    _mood = None
    try:
        from agent_friday.services.model_router import _get_emotional_arc
        _mood = (_get_emotional_arc().state() or {}).get("mood")
    except Exception:
        pass
    _memory_entries = None
    try:
        _mem_dir = FRIDAY_DIR / "memory"
        if _mem_dir.exists():
            _memory_entries = sum(1 for _f in _mem_dir.rglob("*.json"))
    except Exception:
        pass
    _vault_count = None
    try:
        _vault_dir = FRIDAY_DIR / "vault"
        if _vault_dir.exists():
            _vault_count = sum(1 for _p in _vault_dir.iterdir()
                               if _p.is_file() and not _p.name.startswith("."))
    except Exception:
        pass
    # ── Health verdict (decision D1) ──────────────────────────────────────
    # This field used to be the literal "ok", so /api/health could not report
    # failure: a revoked key, a stopped Ollama daemon or an out-of-credit
    # account all read as healthy. It is now derived from a real one-token
    # generation (cached ~60s inside provider_health, because the tray
    # watchdog polls this endpoint).
    #
    # The HTTP status stays 200 even when status == "down": the tray treats a
    # non-2xx as "server is dead" and would restart a server that is in fact
    # running fine with an unreachable model backend. Liveness (HTTP 200) and
    # inference health (this field) are deliberately different signals.
    _inference = {"status": "unknown", "providers": []}
    try:
        from agent_friday.services.provider_health import inference_health
        _inference = inference_health()
    except Exception as _e:
        _inference = {"status": "unknown", "providers": [],
                      "detail": str(_e)[:120]}
    _status = _inference.get("status") or "unknown"

    return jsonify({
        "status": _status,
        "inference": _inference,
        "configuration": {
            "anthropic_key": bool(core.ANTHROPIC_API_KEY),
            "gemini_key": bool(core.GEMINI_API_KEY),
        },
        "version": _app_version,
        "mood": _mood,
        "memory_entries": _memory_entries,
        "vault_count": _vault_count,
        "uptime_seconds": uptime_s,
        "server_start": datetime.fromtimestamp(SERVER_START_TS).isoformat(),
        "creations_today": creations_today,
        "models": models,
        "agent_name": settings.get("agent_name", "AGENT FRIDAY"),
        "orchestrator_model": settings.get("orchestrator_model", "claude-opus-5"),
        "subagent_model": settings.get("subagent_model", "claude-sonnet-5"),
        "creative_model": settings.get("creative_model", "gemini-nano-banana-2"),
        "voice_model": settings.get("voice_model", "gemini-2.5-flash-native-audio-latest"),
        "vault": {
            "encryption_enabled": _vault_state.get("enabled", False),
            "warning": _vault_warning,
        },
        "governance": {
            "enabled": True,
            "version": "v4.4",
            "policy": "cLaws",
            "decision_bom": str(DECISION_BOM_FILE),
            "ring_permissions": {
                "ring_0_read": "always_allowed",
                "ring_1_write": "always_allowed",
                "ring_2_network": "requires_auth",
                "ring_3_full": "requires_cc_permission",
            },
            "tool_counts_by_ring": {
                f"ring_{k}": v for k, v in sorted(ring_counts.items())
            },
        },
    })


# ═══════════════════════════════════════════════════════════════
#  MODEL CATALOG  — single source of truth for the model picker
# ═══════════════════════════════════════════════════════════════

@core_bp.route('/api/models')
def list_models():
    """Return the available-model catalog grouped by UI role.

    Drives every model selector in the UI (orchestrator / subagent / creative /
    voice). Built from the declarative ProviderRegistry + live Ollama detection,
    so adding a provider or model on the backend surfaces it here with zero UI
    changes. Each entry carries availability (is the provider's key present?) so
    the UI can show—but disable—models the user hasn't configured yet.
    """
    try:
        from agent_friday.services.model_catalog import build_catalog
        cat = build_catalog()
        settings = _load_settings()
        # Catalog freshness per hosted/discovery provider — lets the UI say
        # "catalog stale, showing cached" honestly (spec A2). stale=True when
        # older than 24h or never fetched.
        try:
            from agent_friday.services.hosted_catalog import catalog_meta
            cat_meta = catalog_meta()
            # Stale-while-REVALIDATE, with the revalidate part actually wired.
            # This endpoint reported anthropic as stale/never-fetched for its
            # whole life while POST /api/models/refresh sat one screen away,
            # working, waiting to be called (verified 2026-08-18: the picker
            # served the hardcoded fallback list; one manual refresh returned
            # 10 live models). Reporting a problem is not handling it — a
            # stale catalog now kicks its own background refresh, throttled so
            # UI polling cannot hammer the provider.
            _kick_stale_catalog_refresh(cat_meta)
        except Exception:
            cat_meta = {}
        return jsonify({
            "status": "ok",
            "roles": cat["roles"],
            "models": cat["models"],
            "providers": cat["providers"],
            "voice_engines": cat.get("voice_engines", []),
            "catalog_meta": cat_meta,
            "selected": {
                "orchestrator_model": settings.get("orchestrator_model"),
                "subagent_model": settings.get("subagent_model"),
                "creative_model": settings.get("creative_model"),
                # Video has no flat mirror — creative_model IS creative_image
                # (_CAP_FLAT_MAP); the video pick lives only in routing.
                "creative_video_model": ((settings.get("capability_routing")
                                          or {}).get("creative_video")
                                         or {}).get("model"),
                "voice_model": settings.get("voice_model"),
                "voice_engine": settings.get("voice_engine"),
            },
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e),
                        "roles": {}, "models": [], "providers": [],
                        "voice_engines": [], "catalog_meta": {}}), 200


_CATALOG_KICK_AT = {}  # provider -> monotonic seconds of last background kick


def _kick_stale_catalog_refresh(cat_meta):
    """Background-refresh any stale hosted catalog, at most once per 10 min.

    Fire-and-forget on a daemon thread: the /api/models response that noticed
    the staleness returns immediately with the cached list, and the NEXT
    request gets the live one. A refresh failure changes nothing — refresh()
    is stale-while-revalidate and never clobbers a good cache.
    """
    import threading
    import time as _t
    for provider, meta in (cat_meta or {}).items():
        if not (meta or {}).get("stale"):
            continue
        now = _t.monotonic()
        if now - _CATALOG_KICK_AT.get(provider, -1e9) < 600:
            continue
        _CATALOG_KICK_AT[provider] = now

        def _run(name=provider):
            try:
                from agent_friday.services.hosted_catalog import refresh
                result = refresh(name)
                print(f"  [CATALOG] background refresh {name}: "
                      f"{result.get('status')} ({result.get('count', 0)} models)")
            except Exception as e:
                print(f"  [CATALOG] background refresh {name} failed: {e}")

        threading.Thread(target=_run, daemon=True,
                         name=f"catalog-refresh-{provider}").start()


@core_bp.route('/api/models/refresh', methods=['POST'])
def refresh_models_catalog():
    """Force a live hosted-catalog fetch into the discovery cache (spec A2).

    Anthropic /v1/models and/or OpenRouter /models — body
    {"provider": "anthropic"|"openrouter"} for one, empty/omitted for all.
    Per-provider result: {status: refreshed|no_key|error, count, fetched_at}.
    A no_key/error result never clobbers an existing cache
    (stale-while-revalidate), so the picker keeps its last good list.
    """
    try:
        from agent_friday.services.hosted_catalog import (
            HOSTED_PROVIDERS, refresh, refresh_all)
        body = request.get_json(silent=True) or {}
        provider = str(body.get("provider") or "").strip().lower()
        if provider:
            if provider not in HOSTED_PROVIDERS:
                return jsonify({
                    "status": "error",
                    "message": f"unknown provider '{provider}' — one of: "
                               f"{', '.join(HOSTED_PROVIDERS)}"}), 400
            results = {provider: refresh(provider)}
        else:
            results = refresh_all()
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  SYSTEM INFO
# ═══════════════════════════════════════════════════════════════

@core_bp.route('/api/system')
def system_info():
    """Get real system info via PowerShell."""
    try:
        # Disk usage
        disk_cmd = 'Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{N="UsedGB";E={[math]::Round($_.Used/1GB,2)}},@{N="FreeGB";E={[math]::Round($_.Free/1GB,2)}},@{N="TotalGB";E={[math]::Round(($_.Used+$_.Free)/1GB,2)}} | ConvertTo-Json'
        disk_result = subprocess.run(['powershell', '-Command', disk_cmd], capture_output=True, text=True, timeout=10, creationflags=_POPEN_FLAGS)
        disks = json.loads(disk_result.stdout) if disk_result.stdout.strip() else []
        if isinstance(disks, dict):
            disks = [disks]

        # Top processes
        proc_cmd = 'Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 Name,@{N="CPU_s";E={[math]::Round($_.CPU,1)}},@{N="MemMB";E={[math]::Round($_.WorkingSet64/1MB,1)}} | ConvertTo-Json'
        proc_result = subprocess.run(['powershell', '-Command', proc_cmd], capture_output=True, text=True, timeout=10, creationflags=_POPEN_FLAGS)
        procs = json.loads(proc_result.stdout) if proc_result.stdout.strip() else []
        if isinstance(procs, dict):
            procs = [procs]

        return jsonify({"status": "ok", "disks": disks, "processes": procs})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@core_bp.route('/api/system/network-status')
def system_network_status():
    """Connectivity state from the network monitor.

    Returns {status: online|degraded|offline|unknown, since, last_online,
    latency_ms, host, offline_auto_local, ollama_available, queued}. Drives the
    top-bar offline badge, the scene desaturation, and tells the UI whether
    local inference is available as a fallback.
    """
    state = _network_status()
    settings = _load_settings_raw()
    return jsonify({
        "status": "ok",
        "network": state,
        "offline_auto_local": bool(settings.get("offline_auto_local", True)),
        "ollama_available": _ollama_available(),
        "queued": len(_offline_queue_list()),
        "active_routing_mode": (_load_settings().get("model_routing") or {}).get("mode", "cloud_only"),
    })


@core_bp.route('/api/system/offline-queue', methods=['GET', 'POST', 'DELETE'])
def system_offline_queue():
    """Inspect or manage the offline task queue.

    GET    → {items: [...], count}
    POST   → enqueue {kind, payload} (returns the entry)
    DELETE → ?id=<id> removes one; ?clear=1 empties the queue
    """
    if request.method == 'GET':
        items = _offline_queue_list()
        return jsonify({"status": "ok", "items": items, "count": len(items)})
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        kind = (data.get("kind") or "").strip()
        if not kind:
            return jsonify({"status": "error", "message": "kind required"}), 400
        entry = _offline_queue_add(kind, data.get("payload") or {},
                                   dedupe_key=data.get("dedupe_key"))
        return jsonify({"status": "ok", "entry": entry})
    # DELETE
    if request.args.get("clear"):
        for e in _offline_queue_list():
            _offline_queue_remove(e.get("id"))
        return jsonify({"status": "ok", "items": []})
    qid = (request.args.get("id") or "").strip()
    if not qid:
        return jsonify({"status": "error", "message": "id or clear=1 required"}), 400
    ok = _offline_queue_remove(qid)
    return jsonify({"status": "ok" if ok else "not_found", "id": qid})


@core_bp.route('/api/system/offline-queue/flush', methods=['POST'])
def system_offline_queue_flush():
    """Manually replay the offline queue now (normally fired on reconnect)."""
    try:
        result = _flush_offline_queue(reason="manual")
        return jsonify({"status": "ok", **result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/api/countdowns')
def get_countdowns():
    """Compute countdowns to upcoming recurring events.

    Events are defined by (month, day) and roll to their NEXT future occurrence,
    so an event that is today or already past this year is shown for next year
    rather than lingering at 0/negative days or silently vanishing from a
    hardcoded one-shot list. `days` is always >= 1 (strictly upcoming).
    """
    today = date.today()
    # (label, month, day, emoji) — recurring annual markers.
    events = [
        {"label": "Summer Solstice", "month": 6, "day": 21, "emoji": "☀️"},
        {"label": "Independence Day", "month": 7, "day": 4, "emoji": "🎆"},
        {"label": "New Year", "month": 1, "day": 1, "emoji": "🎉"},
    ]
    countdowns = []
    for ev in events:
        # This year's date; if it's today or already past, use next year's.
        occ = date(today.year, ev["month"], ev["day"])
        if (occ - today).days < 1:
            occ = date(today.year + 1, ev["month"], ev["day"])
        countdowns.append({
            "label": ev["label"], "date": occ.isoformat(),
            "emoji": ev["emoji"], "days": (occ - today).days,
        })
    return jsonify({"status": "ok", "countdowns": sorted(countdowns, key=lambda x: x["days"])})


# ═══════════════════════════════════════════════════════════════
#  JOB MANAGEMENT (placeholder)
# ═══════════════════════════════════════════════════════════════

@core_bp.route('/api/jobs/apply', methods=['POST'])
def apply_job():
    """Trigger LinkedIn Easy Apply (placeholder)."""
    data = request.get_json(silent=True) or {}
    return jsonify({"status": "placeholder", "message": f"Would apply to: {data.get('title', 'unknown')}"})


# ═══════════════════════════════════════════════════════════════
#  DRAFTING / COMPOSITION (placeholder)
# ═══════════════════════════════════════════════════════════════

@core_bp.route('/api/email/draft', methods=['POST'])
def draft_email():
    """Draft a Gmail reply (placeholder)."""
    return jsonify({"status": "placeholder", "draft": "Email drafting coming in Phase C"})


@core_bp.route('/api/setup/status')
def api_setup_status():
    initialized = _is_existing_install()
    # Auto-stamp the marker so future checks are instant
    if initialized and not _SETUP_MARKER.exists():
        try:
            _SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _SETUP_MARKER.write_text(datetime.now().isoformat(), encoding='utf-8')
        except Exception:
            pass
    return jsonify({"initialized": initialized})


@core_bp.route('/api/setup/skip', methods=['GET', 'POST'])
def api_setup_skip():
    """Permanently mark setup complete — for existing installs that predate the wizard."""
    try:
        _SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _SETUP_MARKER.write_text(datetime.now().isoformat(), encoding='utf-8')
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok"})


@core_bp.route('/api/setup/complete', methods=['POST'])
def api_setup_complete():
    """Persist wizard choices and mark setup complete.

    Accepts the classic fields (agent_name, *_model, tts_voice, …) AND the new
    onboarding payload (distribution, providers, capability_routing). API keys are
    stored ENCRYPTED via credential_store — never written to settings.json — and
    hot-reloaded so no restart is needed. The settings delta flows through
    _save_settings so capability_routing and the flat *_model keys stay congruent.
    """
    data = request.get_json(silent=True) or {}
    from agent_friday.services import credential_store as cs

    # 1) Provider API keys → encrypted store + live env. Accept both the legacy
    #    flat fields and a providers:{name:{api_key}} map from the new wizard.
    legacy_key_fields = {'anthropic_api_key': 'anthropic',
                         'gemini_api_key': 'google-gemini',
                         'openai_api_key': 'openai'}
    for field, pname in legacy_key_fields.items():
        val = (data.get(field) or '').strip()
        if val:
            cs.set_provider_key(pname, val)
            cs.hot_reload_provider_key(pname, val)
    providers_payload = data.get('providers') or {}
    for pname, pcfg in providers_payload.items():
        if isinstance(pcfg, dict):
            kv = (pcfg.get('api_key') or pcfg.get('key') or '').strip()
            if kv:
                cs.set_provider_key(pname, kv)
                cs.hot_reload_provider_key(pname, kv)

    # 1b) Vault passphrase (H4) → OS keychain, same slot friday vault-setup uses.
    #     Arms AES-256-GCM at-rest encryption for the sovereign vault. Optional;
    #     never written to a file. Live env is set too so this session encrypts
    #     without a restart. Never logged.
    vault_pass = (data.get('vault_passphrase') or '').strip()
    if vault_pass:
        try:
            import keyring as _keyring
            _keyring.set_password("agent-friday", "vault-passphrase", vault_pass)
        except Exception:
            pass  # keyring absent → fall through to env-only for this session
        os.environ["FRIDAY_VAULT_PASSPHRASE"] = vault_pass
        try:
            core.FRIDAY_VAULT_PASSPHRASE = vault_pass
        except Exception:
            pass

    # 2) Settings delta (NO secrets) → _save_settings keeps routing congruent.
    delta = {}
    for k in ('agent_name', 'orchestrator_model', 'subagent_model', 'creative_model',
              'music_model', 'minor_mode', 'daily_creation_free_choice',
              'voice_model', 'tts_voice', 'temperature', 'communication_style',
              'distribution', 'demo_mode', 'capability_routing'):
        if k in data:
            delta[k] = data[k]
    if providers_payload:
        # Persist provider CONFIG only — strip any secret that came in the payload.
        delta['providers'] = {
            n: {kk: vv for kk, vv in (c or {}).items() if kk not in ('api_key', 'key')}
            for n, c in providers_payload.items() if isinstance(c, dict)
        }
    delta['setup_complete'] = True

    # 3) Apply the chosen distribution preset (workspaces / layout / personality).
    if data.get('distribution'):
        try:
            from agent_friday.services import distributions
            delta.update(distributions.apply_distro(data['distribution']))
        except Exception:
            pass

    try:
        _save_settings(delta)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    # 4) Preferred holographic scene → personality.json.
    if 'preferred_scene_index' in data:
        pfile = FRIDAY_DIR / 'personality.json'
        pdata = {}
        if pfile.exists():
            try:
                pdata = json.loads(pfile.read_text('utf-8'))
            except Exception:
                pass
        pdata['preferred_scene_index'] = int(data['preferred_scene_index'])
        try:
            pfile.write_text(json.dumps(pdata, indent=2), encoding='utf-8')
        except Exception:
            pass

    # 5) Stamp the setup-complete marker.
    try:
        _SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _SETUP_MARKER.write_text(datetime.now().isoformat(), encoding='utf-8')
    except Exception:
        pass
    return jsonify({"status": "ok"})


# ── Agent Settings endpoints ──────────────────────────────────
def _check_local_model_seat_gate(new_settings):
    """No-op. The seat gate was REMOVED on 2026-08-15 (Stephen's decision).

    This used to reject a `model_routing.local_model` save on two counts: a
    failed structural conformance gate, and a failed-or-missing honesty
    battery record. Both refused the save outright, so a model the user had
    explicitly chosen could not take the seat — and an ungated model was
    refused simply for never having been measured.

    That is the roadblock. Gating a user-selected model behind a homegrown
    eval is not standard practice, and the structural failures it fired on
    turned out to be a harness problem, not a model one: the same models
    scored 1/10 and 0/10 under the broken harness and 10/10 once it was
    fixed. The honesty record it refused `gemma4:26b` on contained eleven
    timeouts and one HTTP error — eleven empty answers, no model output at
    all.

    Kept as a function returning None so the call site and its tests stay
    honest about the fact that nothing is checked here any more, rather than
    the call quietly disappearing.
    """
    return None


@core_bp.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """GET: return current agent settings + personality.
    POST: merge new values into ~/.friday/settings.json and (optionally) save personality.
    """
    if request.method == 'GET':
        return jsonify({
            "status": "ok",
            "settings": _load_settings(),
            "personality": _load_agent_personality(),
            "default_personality": DEFAULT_AGENT_PERSONALITY,
        })
    try:
        data = request.get_json(silent=True) or {}
        new_settings = data.get('settings') or {}

        # FR-1 (toolcall-integrity-v5): a local model may only take Friday's
        # tool-using seat if it passes the structural conformance gate — see
        # services/model_seat_gate.py. Runs once per model (cached after);
        # a model that has never been gated is gated now, synchronously,
        # before the seat change is persisted. A red model is rejected, not
        # silently substituted — the recommendation is surfaced, the choice
        # stays the user's.
        seat_error = _check_local_model_seat_gate(new_settings)
        if seat_error is not None:
            return jsonify(seat_error), 400

        # Persist only the caller's delta — _save_settings re-merges with the
        # on-disk file. Spreading _load_settings() in here would risk persisting
        # the non-persistent offline routing overlay (mode=local_only).
        merged = _save_settings(new_settings)
        personality = data.get('personality')
        if personality is not None:
            # The personality store is free-text (GET returns it as a string), so
            # reject a non-string payload with a clean 400 instead of letting
            # _save_agent_personality().strip() raise an AttributeError → 500.
            if not isinstance(personality, str):
                return jsonify({"status": "error",
                                "message": "personality must be a string"}), 400
            _save_agent_personality(personality)
        return jsonify({
            "status": "ok",
            "settings": merged,
            "personality": _load_agent_personality(),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ── MCP server management API ────────────────────────────────────────────────
@core_bp.route('/api/mcp/status', methods=['GET'])
def api_mcp_status():
    """Live status of every configured MCP server + its discovered tools."""
    if _MCP_MANAGER is None:
        return jsonify({"status": "ok", "available": False, "servers": {}})
    try:
        return jsonify({
            "status": "ok",
            "available": True,
            "servers": _MCP_MANAGER.status(),
            "registered_tools": sorted(_MCP_TOOL_MAP.keys()),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/api/mcp/servers', methods=['GET', 'POST'])
def api_mcp_servers():
    """GET: the raw mcp_servers.json config.
    POST: replace it ({"servers": {...}}) and hot-reload the manager."""
    if request.method == 'GET':
        return jsonify({"status": "ok", "config": _load_mcp_servers()})
    try:
        data = request.get_json(silent=True) or {}
        cfg = data.get("config") or data
        if "servers" not in cfg:
            cfg = {"servers": cfg.get("servers", {})}
        if not isinstance(cfg.get("servers"), dict):
            return jsonify({"status": "error",
                            "message": "config.servers must be an object"}), 400
        _save_mcp_servers(cfg)
        reload_result = _mcp_reload()
        return jsonify({"status": "ok", "config": cfg, "reload": reload_result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/api/mcp/restart', methods=['POST'])
def api_mcp_restart():
    """Restart a single MCP server and re-register its tools."""
    if _MCP_MANAGER is None:
        return jsonify({"status": "error", "message": "MCP unavailable"}), 503
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"status": "error", "message": "name required"}), 400
        _mcp_unregister_server_tools(name)
        ok = _MCP_MANAGER.restart(name, on_ready=_mcp_register_server_tools)
        return jsonify({"status": "ok", "restarted": ok,
                        "agent_friday.server": _MCP_MANAGER.status().get(name, {})})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/api/mcp/authorize', methods=['POST'])
def api_mcp_authorize():
    """Start the OAuth 2.1 browser flow for a remote (url-based) MCP server.

    Body: {"name": "<server>", "open_browser": true?}. Returns the auth_url
    (also opened in the desktop browser by default); once the user approves,
    the server restarts and its tools register automatically.
    """
    import agent_friday.services.agent as _agent_svc  # live module state
    mgr = _agent_svc._MCP_MANAGER
    if mgr is None:
        return jsonify({"status": "error", "message": "MCP unavailable"}), 503
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"status": "error", "message": "name required"}), 400
        result = mgr.authorize(
            name,
            open_browser=bool(data.get("open_browser", True)),
            on_ready=_mcp_register_server_tools,
        )
        code = 200 if result.get("ok") else 400
        return jsonify({"status": "ok" if result.get("ok") else "error",
                        **result}), code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/api/mcp/authorize/status', methods=['GET'])
def api_mcp_authorize_status():
    """Poll an in-flight authorization: none|pending|done|error (+ auth_url)."""
    import agent_friday.services.agent as _agent_svc
    mgr = _agent_svc._MCP_MANAGER
    if mgr is None:
        return jsonify({"status": "error", "message": "MCP unavailable"}), 503
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"status": "error", "message": "name required"}), 400
    try:
        return jsonify({"status": "ok", **mgr.auth_status(name)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/api/mcp/reload', methods=['POST'])
def api_mcp_reload():
    """Reload the whole MCP config from disk and restart all servers."""
    try:
        result = _mcp_reload()
        return jsonify({"status": "ok", "reload": result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  MODEL ROUTING & OLLAMA STATUS ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@core_bp.route('/api/model-stats')
def model_stats():
    """Return model routing statistics (requests per model, estimated savings)."""
    try:
        from agent_friday.routing.model_router import get_router
        router = get_router()
        stats = router.get_stats()
        settings = _load_settings()
        routing_cfg = settings.get('model_routing') or {}
        stats['mode'] = routing_cfg.get('mode', 'cloud_only')
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e), "mode": "cloud_only",
                        "local_requests": 0, "cloud_requests": 0,
                        "estimated_savings": 0})


# ═══════════════════════════════════════════════════════════════
#  FILE ANALYSIS (Gemini)
# ═══════════════════════════════════════════════════════════════

@core_bp.route('/api/analyze', methods=['POST'])
def analyze_file():
    """Analyze an uploaded file using Gemini."""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    filename = file.filename
    content = file.read()

    try:
        from google import genai
        from google.genai import types
        from agent_friday.services import egress_gate as _eg
        client = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret

        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            # Image BYTES cannot be text-classified by the egress gate — this
            # is the documented caveat (H1/H2 in FABLE5_INTEGRATION_STORM_REPORT):
            # sending an uploaded image to Gemini vision is a conscious tradeoff,
            # not a silent leak. The instruction prompt is fixed (no user text).
            mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[
                    types.Part.from_bytes(data=content, mime_type=mime),
                    "You are Friday. Describe this image. If it looks like a job posting or resume, analyze it for key requirements and fit."
                ]
            )
            return jsonify({"filename": filename, "type": "image", "analysis": response.text})
        elif ext == 'pdf':
            try:
                import pdfplumber
                import io
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    text = '\n'.join(page.extract_text() or '' for page in pdf.pages[:10])
                if text.strip():
                    # Egress gate: the PDF's extracted text is user content going to
                    # a cloud provider (Gemini), which does not route through
                    # seal_outbound. Gate it fail-closed before it leaves the device.
                    _gated_pdf = _eg.gate_text(text[:8000], "gemini", "analyze_file.pdf")
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f'You are Friday. Summarize this PDF document concisely. If it looks like a job posting, evaluate the key requirements and note the role level.\n\n{_gated_pdf}'
                    )
                    return jsonify({"filename": filename, "type": "pdf", "analysis": response.text})
            except ImportError:
                pass
            return jsonify({"filename": filename, "type": "pdf", "analysis": f"PDF received ({len(content)//1024}KB). Install pdfplumber for full analysis: pip install pdfplumber"})
        elif ext in ('txt', 'md', 'py', 'js', 'html', 'css', 'json', 'ts', 'tsx', 'yaml', 'yml', 'toml'):
            text = content.decode('utf-8', errors='replace')[:8000]
            job_keywords = ['responsibilities', 'qualifications', 'salary', 'benefits', 'apply', 'experience required']
            is_job = sum(1 for kw in job_keywords if kw.lower() in text.lower()) >= 2
            # Egress gate: uploaded file text is user content bound for Gemini
            # (which bypasses seal_outbound). Gate fail-closed before it leaves.
            text = _eg.gate_text(text, "gemini", "analyze_file.text")
            if is_job:
                prompt = f'You are Friday. This looks like a job posting. Evaluate the key requirements, role level, and compensation signals. Rate attractiveness 1-10 and explain.\n\n{text}'
            else:
                prompt = f'You are Friday. Analyze this {ext} file and summarize its purpose and key content:\n\n{text}'
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            return jsonify({"filename": filename, "type": "text" if not is_job else "job_posting", "analysis": response.text})
        else:
            return jsonify({"filename": filename, "type": ext, "analysis": f"File received ({len(content)} bytes). Type: .{ext} — drop a text, image, or PDF for full analysis."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"filename": filename, "analysis": f"Analysis error: {str(e)}"})

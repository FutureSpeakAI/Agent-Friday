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
import core
from core import (
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
from services.agent import (
    TOOL_RINGS,
    _MCP_MANAGER,
    _MCP_TOOL_MAP,
    _load_mcp_servers,
    _mcp_register_server_tools,
    _mcp_reload,
    _mcp_unregister_server_tools,
    _save_mcp_servers,
)  # noqa: E501
from services.misc_engine import (
    _spawn_draft_task,
)  # noqa: E501
from services.notifications import (
    _flush_offline_queue,
)  # noqa: E501

core_bp = Blueprint('core_routes', __name__)



# ═══════════════════════════════════════════════════════════════
#  SERVE UI
# ═══════════════════════════════════════════════════════════════

@core_bp.route('/')
def serve_ui():
    return send_from_directory('.', 'index.html')


@core_bp.route('/static/<path:filename>')
def serve_static_asset(filename):
    return send_from_directory('static', filename)


@core_bp.route('/favicon.ico')
def serve_favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/x-icon')


@core_bp.route('/friday-live')
@core_bp.route('/friday-live/')
def serve_friday_live():
    return send_from_directory('.', 'friday_live.html')


@core_bp.route('/friday-live/manifest.json')
def serve_friday_live_manifest():
    return send_from_directory('.', 'friday_live_manifest.json', mimetype='application/manifest+json')


@core_bp.route('/friday-live/sw.js')
def serve_friday_live_sw():
    resp = send_from_directory('.', 'friday_live_sw.js', mimetype='application/javascript')
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
    models = [
        {"name": "Claude Opus 4.8", "active": bool(core.ANTHROPIC_API_KEY)},
        {"name": "Gemini",     "active": bool(core.GEMINI_API_KEY)},
    ]
    ring_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for r in TOOL_RINGS.values():
        ring_counts[r] = ring_counts.get(r, 0) + 1

    return jsonify({
        "status": "ok",
        "uptime_seconds": uptime_s,
        "server_start": datetime.fromtimestamp(SERVER_START_TS).isoformat(),
        "creations_today": creations_today,
        "models": models,
        "agent_name": settings.get("agent_name", "AGENT FRIDAY"),
        "orchestrator_model": settings.get("orchestrator_model", "claude-opus-4-8"),
        "subagent_model": settings.get("subagent_model", "claude-sonnet-4-6"),
        "creative_model": settings.get("creative_model", "gemini-nano-banana-2"),
        "voice_model": settings.get("voice_model", "gemini-3.1-flash-live-preview"),
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
        from services.model_catalog import build_catalog
        cat = build_catalog()
        settings = _load_settings()
        return jsonify({
            "status": "ok",
            "roles": cat["roles"],
            "models": cat["models"],
            "providers": cat["providers"],
            "selected": {
                "orchestrator_model": settings.get("orchestrator_model"),
                "subagent_model": settings.get("subagent_model"),
                "creative_model": settings.get("creative_model"),
                "voice_model": settings.get("voice_model"),
            },
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e),
                        "roles": {}, "models": [], "providers": []}), 200


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
    from services import credential_store as cs

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
            from services import distributions
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
                        "server": _MCP_MANAGER.status().get(name, {})})
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
        from model_router import get_router
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
        client = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret

        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
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
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f'You are Friday. Summarize this PDF document concisely. If it looks like a job posting, evaluate the key requirements and note the role level.\n\n{text[:8000]}'
                    )
                    return jsonify({"filename": filename, "type": "pdf", "analysis": response.text})
            except ImportError:
                pass
            return jsonify({"filename": filename, "type": "pdf", "analysis": f"PDF received ({len(content)//1024}KB). Install pdfplumber for full analysis: pip install pdfplumber"})
        elif ext in ('txt', 'md', 'py', 'js', 'html', 'css', 'json', 'ts', 'tsx', 'yaml', 'yml', 'toml'):
            text = content.decode('utf-8', errors='replace')[:8000]
            job_keywords = ['responsibilities', 'qualifications', 'salary', 'benefits', 'apply', 'experience required']
            is_job = sum(1 for kw in job_keywords if kw.lower() in text.lower()) >= 2
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

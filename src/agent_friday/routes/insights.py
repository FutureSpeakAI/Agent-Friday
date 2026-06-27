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
    FRIDAY_DIR,
    HOME,
    JOB_SEARCH_FILE,
    WIKI_PROFESSIONAL_DIR,
    _HAS_BEHAVIORAL_MONITOR,
    _HAS_COGMEM,
    _HAS_DYNRINGS,
    _HAS_INTEGRITY,
    get_behavioral_monitor,
    get_cognitive_memory,
    get_integrity_engine,
    get_privilege_manager,
    login_required,
)  # noqa: E501
from agent_friday.services.agent import (
    TOOL_RINGS,
    _get_governance_key,
)  # noqa: E501
from agent_friday.services.model_router import (
    _get_conversation_memory,
    _get_emotional_arc,
    _get_vault_control,
    _latest_session_summary,
)  # noqa: E501

insights_bp = Blueprint('insights', __name__)



# ═══════════════════════════════════════════════════════════════
#  LIVE DATA ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@insights_bp.route('/api/career-ops/tracker')
def career_tracker():
    candidates = [
        WIKI_PROFESSIONAL_DIR / 'application-log.md',
        HOME / 'Projects' / 'career-ops' / 'data' / 'applications.md',
    ]
    tracker_path = next((p for p in candidates if p.is_file()), None)
    if tracker_path:
        content = tracker_path.read_text(encoding='utf-8')
        lines = content.strip().split('\n')
        entries = []
        for line in lines:
            if line.startswith('|') and '---' not in line and not any(h in line.lower() for h in ['company','score','#']):
                cols = [c.strip() for c in line.split('|')[1:-1]]
                if len(cols) >= 3:
                    entries.append({'raw': cols, 'company': cols[0], 'score': cols[1] if len(cols)>1 else '', 'status': cols[2] if len(cols)>2 else ''})
        return jsonify({'status': 'ok', 'entries': entries, 'total': len(entries), 'raw': content, 'source': str(tracker_path)})
    return jsonify({'status': 'no_tracker', 'entries': [], 'total': 0, 'raw': ''})

@insights_bp.route('/api/career-ops/pipeline')
def career_pipeline():
    candidates = [
        WIKI_PROFESSIONAL_DIR / 'job-search.md',
        HOME / 'Projects' / 'career-ops' / 'data' / 'pipeline.md',
    ]
    pipe_path = next((p for p in candidates if p.is_file()), None)
    if pipe_path:
        return jsonify({'status': 'ok', 'content': pipe_path.read_text(encoding='utf-8'), 'source': str(pipe_path)})
    return jsonify({'status': 'empty', 'content': ''})

@insights_bp.route('/api/career-ops/reports')
def career_reports():
    reports = []
    seen = set()
    # wiki/professional/ is primary — collect all .md files there
    if WIKI_PROFESSIONAL_DIR.is_dir():
        for f in sorted(WIKI_PROFESSIONAL_DIR.iterdir(), reverse=True):
            if f.suffix == '.md':
                reports.append({'name': f.name, 'size': f.stat().st_size, 'source': 'wiki'})
                seen.add(f.name)
    # career-ops/reports/ is fallback — add any files not already in wiki
    fallback_dir = HOME / 'Projects' / 'career-ops' / 'reports'
    if fallback_dir.is_dir():
        for f in sorted(fallback_dir.iterdir(), reverse=True):
            if f.suffix == '.md' and f.name not in seen:
                reports.append({'name': f.name, 'size': f.stat().st_size, 'source': 'career-ops'})
    if reports:
        return jsonify({'status': 'ok', 'reports': reports, 'total': len(reports)})
    return jsonify({'status': 'no_reports', 'reports': [], 'total': 0})

@insights_bp.route('/api/career-ops/report/<filename>')
def career_report(filename):
    candidates = [
        WIKI_PROFESSIONAL_DIR / filename,
        HOME / 'Projects' / 'career-ops' / 'reports' / filename,
    ]
    report_path = next((p for p in candidates if p.is_file()), None)
    if report_path:
        return jsonify({'status': 'ok', 'content': report_path.read_text(encoding='utf-8'), 'filename': filename, 'source': str(report_path)})
    return jsonify({'status': 'not_found'})

@insights_bp.route('/api/evolution', methods=['GET', 'POST'])
def get_evolution():
    """Return evolution day count and structure index based on first_launch in personality.json.
    POST with {preferred_scene_index: N} to pin a structure (null to clear and return to auto)."""
    from datetime import date as _date
    pfile = FRIDAY_DIR / "personality.json"
    data = {}
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding='utf-8'))
        except Exception:
            pass

    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        if 'preferred_scene_index' in body:
            val = body['preferred_scene_index']
            if val is None:
                data.pop('preferred_scene_index', None)
            else:
                data['preferred_scene_index'] = int(val)
            try:
                pfile.write_text(json.dumps(data, indent=2), encoding='utf-8')
            except Exception:
                pass
        return jsonify({'status': 'ok', 'preferred_scene_index': data.get('preferred_scene_index')})

    today = _date.today()
    first_launch_str = data.get('first_launch')
    if not first_launch_str:
        first_launch_str = today.isoformat()
        data['first_launch'] = first_launch_str
        try:
            pfile.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except Exception:
            pass
    try:
        first_launch = _date.fromisoformat(first_launch_str)
    except Exception:
        first_launch = today
    day_count = max(1, (today - first_launch).days + 1)
    names = [
        'GENESIS LATTICE', 'SACRED SPHERE', 'SHANNON NETWORK',
        'GEODESIC CATHEDRAL', 'LOVELACE ASTROLABE', 'VON NEUMANN TESSERACT',
        'DIRAC PROBABILITY', 'MANDELBROT SET', 'TURING MOBIUS',
        'OCEAN OF LIGHT', 'FIBONACCI NERVE', 'TRANSCENDENCE',
        'GIGA EARTH (REZ)'
    ]
    calendar_idx = ((day_count - 1) // 4) % len(names)
    preferred_idx = data.get('preferred_scene_index')
    idx = preferred_idx if preferred_idx is not None else calendar_idx
    return jsonify({
        'day': day_count,
        'structure': f'DAY {day_count}: {names[idx]}',
        'structure_index': idx,
        'calendar_index': calendar_idx,
        'preferred_scene_index': preferred_idx,
        'first_launch': first_launch_str
    })


@insights_bp.route('/api/jobs')
def get_jobs():
    """Parse job-search.md and return structured data."""
    if not JOB_SEARCH_FILE.exists():
        return jsonify({"status": "no_data", "jobs": [], "raw": ""})

    text = JOB_SEARCH_FILE.read_text(encoding='utf-8')

    roles = []
    current_role = None
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('### '):
            if current_role:
                roles.append(current_role)
            current_role = {'title': line[4:], 'details': '', 'status': 'identified'}
        elif current_role:
            if 'applied' in line.lower():
                current_role['status'] = 'applied'
            elif 'interview' in line.lower():
                current_role['status'] = 'interview'
            elif 'rejected' in line.lower() or 'closed' in line.lower():
                current_role['status'] = 'closed'
            current_role['details'] += line + '\n'
    if current_role:
        roles.append(current_role)

    return jsonify({"status": "ok", "jobs": roles, "raw": text})


@insights_bp.route('/api/security/behavioral-report')
def get_behavioral_report():
    """Latest behavioral anomaly analysis from the most recent agent loop."""
    if not _HAS_BEHAVIORAL_MONITOR:
        return jsonify({"status": "unavailable",
                        "message": "behavioral_monitor not loaded"}), 200
    try:
        return jsonify({"status": "ok", **get_behavioral_monitor().get_latest_report()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@insights_bp.route('/api/security/behavioral-history')
def get_behavioral_history():
    """Rolling 20-session behavioral summary, newest first."""
    if not _HAS_BEHAVIORAL_MONITOR:
        return jsonify({"status": "unavailable", "count": 0, "sessions": []}), 200
    try:
        return jsonify({"status": "ok", **get_behavioral_monitor().get_history_summary()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@insights_bp.route('/api/security/risk-score')
def get_behavioral_risk_score():
    """Current composite behavioral risk level."""
    if not _HAS_BEHAVIORAL_MONITOR:
        return jsonify({"status": "unavailable", "composite": 0.0,
                        "risk_level": "none"}), 200
    try:
        return jsonify({"status": "ok", **get_behavioral_monitor().get_risk_score()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@insights_bp.route('/api/memory/stats')
def get_memory_stats():
    """Return enriched memory tier counts.

    Includes the persistent ChromaDB conversation index (size, session count,
    date range) under the `conversations` key — the Source Production System's
    long-horizon memory.
    """
    mem_dir = FRIDAY_DIR / "memory"
    stats = {"working": 0, "episodic": 0, "semantic": 0, "total": 0,
             "episodes": 0, "last_consolidation": None}
    if mem_dir.exists():
        for f in mem_dir.rglob('*.json'):
            stats["total"] += 1
            name = f.stem.lower()
            if 'episode' in name:
                stats["episodic"] += 1
            elif 'semantic' in name or 'concept' in name:
                stats["semantic"] += 1
            else:
                stats["working"] += 1
    # Persistent conversation memory (ChromaDB) — index size + date range.
    try:
        stats["conversations"] = _get_conversation_memory().stats()
    except Exception as _cm_err:
        stats["conversations"] = {"available": False, "error": str(_cm_err)}
    # Accumulated cross-session emotional arc (local sentiment EMA → tone).
    try:
        stats["emotional_arc"] = _get_emotional_arc().state()
    except Exception as _ea_err:
        stats["emotional_arc"] = {"available": False, "error": str(_ea_err)}
    # End-of-day session summaries on disk + the most recent one.
    try:
        _latest_date, _latest_text = _latest_session_summary()
        _sum_dir = FRIDAY_DIR / "memory" / "session_summaries"
        stats["session_summaries"] = {
            "count": len(list(_sum_dir.glob("*.md"))) if _sum_dir.exists() else 0,
            "latest_date": _latest_date,
            "latest_excerpt": (_latest_text or "")[:240],
        }
    except Exception as _ss_err:
        stats["session_summaries"] = {"count": 0, "error": str(_ss_err)}
    return jsonify({"status": "ok", **stats})


# ═══════════════════════════════════════════════════════════════
#  ZERO-TRUST SECURITY ENDPOINTS (Builds 2-4)
# ═══════════════════════════════════════════════════════════════

# ── BUILD 2: Versioned Cognitive Memory ────────────────────────

@insights_bp.route('/api/memory/ledger')
@login_required
def api_memory_ledger():
    """Return the append-only memory ledger with hash-chain entries."""
    if not _HAS_COGMEM:
        return jsonify({"error": "cognitive_memory module not available"}), 501
    since = request.args.get('since', type=float)
    limit = request.args.get('limit', 200, type=int)
    cm = get_cognitive_memory()
    entries = cm.get_ledger(since=since, limit=limit)
    chain = cm.verify_chain()
    return jsonify({
        "status": "ok",
        "entries": entries,
        "chain_valid": chain["valid"],
        "chain_entries": chain["entries"],
        "chain_break_at": chain.get("break_at"),
    })


@insights_bp.route('/api/memory/rollback', methods=['POST'])
@login_required
def api_memory_rollback():
    """Roll back memory writes after a given timestamp."""
    if not _HAS_COGMEM:
        return jsonify({"error": "cognitive_memory module not available"}), 501
    data = request.get_json(silent=True) or {}
    timestamp = data.get('timestamp')
    if timestamp is None:
        return jsonify({"error": "timestamp required"}), 400
    try:
        timestamp = float(timestamp)
    except (TypeError, ValueError):
        return jsonify({"error": "timestamp must be a number"}), 400
    cm = get_cognitive_memory()
    result = cm.memory_rollback(timestamp)
    return jsonify({"status": "ok", **result})


@insights_bp.route('/api/memory/quarantine', methods=['POST'])
@login_required
def api_memory_quarantine():
    """Quarantine memories by source_id or specific key."""
    if not _HAS_COGMEM:
        return jsonify({"error": "cognitive_memory module not available"}), 501
    data = request.get_json(silent=True) or {}
    source_id = data.get('source_id')
    specific_key = data.get('key')
    reason = data.get('reason', 'manual_quarantine')
    if not source_id and not specific_key:
        return jsonify({"error": "source_id or key required"}), 400
    cm = get_cognitive_memory()
    result = cm.memory_quarantine(source_id=source_id, specific_key=specific_key, reason=reason)
    return jsonify({"status": "ok", **result})


@insights_bp.route('/api/memory/health')
@login_required
def api_memory_health():
    """Return cognitive memory health (counts, chain status)."""
    if not _HAS_COGMEM:
        return jsonify({"error": "cognitive_memory module not available"}), 501
    cm = get_cognitive_memory()
    return jsonify({"status": "ok", **cm.health()})


# ── Weekly self-improvement ────────────────────────────────────

@insights_bp.route('/api/self-improvement/latest')
@login_required
def api_self_improvement_latest():
    """Most recent weekly self-improvement report (or null), plus past week ids."""
    from agent_friday.services.introspection import (
        latest_self_improvement_report, list_self_improvement_reports)
    return jsonify({
        "status": "ok",
        "report": latest_self_improvement_report(),
        "weeks": list_self_improvement_reports(),
    })


@insights_bp.route('/api/self-improvement/run', methods=['POST'])
@login_required
def api_self_improvement_run():
    """Kick off a self-improvement report now (manual trigger, any weekday).

    Runs in a background thread and returns immediately — the work surfaces as a
    floating process orb (see generate_self_improvement_report), and the report
    is readable via /api/self-improvement/latest once the orb completes. Uses the
    same reflection + notification path as the scheduled Sunday job.

    Pass {"wait": true} to block until the report is ready and return it inline
    (used by tests and any caller that wants the result synchronously).
    """
    from agent_friday.services.introspection import generate_self_improvement_report
    from agent_friday.services.notifications import _self_reflect, _notify_self_improvement
    data = request.get_json(silent=True) or {}
    try:
        limit = int(data.get('limit', 30))
    except (TypeError, ValueError):
        limit = 30

    def _run():
        report = generate_self_improvement_report(limit=limit, reflect=_self_reflect)
        _notify_self_improvement(report, manual=True)
        return report

    if data.get('wait'):
        return jsonify({"status": "ok", "report": _run()})

    def _bg():
        try:
            _run()
        except Exception as e:
            print(f"  [self-improvement] manual run failed: {e}")
    threading.Thread(target=_bg, name="self-improvement-run", daemon=True).start()
    return jsonify({"status": "started",
                    "message": "Self-review started — watch the orb."})


# ── BUILD 3: Dynamic Privilege Rings ───────────────────────────

@insights_bp.route('/api/governance/privilege-log')
@login_required
def api_governance_privilege_log():
    """Return the privilege elevation/consumption log."""
    if not _HAS_DYNRINGS:
        return jsonify({"error": "dynamic_rings module not available"}), 501
    since = request.args.get('since', type=float)
    limit = request.args.get('limit', 200, type=int)
    pm = get_privilege_manager(
        log_path=FRIDAY_DIR / "vault" / "privilege-log.jsonl",
        governance_key_fn=_get_governance_key,
    )
    entries = pm.get_privilege_log(since=since, limit=limit)
    return jsonify({"status": "ok", "entries": entries, "count": len(entries)})


@insights_bp.route('/api/governance/elevate', methods=['POST'])
@login_required
def api_governance_elevate():
    """Request privilege elevation for a tool (Ring 3 requires user confirm)."""
    if not _HAS_DYNRINGS:
        return jsonify({"error": "dynamic_rings module not available"}), 501
    data = request.get_json(silent=True) or {}
    ring = data.get('ring', 0)
    reason = data.get('reason', '')
    tool = data.get('tool', '')
    task_id = data.get('task_id', 'manual')
    user_confirmed = data.get('user_confirmed', False)
    if not tool:
        return jsonify({"error": "tool name required"}), 400
    pm = get_privilege_manager(
        log_path=FRIDAY_DIR / "vault" / "privilege-log.jsonl",
        governance_key_fn=_get_governance_key,
    )
    entry = pm.governance_elevate(ring, reason, tool,
                                   task_id=task_id,
                                   user_confirmed=user_confirmed)
    return jsonify({"status": "ok", **entry})


# ── BUILD 4: AI Bill of Integrity ──────────────────────────────

@insights_bp.route('/api/integrity')
@login_required
def api_integrity():
    """Generate and return a signed integrity manifest."""
    if not _HAS_INTEGRITY:
        return jsonify({"error": "proof_of_integrity module not available"}), 501
    engine = get_integrity_engine(
        friday_dir=FRIDAY_DIR,
        governance_key_fn=_get_governance_key,
    )
    # Gather live data for the manifest
    tool_manifest = [{"name": t, "ring": r} for t, r in TOOL_RINGS.items()]
    vault_status = {}
    try:
        vac = _get_vault_control()
        if vac:
            vault_status = vac.stats()
    except Exception:
        pass
    manifest = engine.sign_manifest(
        tool_manifest=tool_manifest,
        vault_status=vault_status,
    )
    return jsonify({"status": "ok", "manifest": manifest.to_dict()})


@insights_bp.route('/api/integrity/verify', methods=['POST'])
@login_required
def api_integrity_verify():
    """Verify a submitted integrity manifest."""
    if not _HAS_INTEGRITY:
        return jsonify({"error": "proof_of_integrity module not available"}), 501
    data = request.get_json(silent=True) or {}
    manifest_dict = data.get('manifest')
    if not manifest_dict:
        return jsonify({"error": "manifest object required"}), 400
    engine = get_integrity_engine(
        friday_dir=FRIDAY_DIR,
        governance_key_fn=_get_governance_key,
    )
    result = engine.verify_manifest(manifest_dict)
    return jsonify({"status": "ok", **result})

# NOTE: The behavioral-report / behavioral-history / risk-score routes are
# defined once, above (the canonical, test-covered handlers). A second
# @login_required copy used to live here but was dead code — Flask routes to
# the first-registered rule, so the duplicate never executed. Removed to keep
# one source of truth per endpoint.

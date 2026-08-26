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
    _load_settings,
    _log_context,
    login_required,
)  # noqa: E501
from agent_friday.services.agent import (
    _CC_KILL,
    _CC_PERMISSION,
    _HAS_PYAUTOGUI,
    _cc_persist,
    _pag,
)  # noqa: E501

control_bp = Blueprint('control', __name__)



# ── Computer Control API ─────────────────────────────────────────

@control_bp.route('/api/control/permission', methods=['GET', 'POST'])
@login_required
def cc_permission():
    """GET: return current CC state. POST {action:'grant'|'revoke'}: change it."""
    if request.method == 'GET':
        return jsonify({
            "granted": _CC_PERMISSION.is_set(),
            "killed": _CC_KILL.is_set(),
            "available": _HAS_PYAUTOGUI,
        })
    data = request.get_json(force=True, silent=True) or {}
    action = data.get('action', '')
    if action == 'grant':
        # Experimental opt-in gate: refuse unless the feature is enabled in
        # Settings → Experimental (defaults OFF for new users).
        try:
            _cc_enabled = bool(_load_settings().get('computer_control_enabled', False))
        except Exception:
            _cc_enabled = False
        if not _cc_enabled:
            return jsonify({
                "granted": False,
                "error": "Computer Control is disabled. Enable it under "
                         "Settings → Privacy & Security → Computer Control first.",
            }), 403
        _CC_KILL.clear()
        _CC_PERMISSION.set()
        _cc_persist(True)
        _log_context("cc_action", {"action": "permission_granted"})
        return jsonify({"granted": True, "killed": False})
    if action == 'revoke':
        _CC_PERMISSION.clear()
        _cc_persist(False)
        _log_context("cc_action", {"action": "permission_revoked"})
        return jsonify({"granted": False, "killed": _CC_KILL.is_set()})
    return jsonify({"error": "action must be 'grant' or 'revoke'"}), 400


@control_bp.route('/api/control/kill', methods=['POST'])
@login_required
def cc_kill():
    """Emergency kill switch — immediately stops all computer control."""
    _CC_PERMISSION.clear()
    _CC_KILL.set()
    _cc_persist(False)
    if _HAS_PYAUTOGUI:
        try:
            _pag.moveTo(0, 0, duration=0.1)
        except Exception:
            pass
    _log_context("cc_action", {"action": "kill_switch_activated"})
    return jsonify({"killed": True, "message": "Computer control terminated. All permissions revoked."})


# ── File permissions (WO-17) ────────────────────────────────────────────────
#
# Authenticated HTTP endpoints only — there is no grant tool in CLAUDE_TOOLS,
# so no surface's model can reach any of this. The dialog's contents (scan
# findings) come from file_grants.scan_path()'s own classifier run, never
# from model text, so a prompt-injected file cannot script its own consent
# screen. Voice can only say a chip appeared; approving one happens here.

@control_bp.route('/api/privacy/file-grants', methods=['GET'])
@login_required
def list_file_grants():
    from agent_friday.services import file_grants as _fg
    return jsonify({
        "grants": _fg.list_grants(),
        "denies": _fg.list_denies(),
        "pending_reapproval": _fg.list_pending_reapproval(),
        "status": _fg.status(),
    })


@control_bp.route('/api/privacy/file-grants/scan', methods=['GET'])
@login_required
def scan_file_grant_target():
    """Classifier findings for a candidate path — what the consent dialog
    renders before the button. Read-only; creates nothing."""
    path = request.args.get('path', '')
    if not path:
        return jsonify({"error": "path is required"}), 400
    from pathlib import Path as _P
    from agent_friday.services import file_grants as _fg
    try:
        p = _P(path).expanduser().resolve()
    except Exception as e:
        return jsonify({"error": f"invalid path: {e}"}), 400
    if not p.exists() or not p.is_file():
        return jsonify({"error": f"{p} does not exist or is not a file"}), 404
    return jsonify(_fg.scan_path(p))


@control_bp.route('/api/privacy/file-grants', methods=['POST'])
@login_required
def create_file_grant():
    """Create a grant. body: {path, scope: 'file'|'folder'|'glob',
    never_send_override?: bool, ack_never_send_matches?: [str],
    expiry_days? (required for folder/glob, max 30)}."""
    data = request.get_json(force=True, silent=True) or {}
    path = (data.get('path') or '').strip()
    scope = (data.get('scope') or 'file').strip().lower()
    if not path:
        return jsonify({"error": "path is required"}), 400
    from agent_friday.services import file_grants as _fg
    try:
        if scope == 'file':
            event = _fg.create_file_grant(
                path,
                never_send_override=bool(data.get('never_send_override')),
                ack_never_send_matches=data.get('ack_never_send_matches') or [],
            )
        elif scope in ('folder', 'glob'):
            event = _fg.create_scope_grant(
                path, scope, float(data.get('expiry_days') or 0))
        else:
            return jsonify({"error": "scope must be 'file', 'folder', or 'glob'"}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _log_context("file_grant_created", {"id": event.get("id"), "type": event.get("type")})
    return jsonify({"status": "ok", "grant": event})


@control_bp.route('/api/privacy/deny-marks', methods=['POST'])
@login_required
def create_deny_mark():
    """body: {path, scope: 'file'|'folder'|'glob'}."""
    data = request.get_json(force=True, silent=True) or {}
    path = (data.get('path') or '').strip()
    scope = (data.get('scope') or 'file').strip().lower()
    if not path:
        return jsonify({"error": "path is required"}), 400
    from agent_friday.services import file_grants as _fg
    try:
        event = _fg.create_deny_mark(path, scope)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _log_context("file_deny_mark_created", {"id": event.get("id"), "type": event.get("type")})
    return jsonify({"status": "ok", "deny": event})


@control_bp.route('/api/privacy/file-grants/<grant_id>/revoke', methods=['POST'])
@login_required
def revoke_file_grant(grant_id):
    """Revokes a grant OR a deny mark by id — same action, either registry."""
    from agent_friday.services import file_grants as _fg
    event = _fg.revoke(grant_id)
    _log_context("file_grant_revoked", {"target_id": grant_id})
    return jsonify({"status": "ok", "revoke": event})

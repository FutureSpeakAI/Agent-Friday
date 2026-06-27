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
                         "Settings → Account & Security → Computer Control first.",
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

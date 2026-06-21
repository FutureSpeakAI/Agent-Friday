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
    PROCESSES,
    PROCESSES_LOCK,
    login_required,
)  # noqa: E501
from services.agent import (
    TASKS,
    TASKS_LOCK,
    _FOLLOW_UP_LOCK,
    _FOLLOW_UP_QUEUES,
    _task_snapshot,
)  # noqa: E501

tasks_bp = Blueprint('tasks', __name__)



# ── Task Tray HTTP endpoints (consumed by the frontend TaskTray) ──
@tasks_bp.route('/api/tasks')
def list_tasks():
    """Tasks for the frontend TaskTray / notifications panel.

    Surfaces two sources so the viewer reflects real activity instead of sitting
    empty: (1) the TASKS registry (agent tasks spawned via /api/tasks or
    spawn_task), and (2) live PROCESSES — the holographic orbs that briefings,
    vault access, context compression, model pulls, etc. register. Process
    entries are flagged `process: True` so the frontend can skip them for the
    "task complete" chat notification and for orb-syncing (the /api/processes
    poll already owns those orbs)."""
    tasks = _task_snapshot() or []
    seen = {t.get('task_id') for t in tasks if t.get('task_id')}
    _status_map = {'completed': 'complete', 'error': 'failed', 'running': 'running'}
    now = _time.time()
    with PROCESSES_LOCK:
        for pid, p in list(PROCESSES.items()):
            if pid in seen:
                continue
            # Skip ephemeral inline chat/voice orbs (category 'default') — those
            # turns are already reflected by the chat "thinking" state, and
            # surfacing every one would clutter the tray.
            if (p.get('category') or 'default') == 'default':
                continue
            started = p.get('started', now)
            ended = p.get('ended')
            tasks.append({
                'task_id': pid,
                'name': p.get('label') or p.get('name') or 'Process',
                'status': _status_map.get(p.get('status', 'running'), 'running'),
                'progress': p.get('progress', 0),
                'icon': p.get('icon'),
                'model': p.get('model'),
                'category': p.get('category'),
                'created': started,
                'started': started,
                'elapsed': int((ended or now) - started),
                'process': True,
            })
    return jsonify({"tasks": tasks})


@tasks_bp.route('/api/tasks/<task_id>')
def get_task(task_id):
    task = _task_snapshot(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@tasks_bp.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    with TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id]['status'] = 'cancelled'
            del TASKS[task_id]
            return jsonify({"status": "cancelled"})
    return jsonify({"error": "Task not found"}), 404


@tasks_bp.route('/api/agent/steer', methods=['POST'])
@login_required
def api_agent_steer():
    """Push a follow-up prompt into a running task's dual-loop queue.

    POST body: { "task_id": "...", "message": "..." }
    The message is injected as a new user turn after the current agent pass finishes.
    """
    data = request.get_json() or {}
    task_id = (data.get('task_id') or '').strip()
    message = (data.get('message') or '').strip()
    if not task_id or not message:
        return jsonify({"error": "task_id and message are required"}), 400
    with TASKS_LOCK:
        if task_id not in TASKS:
            return jsonify({"error": "Task not found"}), 404
    with _FOLLOW_UP_LOCK:
        _FOLLOW_UP_QUEUES.setdefault(task_id, []).append(message)
    return jsonify({"ok": True, "task_id": task_id, "queued": message[:120]})


@tasks_bp.route('/api/processes')
def list_processes():
    with PROCESSES_LOCK:
        out = []
        now = _time.time()
        for pid, p in list(PROCESSES.items()):
            row = dict(p)
            row["elapsed"] = int(now - row.get("started", now))
            if row.get("ended"):
                row["elapsed"] = int(row["ended"] - row["started"])
            out.append(row)
            # Auto-purge completed processes older than 30s
            if row.get("status") in ("completed", "error") and row.get("ended"):
                if now - row["ended"] > 30:
                    del PROCESSES[pid]
    return jsonify({"processes": out})

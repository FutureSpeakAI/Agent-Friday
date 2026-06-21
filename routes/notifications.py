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
from services.notifications import (
    _compute_derived_notifications,
)  # noqa: E501
from services.voice_engine import (
    _notif_engine,
)  # noqa: E501

notif_bp = Blueprint('notifications', __name__)



@notif_bp.route('/api/notifications')
def get_notifications():
    """Return queued + computed notifications, newest first."""
    queued = _notif_engine.list_notifications(limit=80) if _notif_engine else []
    derived = _compute_derived_notifications()
    # Normalize legacy keys: queued items already have id/title/body/priority/etc
    items = queued + derived
    unread = sum(1 for n in items if not n.get('read') and not n.get('dismissed'))
    return jsonify({
        "status": "ok",
        "items": items,
        "notifications": items,  # legacy alias
        "count": len(items),
        "unread": unread,
    })


@notif_bp.route('/api/notifications/read', methods=['POST'])
def mark_notification_read():
    data = request.get_json(silent=True) or {}
    nid = data.get('id')
    if _notif_engine and nid:
        if data.get('all'):
            n = _notif_engine.mark_all_read()
            return jsonify({"status": "ok", "marked": n})
        ok = _notif_engine.mark_read(str(nid))
        return jsonify({"status": "ok" if ok else "not_found", "id": nid})
    return jsonify({"status": "ok", "id": nid})


@notif_bp.route('/api/notifications/dismiss', methods=['POST'])
def dismiss_notification():
    data = request.get_json(silent=True) or {}
    nid = data.get('id')
    if not _notif_engine or not nid:
        return jsonify({"status": "noop"})
    ok = _notif_engine.dismiss(str(nid))
    return jsonify({"status": "ok" if ok else "not_found", "id": nid})


@notif_bp.route('/api/notifications/push', methods=['POST'])
def push_notification_endpoint():
    """Allow other processes / skills to enqueue a notification."""
    if not _notif_engine:
        return jsonify({"status": "engine_unavailable"}), 503
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({"status": "error", "message": "title required"}), 400
    entry = _notif_engine.push(
        title=title,
        body=data.get('body', ''),
        priority=data.get('priority', 'medium'),
        source=data.get('source', 'external'),
        kind=data.get('kind', 'info'),
        actions=data.get('actions') or [],
        proactive_chat=bool(data.get('proactive_chat')),
        chat_message=data.get('chat_message'),
        dedupe_key=data.get('dedupe_key'),
        meta=data.get('meta') or {},
        target=data.get('target') or {},
    )
    return jsonify({"status": "ok", "notification": entry})


@notif_bp.route('/api/notifications/chat-injections')
def get_chat_injections():
    """Pending proactive messages that should appear in the chat stream."""
    if not _notif_engine:
        return jsonify({"items": []})
    return jsonify({"items": _notif_engine.pending_chat_injections()})


@notif_bp.route('/api/notifications/chat-injections/ack', methods=['POST'])
def ack_chat_injection_endpoint():
    if not _notif_engine:
        return jsonify({"status": "noop"})
    data = request.get_json(silent=True) or {}
    nid = data.get('id')
    if not nid:
        return jsonify({"status": "error"}), 400
    ok = _notif_engine.ack_chat_injection(str(nid))
    return jsonify({"status": "ok" if ok else "not_found", "id": nid})

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
    CONTEXT_LOG_DIR,
    _context_log_files,
    _context_logging_enabled,
    _load_settings,
    _save_settings,
)  # noqa: E501
from services.model_router import (
    _get_context_compressor,
)  # noqa: E501

context_bp = Blueprint('context', __name__)


@context_bp.route('/api/context/compression-stats')
def context_compression_stats():
    """Cumulative Headroom compression savings (tokens before/after/saved,
    ratios, availability). The compressor has tracked these since day one —
    this endpoint finally surfaces them."""
    try:
        from services.model_router import _get_context_compressor
        stats = _get_context_compressor().get_stats()
    except Exception as e:
        stats = {"available": False, "error": str(e)}
    return jsonify({"status": "ok", "compression": stats})


@context_bp.route('/api/context/search', methods=['POST'])
def context_search():
    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    date_from = (data.get("date_from") or "").strip() or None
    date_to = (data.get("date_to") or "").strip() or None
    type_filter = (data.get("type") or "").strip() or None
    limit = int(data.get("limit") or 200)
    q_lower = query.lower()
    out = []
    for d, f in (_context_log_files(date_from, date_to) or []):
        try:
            with open(f, "r", encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if type_filter and entry.get("type") != type_filter:
                        continue
                    if q_lower and q_lower not in json.dumps(entry, default=str).lower():
                        continue
                    out.append(entry)
                    if len(out) >= limit:
                        break
        except Exception:
            continue
        if len(out) >= limit:
            break
    return jsonify({"status": "ok", "count": len(out), "results": out})


@context_bp.route('/api/context/stats', methods=['GET'])
def context_stats():
    enabled = _context_logging_enabled()
    settings = _load_settings()
    files = _context_log_files() or []
    total_entries = 0
    total_bytes = 0
    dates = []
    for d, f in files:
        try:
            sz = f.stat().st_size
            total_bytes += sz
            with open(f, "r", encoding='utf-8') as fh:
                total_entries += sum(1 for _ in fh)
            dates.append(d)
        except Exception:
            pass
    avg_per_day = round(total_entries / len(dates), 1) if dates else 0
    return jsonify({
        "status": "ok",
        "enabled": enabled,
        "off_record": bool(settings.get('off_record')),
        "retention_days": settings.get('context_retention_days', 0),
        "days": len(dates),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "total_entries": total_entries,
        "total_bytes": total_bytes,
        "avg_entries_per_day": avg_per_day,
        "log_dir": str(CONTEXT_LOG_DIR),
    })


@context_bp.route('/api/compression-stats', methods=['GET'])
def compression_stats():
    """Headroom context-compression savings for this server process.

    Compression powered by Headroom (https://github.com/chopratejas/headroom,
    Tejas Chopra, Apache 2.0). Stats are cumulative since the last restart.
    """
    settings = _load_settings()
    cfg = settings.get('context_compression') or {}
    try:
        compressor = _get_context_compressor(cfg)
        stats = compressor.get_stats()
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": str(exc),
            "enabled": bool(cfg.get('enabled', True)),
            "available": False,
        }), 200
    return jsonify({
        "status": "ok",
        "powered_by": "Headroom (https://github.com/chopratejas/headroom)",
        **stats,
    })


@context_bp.route('/api/context/range', methods=['DELETE'])
def context_delete_range():
    data = request.get_json(force=True, silent=True) or {}
    if data.get("confirm") != "DELETE":
        return jsonify({"status": "error", "message": "confirmation token required"}), 400
    date_from = (data.get("date_from") or "").strip() or None
    date_to = (data.get("date_to") or "").strip() or None
    deleted = []
    for d, f in (_context_log_files(date_from, date_to) or []):
        try:
            f.unlink()
            deleted.append(d)
        except Exception:
            pass
    return jsonify({"status": "ok", "deleted": deleted, "count": len(deleted)})


@context_bp.route('/api/context/pause', methods=['POST'])
def context_pause():
    merged = _save_settings({**_load_settings(), "context_logging_enabled": False})
    return jsonify({"status": "ok", "enabled": merged.get('context_logging_enabled', False)})


@context_bp.route('/api/context/resume', methods=['POST'])
def context_resume():
    merged = _save_settings({**_load_settings(), "context_logging_enabled": True})
    return jsonify({"status": "ok", "enabled": merged.get('context_logging_enabled', True)})


@context_bp.route('/api/context/export', methods=['GET'])
def context_export():
    """Stream a zip of all context log files."""
    import zipfile, io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for d, f in (_context_log_files() or []):
            try:
                zf.write(f, arcname=f"context-log/{f.name}")
            except Exception:
                pass
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'friday-context-log-{date.today().isoformat()}.zip',
    )

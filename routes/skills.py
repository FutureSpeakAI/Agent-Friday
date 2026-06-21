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
    _load_settings,
    login_required,
    process_register,
    process_update,
)  # noqa: E501

skills_bp = Blueprint('skills', __name__)



# ── Portable skill registry (SKILL.md folder format) ────────────
@skills_bp.route('/api/skills', methods=['GET'])
@login_required
def api_skills_list():
    """List all skills (learned, imported, bundled) in the registry."""
    try:
        import skill_registry as _skreg
        skills = _skreg.list_skills()
        return jsonify({"skills": skills, "count": len(skills)})
    except Exception as e:
        return jsonify({"error": str(e), "skills": [], "count": 0}), 500


@skills_bp.route('/api/skills/import', methods=['POST'])
@login_required
def api_skills_import():
    """Import a portable skill — multipart .zip upload, or JSON {path, name}
    pointing at a local folder / zip / legacy .yaml."""
    import tempfile as _tf, shutil as _sh
    try:
        import skill_registry as _skreg
        upload = request.files.get('file') if request.files else None
        if upload is not None:
            name = request.form.get('name') or None
            tmpd = Path(_tf.mkdtemp(prefix='skup_'))
            try:
                dest = tmpd / (upload.filename or 'skill.zip')
                upload.save(str(dest))
                res = _skreg.import_skill(dest, name=name)
            finally:
                _sh.rmtree(tmpd, ignore_errors=True)
        else:
            data = request.get_json(silent=True) or {}
            src = data.get('path')
            if not src:
                return jsonify({"error": "provide a 'file' upload or JSON {path}"}), 400
            res = _skreg.import_skill(src, name=data.get('name'))
        return jsonify({"status": "ok", **res})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@skills_bp.route('/api/skills/<name>/export', methods=['GET'])
@login_required
def api_skills_export(name):
    """Download a skill as a portable .zip (canonical SKILL.md folder)."""
    try:
        import skill_registry as _skreg
        z = _skreg.export_skill(name)
        return send_file(str(z), as_attachment=True, download_name=f"{name}.zip")
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@skills_bp.route('/api/skillopt/state', methods=['GET'])
@login_required
def api_skillopt_state():
    """Fleet state for the Skills Observatory UI (was previously unrouted)."""
    try:
        from skillopt_engine import export_fleet_state
        return jsonify(export_fleet_state())
    except Exception as e:
        return jsonify({"error": str(e), "skills": []})


@skills_bp.route('/api/ollama/status')
def ollama_status():
    """Return Ollama availability + installed models."""
    try:
        from ollama_manager import get_manager
        settings = _load_settings()
        routing_cfg = settings.get('model_routing') or {}
        ollama = get_manager(routing_cfg.get('ollama_url', 'http://localhost:11434'))
        available = ollama.is_available()
        models = ollama.list_models() if available else []
        hw = ollama.detect_hardware() if available else {}
        return jsonify({
            "available": available,
            "url": ollama.base_url,
            "models": models,
            "hardware": hw,
            "model_count": len(models),
        })
    except Exception as e:
        return jsonify({"available": False, "error": str(e), "models": [],
                        "model_count": 0})


@skills_bp.route('/api/ollama/models')
def ollama_models():
    """List available Ollama models with capabilities and recommendations."""
    try:
        from ollama_manager import get_manager
        settings = _load_settings()
        routing_cfg = settings.get('model_routing') or {}
        ollama = get_manager(routing_cfg.get('ollama_url', 'http://localhost:11434'))
        if not ollama.is_available():
            return jsonify({"installed": [], "recommended": [], "available": False})
        installed = ollama.list_models()
        hw = ollama.detect_hardware()
        recommended = ollama.recommend_models(hw)
        installed_names = {m['name'] for m in installed}
        for rec in recommended:
            rec['installed'] = rec['name'] in installed_names
        return jsonify({
            "installed": installed,
            "recommended": recommended,
            "hardware": hw,
            "available": True,
        })
    except Exception as e:
        return jsonify({"installed": [], "recommended": [], "available": False,
                        "error": str(e)})


@skills_bp.route('/api/ollama/pull', methods=['POST'])
def ollama_pull():
    """Pull/download an Ollama model. Returns immediately; poll /api/ollama/status."""
    try:
        data = request.get_json(silent=True) or {}
        model_name = data.get('model', '').strip()
        if not model_name:
            return jsonify({"error": "model name required"}), 400
        from ollama_manager import get_manager
        settings = _load_settings()
        routing_cfg = settings.get('model_routing') or {}
        ollama = get_manager(routing_cfg.get('ollama_url', 'http://localhost:11434'))
        pid = f"pull-{uuid.uuid4().hex[:8]}"
        process_register(pid, name="Pulling Model", label=f"Pulling {model_name}…",
                         category="monitoring", icon="⬇️")

        def _do_pull():
            try:
                def _progress(status, pct):
                    try:
                        process_update(pid, label=f"{model_name}: {status} ({pct:.0f}%)",
                                       progress=min(0.99, pct / 100))
                    except Exception:
                        pass
                ok = ollama.pull_model(model_name, progress_callback=_progress)
                process_update(pid, status='completed' if ok else 'error',
                               progress=1.0, label=f"{model_name} {'ready' if ok else 'failed'}")
            except Exception as e:
                process_update(pid, status='error', progress=1.0, label=str(e)[:40])

        threading.Thread(target=_do_pull, daemon=True).start()
        return jsonify({"status": "pulling", "task_id": pid, "model": model_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

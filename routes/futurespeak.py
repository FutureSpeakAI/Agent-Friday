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
    VIBE_TERMINALS,
)  # noqa: E501
from services.code_engine import (
    _run_claude_terminal,
)  # noqa: E501
from services.futurespeak import (
    FS_ASSETS_DIR,
    FS_LEGAL_FILE,
    FS_ORG,
    FS_PIPELINE_FILE,
    FS_PROJECTS_ROOT,
    FS_REVENUE_FILE,
    _fs_has_replit,
    _fs_load,
    _fs_project_view,
    _fs_projects_load,
    _fs_projects_save,
    _fs_repo_status,
    _fs_resolve_repo_path,
    _git,
)  # noqa: E501

fs_bp = Blueprint('futurespeak', __name__)



@fs_bp.route('/api/futurespeak/pipeline')
def fs_pipeline():
    data = _fs_load(FS_PIPELINE_FILE, {"opportunities": []})
    opps = data.get('opportunities', []) or []
    total_value = sum((o.get('value_usd') or 0) for o in opps)
    weighted = sum((o.get('value_usd') or 0) * (o.get('probability') or 0) for o in opps)
    by_status = {}
    for o in opps:
        s = o.get('status', 'unknown')
        by_status[s] = by_status.get(s, 0) + 1
    return jsonify({
        "status": "ok",
        "opportunities": opps,
        "total": len(opps),
        "total_value": total_value,
        "weighted_value": weighted,
        "by_status": by_status,
    })


@fs_bp.route('/api/futurespeak/revenue')
def fs_revenue():
    data = _fs_load(FS_REVENUE_FILE, {"months": [], "quarters": []})
    months = data.get('months', []) or []
    quarters = data.get('quarters', []) or []
    burn = data.get('monthly_burn') or 0
    cash = data.get('cash_on_hand') or 0

    last_actual = 0
    for m in months:
        if isinstance(m.get('actual'), (int, float)):
            last_actual = m['actual']
    net_monthly = last_actual - burn
    runway_months = None
    if burn > 0 and net_monthly < 0:
        runway_months = round(cash / burn, 1)

    ytd_actual = sum(m.get('actual') or 0 for m in months)
    ytd_projected = sum(m.get('projected') or 0 for m in months)

    return jsonify({
        "status": "ok",
        "currency": data.get('currency', 'USD'),
        "months": months,
        "quarters": quarters,
        "monthly_burn": burn,
        "cash_on_hand": cash,
        "last_actual_month": last_actual,
        "net_monthly": net_monthly,
        "runway_months": runway_months,
        "ytd_actual": ytd_actual,
        "ytd_projected": ytd_projected,
    })


@fs_bp.route('/api/futurespeak/legal')
def fs_legal():
    data = _fs_load(FS_LEGAL_FILE, {"items": []})
    items = data.get('items', []) or []
    by_status, by_type = {}, {}
    for it in items:
        s = it.get('status', 'unknown')
        t = it.get('type', 'other')
        by_status[s] = by_status.get(s, 0) + 1
        by_type[t] = by_type.get(t, 0) + 1
    return jsonify({
        "status": "ok",
        "items": items,
        "total": len(items),
        "by_status": by_status,
        "by_type": by_type,
    })


@fs_bp.route('/api/futurespeak/assets')
def fs_assets():
    assets = []
    if FS_ASSETS_DIR.exists():
        for p in sorted(FS_ASSETS_DIR.iterdir()):
            try:
                stat = p.stat()
                assets.append({
                    "name": p.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "ext": p.suffix.lower().lstrip('.'),
                    "kind": "dir" if p.is_dir() else "file",
                })
            except Exception:
                continue
    return jsonify({"status": "ok", "assets": assets, "total": len(assets), "path": str(FS_ASSETS_DIR)})


@fs_bp.route('/api/futurespeak/projects')
def fs_projects():
    projects = [_fs_project_view(p) for p in _fs_projects_load()]
    counts = {"green": 0, "yellow": 0, "red": 0, "remote": 0}
    for p in projects:
        ds = (p.get('status') or {}).get('deploy_status', 'remote')
        counts[ds] = counts.get(ds, 0) + 1
    return jsonify({"status": "ok", "projects": projects, "total": len(projects),
                    "by_deploy": counts})


@fs_bp.route('/api/futurespeak/project/<name>')
def fs_project_detail(name):
    for p in _fs_projects_load():
        if p.get('name', '').lower() == name.lower():
            return jsonify({"status": "ok", "project": _fs_project_view(p)})
    return jsonify({"status": "error", "message": "Project not found"}), 404


@fs_bp.route('/api/futurespeak/project', methods=['POST'])
def fs_project_add():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"status": "error", "message": "name required"}), 400
    projects = _fs_projects_load()
    if any(p.get('name', '').lower() == name.lower() for p in projects):
        return jsonify({"status": "error", "message": "Project already exists"}), 409
    proj = {
        "name": name,
        "url": (data.get('url') or '').strip(),
        "replit_url": (data.get('replit_url') or '').strip(),
        "repo": (data.get('repo') or '').strip(),
        "repo_path": (data.get('repo_path') or '').strip() or None,
        "description": (data.get('description') or '').strip(),
        "category": (data.get('category') or 'company').strip(),
    }
    projects.append(proj)
    _fs_projects_save(projects)
    return jsonify({"status": "ok", "project": _fs_project_view(proj)})


@fs_bp.route('/api/futurespeak/project/<name>', methods=['DELETE'])
def fs_project_remove(name):
    projects = _fs_projects_load()
    remaining = [p for p in projects if p.get('name', '').lower() != name.lower()]
    if len(remaining) == len(projects):
        return jsonify({"status": "error", "message": "Project not found"}), 404
    _fs_projects_save(remaining)
    return jsonify({"status": "ok", "removed": name, "total": len(remaining)})


@fs_bp.route('/api/futurespeak/project/<name>/edit', methods=['POST'])
def fs_project_edit(name):
    """Scoped vibe-coding: launch a Claude Code terminal pinned to this repo.

    Mirrors /api/vibe-code/launch but pre-sets cwd to the project's clone so
    the Dev Studio flow is scoped to a single site. With deploy=true the task
    is wrapped to stage, commit and push (Replit auto-deploys from GitHub).
    """
    data = request.get_json(silent=True) or {}
    instruction = (data.get('task') or data.get('instruction') or '').strip()
    if not instruction:
        return jsonify({"status": "error", "message": "task required"}), 400

    proj = next((p for p in _fs_projects_load()
                 if p.get('name', '').lower() == name.lower()), None)
    if not proj:
        return jsonify({"status": "error", "message": "Project not found"}), 404
    repo_path = _fs_resolve_repo_path(proj)
    if not repo_path:
        return jsonify({"status": "error",
                        "message": f"{name} is not cloned locally — clone it under ~/Projects first"}), 400

    task_desc = instruction
    if data.get('deploy'):
        task_desc = (
            f"In the {proj['name']} repo: {instruction}. "
            "When the change is complete and verified, stage all changes, commit with a clear "
            "conventional-commit message describing the change, and push to the current branch's "
            "remote so Replit auto-deploys from GitHub. Report the commit hash and push result."
        )

    cwd = os.path.normpath(str(repo_path))
    tid = str(uuid.uuid4())[:12]
    VIBE_TERMINALS[tid] = {
        'id': tid, 'task': task_desc, 'status': 'launching', 'cwd': cwd,
        'pid': None, 'started': datetime.now().isoformat(), 'stopped': None,
        'log_file': None, 'project': proj['name'],
    }
    threading.Thread(target=_run_claude_terminal, args=(tid, task_desc, cwd), daemon=True).start()
    return jsonify({"status": "ok", "terminal_id": tid, "project": proj['name'],
                    "cwd": cwd, "deploy": bool(data.get('deploy'))})


@fs_bp.route('/api/futurespeak/scan')
def fs_scan():
    """Rescan ~/Projects for FutureSpeakAI repos.

    A repo is a deployable candidate if its origin remote belongs to the
    FutureSpeakAI org AND it carries a Replit config (.replit / replit.nix).
    Also auto-links repo_path for any portfolio entry whose repo is now found,
    and flags which candidates are already in the portfolio.
    """
    portfolio = _fs_projects_load()
    portfolio_repos = {(p.get('repo') or '').lower() for p in portfolio}

    candidates = []
    if FS_PROJECTS_ROOT.exists():
        for d in sorted(FS_PROJECTS_ROOT.iterdir()):
            if not d.is_dir() or not (d / '.git').exists():
                continue
            remote = _git(d, 'remote', 'get-url', 'origin') or ''
            is_org = FS_ORG.lower() in remote.lower()
            has_replit = _fs_has_replit(d)
            if not (is_org and has_replit):
                continue
            st = _fs_repo_status(d)
            candidates.append({
                "repo": d.name, "repo_path": str(d), "remote": remote,
                "has_replit": has_replit,
                "in_portfolio": d.name.lower() in portfolio_repos,
                "branch": st.get('branch'),
                "last_commit_rel": st.get('last_commit_rel'),
                "last_commit_msg": st.get('last_commit_msg'),
                "deploy_status": st.get('deploy_status'),
            })

    # Refresh repo_path on portfolio entries that are now resolvable.
    changed = False
    for p in portfolio:
        rp = _fs_resolve_repo_path(p)
        if rp and p.get('repo_path') != str(rp):
            p['repo_path'] = str(rp)
            changed = True
    if changed:
        _fs_projects_save(portfolio)

    return jsonify({"status": "ok", "candidates": candidates,
                    "total": len(candidates),
                    "discovered": [c for c in candidates if not c['in_portfolio']],
                    "root": str(FS_PROJECTS_ROOT)})

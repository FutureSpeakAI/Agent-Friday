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
    _POPEN_FLAGS,
)  # noqa: E501



# ═══════════════════════════════════════════════════════════════
#  FUTURESPEAK BUSINESS WORKSPACE
# ═══════════════════════════════════════════════════════════════

FUTURESPEAK_DIR = FRIDAY_DIR / "futurespeak"
FUTURESPEAK_DIR.mkdir(parents=True, exist_ok=True)
FS_PIPELINE_FILE = FUTURESPEAK_DIR / "pipeline.json"
FS_REVENUE_FILE = FUTURESPEAK_DIR / "revenue.json"
FS_LEGAL_FILE = FUTURESPEAK_DIR / "legal.json"
FS_ASSETS_DIR = FUTURESPEAK_DIR / "demo-assets"
FS_ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def _fs_load(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return fallback


# ═══════════════════════════════════════════════════════════════
#  FUTURESPEAK STUDIO — portfolio project / deployment cockpit
# ═══════════════════════════════════════════════════════════════
#  Project-management + deploy layer on top of the Dev Studio vibe-coding
#  backend. The curated portfolio lives in ~/.friday/futurespeak_projects.json;
#  live git/deploy status is computed on demand from the local clone.

FS_PROJECTS_FILE = FRIDAY_DIR / "futurespeak_projects.json"
FS_ORG = "FutureSpeakAI"                      # GitHub org + Replit owner handle
FS_PROJECTS_ROOT = HOME / "Projects"          # where local clones live

# Seeded the first time the file is absent. repo == local clone dir name under
# ~/Projects; url == live site; replit_url derived from the org handle. Sites
# that aren't cloned locally (Replit-only) still show as live cards.
# New users start with just this app in the portfolio. Use "Scan ~/Projects" or
# the add button in the Sites workspace to populate your own sites.
FS_DEFAULT_PROJECTS = [
    {"name": "Agent Friday", "url": "",
     "replit_url": "",
     "repo": "friday-desktop",
     "description": "This sovereign AI desktop — the app you're running now",
     "category": "company"},
]


def _fs_projects_load():
    """Load the portfolio, seeding defaults on first run."""
    if not FS_PROJECTS_FILE.exists():
        try:
            FS_PROJECTS_FILE.write_text(
                json.dumps({"projects": FS_DEFAULT_PROJECTS}, indent=2), encoding='utf-8')
        except Exception:
            pass
        return list(FS_DEFAULT_PROJECTS)
    try:
        data = json.loads(FS_PROJECTS_FILE.read_text(encoding='utf-8'))
        return data.get('projects', []) or []
    except Exception:
        return list(FS_DEFAULT_PROJECTS)


def _fs_projects_save(projects):
    try:
        FS_PROJECTS_FILE.write_text(
            json.dumps({"projects": projects}, indent=2), encoding='utf-8')
        return True
    except Exception:
        return False


def _git(repo_path, *args, timeout=10):
    """Run a git command inside repo_path; return stdout (stripped) or None."""
    try:
        r = subprocess.run(['git', '-C', str(repo_path), *args],
                           capture_output=True, text=True, timeout=timeout,
                           creationflags=_POPEN_FLAGS)
        if r.returncode != 0:
            return None
        return (r.stdout or '').strip()
    except Exception:
        return None


def _fs_resolve_repo_path(proj):
    """Resolve a project's local clone dir, or None if it isn't cloned."""
    rp = proj.get('repo_path')
    if rp:
        p = Path(os.path.expanduser(rp))
        if p.exists():
            return p
    repo = proj.get('repo')
    if repo:
        p = FS_PROJECTS_ROOT / repo
        if (p / '.git').exists():
            return p
    return None


def _fs_has_replit(repo_path):
    return bool(repo_path) and ((repo_path / '.replit').exists() or (repo_path / 'replit.nix').exists())


def _fs_repo_status(repo_path):
    """Compute live git/deploy status for a local clone.

    deploy_status: green = clean & in sync · yellow = uncommitted or ahead ·
    red = git error · remote = no local clone (handled by the caller).
    """
    status = {
        "cloned": True, "branch": None, "last_commit_date": None,
        "last_commit_msg": None, "last_commit_rel": None,
        "uncommitted": 0, "ahead": 0, "behind": 0, "dirty": False,
        "has_replit": _fs_has_replit(repo_path), "deploy_status": "green",
        "error": None,
    }
    branch = _git(repo_path, 'rev-parse', '--abbrev-ref', 'HEAD')
    if branch is None:
        status.update({"deploy_status": "red", "error": "git unavailable"})
        return status
    status["branch"] = branch

    log = _git(repo_path, 'log', '-1', '--format=%cI|%s|%cr')
    if log and '|' in log:
        parts = log.split('|', 2)
        status["last_commit_date"] = parts[0]
        status["last_commit_msg"] = parts[1] if len(parts) > 1 else ''
        status["last_commit_rel"] = parts[2] if len(parts) > 2 else ''

    porcelain = _git(repo_path, 'status', '--porcelain')
    if porcelain:
        files = [l for l in porcelain.splitlines() if l.strip()]
        status["uncommitted"] = len(files)
        status["dirty"] = len(files) > 0

    counts = _git(repo_path, 'rev-list', '--count', '--left-right', '@{u}...HEAD')
    if counts and '\t' in counts:
        try:
            behind, ahead = counts.split('\t')
            status["behind"] = int(behind)
            status["ahead"] = int(ahead)
        except Exception:
            pass

    if status["dirty"] or status["ahead"] > 0:
        status["deploy_status"] = "yellow"
    return status


def _fs_project_view(proj):
    """Merge a stored project with its live status for the API response."""
    out = dict(proj)
    repo_path = _fs_resolve_repo_path(proj)
    if not proj.get('replit_url') and proj.get('repo'):
        out['replit_url'] = f"https://replit.com/@{FS_ORG}/{proj['repo']}"
    if repo_path:
        out['repo_path'] = str(repo_path)
        out['status'] = _fs_repo_status(repo_path)
    else:
        # Live on Replit but not cloned here — surface as a remote-only card.
        out['repo_path'] = None
        out['status'] = {
            "cloned": False, "deploy_status": "remote",
            "has_replit": False, "branch": None, "uncommitted": 0,
            "ahead": 0, "behind": 0, "dirty": False,
            "last_commit_date": None, "last_commit_msg": None,
            "last_commit_rel": None, "error": None,
        }
    return out



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
    FRIDAY_DIR,
    HOME,
    VIBE_LOG_DIR,
    VIBE_TERMINALS,
    _POPEN_FLAGS,
    _safe_under_home,
)  # noqa: E501



# ═══════════════════════════════════════════════════════════════
#  VIBE CODE — TERMINAL MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def _run_claude_terminal(terminal_id, task, cwd):
    """Launch a Claude Code instance in a new CMD window."""
    log_file = VIBE_LOG_DIR / f"{terminal_id}.log"
    try:
        # Validate cwd: must be an existing directory under HOME (prevents path
        # injection and escaping the sandbox root).
        cwd_p = Path(cwd or '').expanduser().resolve()
        if not cwd_p.is_dir() or _safe_under_home(str(cwd_p)) is None:
            raise ValueError(f"invalid or out-of-sandbox cwd: {cwd!r}")
        # Sanitize the task string: strip characters that could break out of the
        # nested cmd quoting and chain commands (command injection).
        safe_task = re.sub(r'["&|<>^%\r\n`]', ' ', str(task or ''))[:2000].strip()
        cmd = f'start "Friday-Vibe-{terminal_id[:8]}" cmd /k "cd /d {cwd_p} && claude --dangerously-skip-permissions \"{safe_task}\""'
        proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd_p))
        VIBE_TERMINALS[terminal_id].update({
            'status': 'running',
            'pid': proc.pid,
            'log_file': str(log_file)
        })
    except Exception as e:
        VIBE_TERMINALS[terminal_id].update({
            'status': 'error',
            'stopped': datetime.now().isoformat(),
            'error': str(e)
        })

PROJECTS_DIR = HOME / "Projects"
CODE_LOGS_DIR = FRIDAY_DIR / "logs"
CODE_PLANS_DIR = FRIDAY_DIR / "code_plans"
for _d in (CODE_LOGS_DIR, CODE_PLANS_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# ── Live log bus (in-memory ring buffer + SSE pub/sub) ──────────
_CODE_LOG_BUF = _deque(maxlen=2000)       # recent events for late subscribers
_CODE_LOG_SUBS = []                       # list[queue.Queue] of live SSE clients
_CODE_LOG_LOCK = threading.Lock()
_CODE_LOG_SEQ = {"n": 0}

# Process registry for the monitor (id -> meta). Separate from VIBE_TERMINALS
# so non-terminal background jobs (plans, git ops) can register too.
CODE_PROCESSES = {}


def _code_log(message, source="system", level="info"):
    """Publish a log line to the ring buffer, the daily file, and live SSE subs."""
    with _CODE_LOG_LOCK:
        _CODE_LOG_SEQ["n"] += 1
        evt = {
            "id": _CODE_LOG_SEQ["n"],
            "ts": datetime.now().isoformat(),
            "source": str(source)[:60],
            "level": str(level)[:12],
            "message": str(message)[:4000],
        }
        _CODE_LOG_BUF.append(evt)
        dead = []
        for q in _CODE_LOG_SUBS:
            try:
                q.put_nowait(evt)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                _CODE_LOG_SUBS.remove(q)
            except ValueError:
                pass
    # Persist to a daily log file (best-effort, outside the lock)
    try:
        day = datetime.now().strftime("%Y-%m-%d")
        with open(CODE_LOGS_DIR / f"{day}.log", "a", encoding="utf-8") as f:
            f.write(f"[{evt['ts']}] [{evt['source']}/{evt['level']}] {evt['message']}\n")
    except Exception:
        pass
    return evt


# ── Path safety ────────────────────────────────────────────────
def _projects_root():
    return os.path.realpath(str(PROJECTS_DIR))


def _safe_project_path(target):
    """Resolve `target` (abs or relative to ~/Projects) and confirm it lives
    inside ~/Projects/. Returns the realpath, or None if it escapes the sandbox."""
    if target is None:
        return None
    raw = str(target).strip()
    if not raw:
        return None
    raw = os.path.expanduser(raw)
    if not os.path.isabs(raw):
        raw = os.path.join(_projects_root(), raw)
    rp = os.path.realpath(raw)
    root = _projects_root()
    if rp == root or rp.startswith(root + os.sep):
        return rp
    return None


def _repo_path(name):
    """Resolve a repo by directory name (or relative path) under ~/Projects/.
    Returns realpath only if it exists and is a git working tree."""
    p = _safe_project_path(name)
    if not p or not os.path.isdir(p):
        return None
    if not os.path.isdir(os.path.join(p, ".git")) and not os.path.isfile(os.path.join(p, ".git")):
        return None
    return p


def _git_available() -> bool:
    """Return True if the git executable is on PATH."""
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5,
                       creationflags=_POPEN_FLAGS)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _dev_git(repo_path, *args, timeout=40):
    """Run a git subcommand inside repo_path. Returns CompletedProcess or None."""
    if not _git_available():
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="git not found")
    return subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True, text=True, timeout=timeout,
        creationflags=_POPEN_FLAGS,
    )


def _git_repo_summary(repo_path):
    """Build a status card dict for one repo."""
    name = os.path.basename(repo_path)
    card = {"name": name, "path": repo_path, "branch": "?", "dirty": 0,
            "ahead": 0, "behind": 0, "last_commit": "", "last_when": "",
            "upstream": None, "clean": True, "error": None}
    try:
        b = _dev_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD", timeout=10)
        if b.returncode == 0:
            card["branch"] = b.stdout.strip() or "?"
        st = _dev_git(repo_path, "status", "--porcelain", timeout=15)
        if st.returncode == 0:
            lines = [l for l in st.stdout.splitlines() if l.strip()]
            card["dirty"] = len(lines)
            card["clean"] = len(lines) == 0
        up = _dev_git(repo_path, "rev-list", "--left-right", "--count", "@{u}...HEAD", timeout=10)
        if up.returncode == 0 and up.stdout.strip():
            parts = up.stdout.split()
            if len(parts) == 2:
                card["behind"] = int(parts[0])
                card["ahead"] = int(parts[1])
                card["upstream"] = True
        else:
            card["upstream"] = False
        lc = _dev_git(repo_path, "log", "-1", "--format=%h\x1f%s\x1f%cr", timeout=10)
        if lc.returncode == 0 and lc.stdout.strip():
            bits = lc.stdout.strip().split("\x1f")
            if len(bits) == 3:
                card["last_commit"] = f"{bits[0]} {bits[1]}"
                card["last_when"] = bits[2]
    except subprocess.TimeoutExpired:
        card["error"] = "git timed out"
    except Exception as e:
        card["error"] = str(e)
    return card


# ── GIT: operations ────────────────────────────────────────────
def _git_result(rp, cp, action):
    ok = cp.returncode == 0
    out = (cp.stdout or "").strip()
    err = (cp.stderr or "").strip()
    _code_log(f"git {action} -> {'ok' if ok else 'FAILED'} :: {(out or err)[:300]}",
              source=f"git:{os.path.basename(rp)}", level="info" if ok else "error")
    return {"status": "ok" if ok else "error", "ok": ok, "stdout": out, "stderr": err, "code": cp.returncode}


# ── FILES: browser + viewer ────────────────────────────────────
_LANG_BY_EXT = {
    'py': 'python', 'js': 'javascript', 'jsx': 'javascript', 'ts': 'typescript',
    'tsx': 'typescript', 'html': 'html', 'htm': 'html', 'css': 'css', 'scss': 'scss',
    'json': 'json', 'md': 'markdown', 'sh': 'bash', 'bat': 'dos', 'ps1': 'powershell',
    'yml': 'yaml', 'yaml': 'yaml', 'toml': 'ini', 'ini': 'ini', 'sql': 'sql',
    'go': 'go', 'rs': 'rust', 'java': 'java', 'c': 'c', 'h': 'c', 'cpp': 'cpp',
    'rb': 'ruby', 'php': 'php', 'xml': 'xml', 'vue': 'xml', 'txt': 'plaintext',
}
_SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist',
              'build', '.next', '.cache', 'test-results', '.pytest_cache'}


# ── CODE: vibe coding (plan -> diff -> apply) ──────────────────
def _repo_tree(repo_path, max_files=200):
    """A compact relative-path listing of a repo for plan context."""
    out = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), repo_path).replace("\\", "/")
            out.append(rel)
            if len(out) >= max_files:
                return out
    return out



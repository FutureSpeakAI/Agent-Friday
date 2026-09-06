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
    VIBE_LOG_DIR,
    VIBE_TERMINALS,
    _POPEN_FLAGS,
    _safe_under_home,
)  # noqa: E501



# ═══════════════════════════════════════════════════════════════
#  VIBE CODE — TERMINAL MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def _run_claude_terminal(terminal_id, task, cwd):
    """Launch a Claude Code instance in a new console window Friday owns.

    Spawned directly with CREATE_NEW_CONSOLE rather than `cmd /c start ...`:
    a `start`-launched window is a grandchild of a wrapper `cmd.exe /c` that
    exits the moment `start` returns, so `proc.pid` from that older approach
    named a process already dead by the time anything read it back — every
    stop/kill against it was a no-op against a PID nobody held. Spawning the
    console directly makes `proc.pid` the real, long-lived window.
    The `title Friday-Vibe-<id>` prefix is not cosmetic: it is the only thing
    that lets a restarted process find this exact window again (by what it is
    actually running, matched via its own command line) rather than by a PID
    that reboot has already forgotten — see
    code_engine.adopt_or_reap_vibe_terminals.
    """
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
        inner = (f'title Friday-Vibe-{terminal_id} && cd /d "{cwd_p}" && '
                f'claude --dangerously-skip-permissions "{safe_task}"')
        proc = subprocess.Popen(
            ["cmd.exe", "/k", inner], cwd=str(cwd_p),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
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
    core._persist_vibe_terminals()


def _vibe_terminal_processes() -> dict:
    """`terminal_id -> (pid, command_line)` for live Friday-Vibe console windows.

    Matches on the `title Friday-Vibe-<id>` marker actually present in the
    process's own command line — never on `Name='cmd.exe'` alone, which would
    just as happily catch a terminal window the user opened by hand. This is
    the same discriminator lesson residency_arbiter._llama_server_pids
    documents from 2026-08-18: matching by binary/process name instead of by
    what the process is actually running reaps something that was never ours
    to touch. Nobody types `title Friday-Vibe-<id>` themselves.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
             "Where-Object { $_.CommandLine -like '*title Friday-Vibe-*' } | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30, creationflags=_POPEN_FLAGS)
        rows = json.loads(out.stdout or "[]")
        if isinstance(rows, dict):
            rows = [rows]
    except Exception:
        return {}
    found = {}
    for r in rows:
        try:
            cmdline = str(r.get("CommandLine") or "")
            pid = r.get("ProcessId")
            # `terminal_id` is `str(uuid.uuid4())[:12]` — 8 hex chars, the
            # uuid's first hyphen, then 3 more hex chars. Not 12 plain hex
            # chars: uuid4's dash always lands at index 8.
            m = re.search(r"Friday-Vibe-([0-9a-fA-F]{8}-[0-9a-fA-F]{3})", cmdline)
            if m and pid:
                found[m.group(1)] = (int(pid), cmdline)
        except Exception:
            continue
    return found


def adopt_or_reap_vibe_terminals() -> dict:
    """Reconcile VIBE_TERMINALS against what is actually running, at boot.

    `VIBE_TERMINALS` is an in-memory registry (core/__init__.py) of real OS
    subprocesses; it has no disk persistence of its own and no boot-time
    adopt-or-reap, so a Friday restart while a vibe-code terminal is running
    orphans that cmd.exe window — nothing tracks it, nothing can stop it from
    the UI, and the process monitor stops reporting it. This is the same
    class of bug residency_arbiter.LlamaServerBackend.adopt_or_reap exists to
    close for llama-server seats, applied to the other kind of process this
    app leaves running behind its own back.

    A live `Friday-Vibe-<id>` window whose id was persisted as running is
    ADOPTED: its record is restored into VIBE_TERMINALS with the (now
    verified live) pid.

    A live window with no persisted record is an orphan ONLY IF the state file
    is trustworthy, and on the first boot after this feature lands it is not.
    Reaping is inferential — "no record, therefore abandoned" — and that
    inference is only sound once every terminal Friday launches is recorded at
    launch. Before then the file is empty because nothing ever wrote it, not
    because nothing is running, and every live window looks like an orphan.
    Each one is a `claude --dangerously-skip-permissions` session someone may
    be part-way through, so the first-run cost of a wrong reap is unrecoverable
    work; the cost of a wrong adopt is a stale row in a list. The asymmetry
    decides it.

    So: an UNVERSIONED state file (missing, unreadable, or written before
    VIBE_STATE_VERSION existed) puts this in GRACE. Live windows are adopted
    rather than killed, and the versioned file written on the way out arms
    reaping for every subsequent boot. Grace is entered on the state of the
    file rather than on its mere existence precisely because the pre-fix
    reconcile already created an empty one on any machine that has booted this
    code once -- an existence check would find that file and reap on the very
    boot this exists to protect.
    """
    report = {"adopted": [], "reaped": [], "grace": False}
    state = core._read_vibe_state()
    persisted = state.get("terminals", {})
    # A version this process does not recognise is treated as trustworthy: it
    # was written by a build that also records at launch. Only the ABSENCE of a
    # version means "written before that promise existed".
    grace = not isinstance(state.get("version"), int)
    report["grace"] = grace
    try:
        live = _vibe_terminal_processes()
    except Exception as e:
        _code_log(f"vibe-terminal survey failed: {e}", source="vibe", level="error")
        return report

    for tid, (pid, _cmdline) in live.items():
        saved = persisted.get(tid)
        if saved or grace:
            entry = dict(saved or {})
            entry["pid"] = pid
            entry["status"] = "running"
            if not saved:
                # Adopted without a record: name it for the UI, and mark how it
                # arrived so a stale row is legible rather than mysterious.
                entry.setdefault("task", "(adopted at startup — launched before "
                                          "Friday tracked terminals)")
                entry.setdefault("cwd", "")
                entry.setdefault("started", datetime.now().isoformat())
                entry["adopted_without_record"] = True
            VIBE_TERMINALS[tid] = entry
            report["adopted"].append(tid)
        else:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=20, creationflags=_POPEN_FLAGS)
                report["reaped"].append(pid)
            except Exception:
                pass

    # Writes the versioned file. This is what ends grace, so it must happen
    # even when nothing was found -- otherwise every boot is a first boot.
    core._persist_vibe_terminals()
    if grace:
        _code_log(f"first vibe-terminal reconcile on this machine: adopted "
                  f"{len(report['adopted'])} live terminal(s) without killing "
                  f"anything. Reaping is armed from the next start.",
                  source="vibe", level="info")
    if report["adopted"]:
        _code_log(f"adopted {len(report['adopted'])} vibe-code terminal(s) "
                  f"from before the restart", source="vibe", level="info")
    if report["reaped"]:
        _code_log(f"reaped {len(report['reaped'])} orphaned vibe-code "
                  f"terminal(s): {sorted(report['reaped'])}", source="vibe", level="warn")
    return report

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



"""
FRIDAY Desktop v4.4 — Phase B OS Backend
Flask server with live data endpoints + Gemini creative API integration.
Powered by FutureSpeak.AI
"""

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
import time as _time
import calendar
import logging
import logging.handlers
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path

# ── Structured logging ──────────────────────────────────────────
# Module-level logger; file handler is attached below once FRIDAY_DIR is known.
# Using the "friday" hierarchy means all sub-loggers (friday.agent, friday.vault,
# etc.) propagate here and land in the same friday.log file automatically.
_log = logging.getLogger("friday")

# ── Frozen (PyInstaller) resource root ──────────────────────────
# When bundled, data files (index.html, static/, assets/, SELF.md, skills/…)
# live under sys._MEIPASS. Resolve resource paths against it and chdir there so
# the many CWD-relative send_from_directory('.', …) / ('static', …) calls work.
# NOTE: this file lives at src/agent_friday/core/__init__.py; .parent.parent gives
# src/agent_friday/ which is the package root where SELF.md and static/ live.
_RES_DIR = (Path(getattr(sys, "_MEIPASS")) if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent.parent)
if getattr(sys, "frozen", False):
    try:
        os.chdir(_RES_DIR)
    except Exception:
        pass

from flask import Flask, jsonify, request, send_from_directory, send_file, session, redirect, url_for, Response, stream_with_context
from functools import wraps

# Vault access control — gates Sovereign Vault content so it reaches local
# models only. Imported defensively so a missing module never blocks startup.
try:
    from agent_friday.privacy.vault_access import Tier as _VaultTier, VaultAccessControl, VaultAccessDenied
except Exception as _vac_err:  # pragma: no cover
    _VaultTier = None
    VaultAccessControl = None
    class VaultAccessDenied(Exception):
        pass
    _log.warning("vault_access unavailable (%s); vault gating disabled.", _vac_err)

# Cognitive Memory — versioned, hash-chained memory ledger.
try:
    from agent_friday.cognitive_memory import get_cognitive_memory, CognitiveMemory
    _HAS_COGMEM = True
except Exception as _cm_err:
    _HAS_COGMEM = False
    _log.warning("cognitive_memory unavailable (%s)", _cm_err)

# Dynamic Privilege Rings — zero-trust per-call elevation.
try:
    from agent_friday.dynamic_rings import get_privilege_manager, DynamicPrivilegeManager
    _HAS_DYNRINGS = True
except Exception as _dr_err:
    _HAS_DYNRINGS = False
    _log.warning("dynamic_rings unavailable (%s)", _dr_err)

# Proof of Integrity — AI Bill of Integrity manifest.
try:
    from agent_friday.governance.proof_of_integrity import get_integrity_engine, IntegrityEngine, CLAWS_TEXT
    _HAS_INTEGRITY = True
except Exception as _poi_err:
    _HAS_INTEGRITY = False
    _log.warning("proof_of_integrity unavailable (%s)", _poi_err)

# Trust systems — split into human contacts (PeopleGraph) and media/agent
# reputation (SourceTrustGraph) + the signed Federation attestation protocol.
try:
    from agent_friday.people_graph import get_people_graph
    from agent_friday.source_trust_graph import get_source_trust_graph
    import agent_friday.source_trust_federation as federation
    _HAS_TRUST_GRAPHS = True
except Exception as _tg_err:
    _HAS_TRUST_GRAPHS = False
    _log.warning("trust graphs unavailable (%s)", _tg_err)

# Behavioral anomaly detection — internal-governance monitor that scores each
# agent tool-use loop against the user's stated intent (inspired by Adrian).
try:
    from agent_friday.governance.behavioral_monitor import get_behavioral_monitor
    _HAS_BEHAVIORAL_MONITOR = True
except Exception as _bm_err:
    _HAS_BEHAVIORAL_MONITOR = False
    _log.warning("behavioral_monitor unavailable (%s)", _bm_err)

# Prevent console windows from flashing when spawning subprocesses on Windows.
_POPEN_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

try:
    from flask_sock import Sock, ConnectionClosed
    _HAS_SOCK = True
except ImportError:
    _HAS_SOCK = False
    _log.warning("flask-sock not installed — /ws/live disabled.")

app = Flask(__name__, static_folder=None)

# When set, the module imports cleanly for the test suite: the background daemon
# loops (kill-hotkey, daily scheduler, notification triggers, news archiver) are
# NOT started, so `import server` has no threads, no network, and no global
# hotkey side effects. Production launches leave this unset and behave normally.
_TESTING = os.environ.get('FRIDAY_TESTING') == '1'


def _load_or_create_secret():
    """Use FRIDAY_SECRET_KEY if provided, else a persisted random secret.

    Never falls back to a hardcoded value: this repo is public, so a known
    fallback secret would let anyone forge an authenticated session cookie on a
    remotely-exposed instance. The generated secret is stored 0600 under
    ~/.friday so sessions survive restarts.
    """
    env = os.environ.get("FRIDAY_SECRET_KEY")
    if env:
        return env
    try:
        p = Path(os.path.expanduser("~")) / ".friday" / "secret_key"
        if p.exists():
            existing = p.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        s = secrets.token_hex(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_text(s, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)  # restrict BEFORE rename so it's never world-readable
        except Exception:
            pass
        tmp.replace(p)  # atomic — no TOCTOU window
        return s
    except Exception:
        # Last resort: ephemeral per-process secret (logs everyone out on restart,
        # but never a guessable constant).
        return secrets.token_hex(32)


app.secret_key = _load_or_create_secret()
# Harden the session cookie. SECURE is opt-in (set FRIDAY_COOKIE_SECURE=1) since
# it requires HTTPS — enable it when serving over a tunnel.
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
if os.environ.get("FRIDAY_COOKIE_SECURE", "") not in ("", "0", "false", "False"):
    app.config["SESSION_COOKIE_SECURE"] = True
sock = Sock(app) if _HAS_SOCK else None

# Server start time for uptime reporting
SERVER_START_TS = _time.time()

# ── Authentication ───────────────────────────────────────────
FRIDAY_USERNAME = os.environ.get("FRIDAY_USERNAME", "admin")
# FRIDAY_PASSWORD is kept for backward compatibility only. Its two former duties
# are now split:
#   • HTTP auth  → _HTTP_AUTH_KEY  (FRIDAY_REMOTE_KEY env var, fallback FRIDAY_PASSWORD)
#   • Vault KDF  → FRIDAY_VAULT_PASSPHRASE (env var, fallback FRIDAY_PASSWORD)
# Setting only FRIDAY_PASSWORD still works; set the dedicated vars to decouple them.
FRIDAY_PASSWORD = os.environ.get("FRIDAY_PASSWORD", "")

# Vault passphrase — used ONLY for AES-256-GCM key derivation (Argon2id).
# Never used for HTTP authentication.  Set FRIDAY_VAULT_PASSPHRASE to decouple
# vault encryption from the remote-access password entirely.
FRIDAY_VAULT_PASSPHRASE: str = (
    os.environ.get("FRIDAY_VAULT_PASSPHRASE", "")
    or FRIDAY_PASSWORD
)

# Remote HTTP auth key — used ONLY for the login form shown to non-loopback
# clients (e.g. via Cloudflare Tunnel).  Set FRIDAY_REMOTE_KEY to a strong
# unique value and keep it separate from the vault passphrase.
_HTTP_AUTH_KEY: str = (
    os.environ.get("FRIDAY_REMOTE_KEY", "")
    or FRIDAY_PASSWORD
)

# Ephemeral per-startup API session token.  Generated fresh each restart, stored
# only in memory, never written to disk.  The main HTML page embeds it as
# window.__FRIDAY_API_TOKEN so the UI can include it in every API request via the
# X-Friday-Token header.  Rotating every restart means a captured token is
# automatically invalidated the next time the server is restarted.
_API_SESSION_TOKEN: str = secrets.token_hex(32)

# When "0"/"false", same-machine (loopback) requests are NOT auto-trusted and
# must authenticate like remote clients. Default "1" preserves the local-dev UX.
FRIDAY_TRUST_LOOPBACK = os.environ.get("FRIDAY_TRUST_LOOPBACK", "1") not in ("0", "false", "False")
# Optional shared token required for the /ws/live WebSocket regardless of
# loopback trust — defense-in-depth for voice when the server is exposed.
FRIDAY_WS_TOKEN = os.environ.get("FRIDAY_WS_TOKEN", "")

# Vault encryption health — updated by _get_vault_key() in services/agent.py.
# 'enabled' is True only when AES-256-GCM is confirmed working.  'warning' is
# surfaced in GET /api/health so the UI can display a persistent banner.
_VAULT_ENCRYPTION_STATE: dict = {
    "enabled": False,
    "error": "",    # non-empty = derivation failed (CRITICAL)
    "warning": "",  # non-empty = passphrase unset (advisory)
}

# Login throttle — per-IP failed-attempt window, persisted in SQLite so it
# survives server restarts (prevents brute-force via restart-cycle).
_LOGIN_LOCK = threading.Lock()
_LOGIN_MAX = 8                  # attempts allowed per window
_LOGIN_WINDOW = 300            # seconds
_THROTTLE_DB_PATH = None       # set once FRIDAY_DIR is known (below)

def _get_throttle_db():
    """Return a connection to the login throttle DB, creating the table if needed."""
    import sqlite3 as _sq3
    global _THROTTLE_DB_PATH
    if _THROTTLE_DB_PATH is None:
        _THROTTLE_DB_PATH = FRIDAY_DIR / "login_throttle.db"
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    conn = _sq3.connect(str(_THROTTLE_DB_PATH), timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS login_attempts "
        "(ip TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0, window_start REAL NOT NULL)"
    )
    conn.commit()
    return conn

# Loopback addresses that are always auto-authenticated. Requests from the
# user's own machine (direct HTTP or WebSocket) skip the login screen; only
# remote connections (e.g. via Cloudflare Tunnel) ever see it.
_LOOPBACK_ADDRS = {'127.0.0.1', '::1', 'localhost'}

def _is_local_request():
    """True if the current request originates from this machine (loopback)."""
    try:
        addr = (request.remote_addr or '').strip()
    except Exception:
        return False
    if not addr:
        return False
    # Normalize IPv6-mapped IPv4 like ::ffff:127.0.0.1
    if addr.startswith('::ffff:'):
        addr = addr[7:]
    return addr in _LOOPBACK_ADDRS

def _loopback_trusted():
    """Loopback auto-auth, unless FRIDAY_TRUST_LOOPBACK=0 forces login locally too."""
    return FRIDAY_TRUST_LOOPBACK and _is_local_request()

def _login_attempt_ok(ip):
    """False if this IP has exceeded the failed-login budget for the window.

    State is persisted in SQLite so throttle windows survive server restarts,
    giving real brute-force protection regardless of process lifecycle.
    """
    try:
        with _LOGIN_LOCK:
            conn = _get_throttle_db()
            row = conn.execute(
                "SELECT count, window_start FROM login_attempts WHERE ip=?", (ip,)
            ).fetchone()
            if row is None:
                conn.close()
                return True
            cnt, first = row
            if _time.time() - first > _LOGIN_WINDOW:
                conn.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
                conn.commit()
                conn.close()
                return True
            conn.close()
            return cnt < _LOGIN_MAX
    except Exception:
        return True  # fail open on DB error so a corrupt DB doesn't lock everyone out

def _login_attempt_fail(ip):
    try:
        with _LOGIN_LOCK:
            conn = _get_throttle_db()
            now = _time.time()
            row = conn.execute(
                "SELECT count, window_start FROM login_attempts WHERE ip=?", (ip,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO login_attempts (ip, count, window_start) VALUES (?,?,?)",
                    (ip, 1, now)
                )
            else:
                cnt, first = row
                if now - first > _LOGIN_WINDOW:
                    cnt, first = 0, now
                conn.execute(
                    "INSERT OR REPLACE INTO login_attempts (ip, count, window_start) VALUES (?,?,?)",
                    (ip, cnt + 1, first)
                )
            conn.commit()
            conn.close()
    except Exception:
        pass

def _login_attempt_reset(ip):
    try:
        with _LOGIN_LOCK:
            conn = _get_throttle_db()
            conn.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
            conn.commit()
            conn.close()
    except Exception:
        pass

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _HTTP_AUTH_KEY:
            return f(*args, **kwargs)
        if _loopback_trusted():
            session['authenticated'] = True
            return f(*args, **kwargs)
        # Accept the ephemeral per-startup token embedded in the UI HTML.
        if request.headers.get("X-Friday-Token") == _API_SESSION_TOKEN:
            return f(*args, **kwargs)
        if not session.get("authenticated"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FRIDAY — Authenticate</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0ff;font-family:'Orbitron',monospace;display:flex;align-items:center;justify-content:center;min-height:100vh;overflow:hidden}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(ellipse at 50% 50%,rgba(124,58,237,.12) 0%,transparent 70%);pointer-events:none}
.login-box{background:rgba(15,15,30,.85);border:1px solid rgba(124,58,237,.35);border-radius:12px;padding:40px 36px;width:340px;backdrop-filter:blur(20px);box-shadow:0 0 40px rgba(124,58,237,.15),inset 0 0 30px rgba(124,58,237,.05);position:relative}
.login-box::before{content:'';position:absolute;top:-1px;left:20%;right:20%;height:2px;background:linear-gradient(90deg,transparent,rgba(124,58,237,.8),transparent);border-radius:2px}
h1{font-size:14px;letter-spacing:.25em;text-align:center;color:rgba(124,58,237,.9);margin-bottom:8px}
.subtitle{font-size:9px;letter-spacing:.15em;text-align:center;color:rgba(180,160,255,.4);margin-bottom:32px}
.field{margin-bottom:12px}
input[type=email],input[type=text],input[type=password]{width:100%;padding:12px 16px;background:rgba(124,58,237,.08);border:1px solid rgba(124,58,237,.25);border-radius:6px;color:#e0e0ff;font-family:'Orbitron',monospace;font-size:12px;letter-spacing:.15em;outline:none;transition:border-color .3s}
input[type=email]:focus,input[type=text]:focus,input[type=password]:focus{border-color:rgba(124,58,237,.7);box-shadow:0 0 15px rgba(124,58,237,.15)}
input::placeholder{color:rgba(180,160,255,.25)}
button{width:100%;padding:12px;margin-top:4px;background:linear-gradient(135deg,rgba(124,58,237,.3),rgba(124,58,237,.15));border:1px solid rgba(124,58,237,.4);border-radius:6px;color:rgba(200,180,255,.9);font-family:'Orbitron',monospace;font-size:11px;letter-spacing:.2em;cursor:pointer;transition:all .3s}
button:hover{background:linear-gradient(135deg,rgba(124,58,237,.45),rgba(124,58,237,.25));border-color:rgba(124,58,237,.7);box-shadow:0 0 20px rgba(124,58,237,.2)}
.error{color:#ff4466;font-size:9px;text-align:center;margin-top:12px;letter-spacing:.1em}
.scan-line{position:fixed;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,rgba(124,58,237,.15),transparent);animation:scan 4s linear infinite;pointer-events:none}
@keyframes scan{0%{top:0}100%{top:100vh}}
</style>
</head>
<body>
<div class="scan-line"></div>
<div class="login-box">
<h1>FRIDAY</h1>
<div class="subtitle">AUTHENTICATION REQUIRED</div>
<form method="POST">
<div class="field"><input type="email" name="username" placeholder="EMAIL / USERNAME" autofocus autocomplete="username"></div>
<div class="field"><input type="password" name="password" placeholder="PASSWORD" autocomplete="current-password"></div>
<button type="submit">AUTHENTICATE</button>
</form>
{{ error }}
</div>
</body>
</html>"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Loopback users are auto-authenticated — never show the form locally
    # (unless FRIDAY_TRUST_LOOPBACK=0 explicitly opts into local auth).
    if _loopback_trusted():
        session['authenticated'] = True
        session.permanent = True
        app.permanent_session_lifetime = timedelta(days=30)
        return redirect('/')
    if not _HTTP_AUTH_KEY:
        return redirect('/')
    error = ""
    if request.method == 'POST':
        ip = request.remote_addr or 'unknown'
        if not _login_attempt_ok(ip):
            error = '<div class="error">TOO MANY ATTEMPTS — WAIT AND RETRY</div>'
            html = LOGIN_HTML.replace('{{ error }}', error)
            return Response(html, content_type='text/html', status=429)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')  # pragma: allowlist secret
        # Constant-time comparison to avoid leaking credentials via timing.
        if _hmac.compare_digest(username, FRIDAY_USERNAME) and _hmac.compare_digest(password, _HTTP_AUTH_KEY):  # pragma: allowlist secret
            session['authenticated'] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
            _login_attempt_reset(ip)
            return redirect('/')
        _login_attempt_fail(ip)
        error = '<div class="error">ACCESS DENIED — INVALID CREDENTIALS</div>'
    html = LOGIN_HTML.replace('{{ error }}', error)
    return Response(html, content_type='text/html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ── Vibe Code: Terminal State ─────────────────────────────────
VIBE_TERMINALS = {}   # id -> { id, task, status, cwd, pid, started, stopped, log_file }
VIBE_LOG_DIR = Path(os.path.expanduser("~")) / ".friday" / "vibe-code-logs"
VIBE_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Paths ─────────────────────────────────────────────────────
HOME = Path(os.path.expanduser("~"))
FRIDAY_DIR = HOME / ".friday"
WIKI_DIR = FRIDAY_DIR / "wiki"

# Migrate wiki from ~/wiki (legacy location) to ~/.friday/wiki on first run.
_LEGACY_WIKI = HOME / "wiki"
if _LEGACY_WIKI.exists() and not WIKI_DIR.exists():
    try:
        import shutil as _shutil
        _shutil.copytree(str(_LEGACY_WIKI), str(WIKI_DIR))
        _LEGACY_WIKI.rename(_LEGACY_WIKI.parent / "wiki_migrated_to_friday")
    except Exception as _mig_err:
        import logging as _log
        _log.getLogger(__name__).warning("wiki migration ~/wiki → ~/.friday/wiki failed: %s", _mig_err)
# Captured ONCE, before anything creates ~/.friday: True only for a pristine
# first run. Drives the `show_all_workspaces` default — existing installs keep
# the full dock; fresh installs get the trimmed core set from the setup wizard.
_FRESH_INSTALL = not FRIDAY_DIR.exists()
_desktop = HOME / "Desktop"
CREATIONS_DIR = (_desktop / "friday-creations") if _desktop.exists() else (FRIDAY_DIR / "friday-creations")
# Daily Creation archive — JSON artifacts Friday generates once a day on a
# background schedule (distinct from the Desktop media gallery above).
DAILY_CREATIONS_DIR = FRIDAY_DIR / "creations"
WIKI_PROFESSIONAL_DIR = WIKI_DIR / "professional"
JOB_SEARCH_FILE = WIKI_PROFESSIONAL_DIR / "job-search.md"

# Ensure creations dirs exist
CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
DAILY_CREATIONS_DIR.mkdir(parents=True, exist_ok=True)


# ── File logging setup ─────────────────────────────────────────
# Now that FRIDAY_DIR is known, wire the rotating file handler. This runs once
# at import time. Using pythonw (no console) makes this the only debug output.
def _setup_friday_logging() -> None:
    root = logging.getLogger("friday")
    if root.handlers:
        return  # already configured (e.g. tests that import core twice)
    root.setLevel(logging.DEBUG)
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            FRIDAY_DIR / "friday.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        root.addHandler(fh)
    except Exception:
        pass  # never crash the server because of a logging setup failure
    # Always mirror WARNING+ to stderr so console launches still show issues.
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("[FRIDAY] %(levelname)s %(message)s"))
    root.addHandler(sh)


if not _TESTING:
    _setup_friday_logging()


# ── Env bootstrap from launch scripts ─────────────────────────
# The API keys live in start.bat / launch_now.bat as `set NAME=VALUE` lines. A
# server launched THROUGH those scripts inherits them — but one started any other
# way (IDE, a bare `python server.py`, a preview launcher, or a stale shell that
# predates the keys being added) has an empty environment, and every cloud call
# then dies with "No model provider could run the agent" even though the keys
# exist on disk. To make the keys reliably present however the process was
# started, parse the launch scripts here and fill in anything not already in the
# environment. os.environ ALWAYS wins (setdefault), so a real env var is never
# overridden. Best-effort and silent about values — never logs a secret.
def _bootstrap_env_from_launch_scripts():
    repo = Path(__file__).resolve().parents[3]  # __init__.py is src/agent_friday/core/__init__.py → repo root
    # Later files do not override earlier ones (setdefault); start.bat is primary.
    candidates = ['start.bat', 'launch_now.bat', 'friday_startup.bat']
    _set_re = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$',
                         re.IGNORECASE)
    # API keys ALWAYS come from start.bat — stale Windows User-scope env vars
    # (set months ago, now expired) would otherwise shadow the fresh key and
    # cause 1008 auth failures against Gemini Live or Anthropic.
    _FORCE_OVERRIDE = {
        'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY',
    }
    loaded = []
    for fname in candidates:
        p = repo / fname
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
                m = _set_re.match(line)
                if not m:
                    continue
                name, value = m.group(1), m.group(2).strip()
                if not value or value.startswith('%'):  # skip empty / %VAR% refs
                    continue
                if name in _FORCE_OVERRIDE or not os.environ.get(name):
                    os.environ[name] = value
                    loaded.append(name)
        except Exception as _be:
            print(f"  [FRIDAY] env bootstrap skipped {fname}: {_be}")
    if loaded:
        print(f"  [FRIDAY] Loaded {len(loaded)} key(s) from launch script: "
              + ", ".join(sorted(set(loaded))))


_bootstrap_env_from_launch_scripts()

# ── Gemini Client (lazy init) ─────────────────────────────────
_genai_client = None

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TEMP_AUDIO_DIR = FRIDAY_DIR / "audio-cache"
TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

def get_genai_client():
    global _genai_client, GEMINI_API_KEY
    if _genai_client is None:
        # Check settings.json for key saved via setup wizard
        if not GEMINI_API_KEY:
            GEMINI_API_KEY = _load_settings().get('gemini_api_key', '')  # pragma: allowlist secret
        try:
            from google import genai
            if GEMINI_API_KEY:
                _genai_client = genai.Client(api_key=GEMINI_API_KEY)  # pragma: allowlist secret
            else:
                print("  [FRIDAY] WARNING: No GEMINI_API_KEY set. Creative endpoints disabled.")
        except ImportError:
            print("  [FRIDAY] WARNING: google-genai not installed. Creative endpoints disabled.")
    return _genai_client


# ── Anthropic Claude (text reasoning + chat) ───────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL_DEFAULT = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
_anthropic_client = None


def get_anthropic_client():
    global _anthropic_client, ANTHROPIC_API_KEY
    if _anthropic_client is None:
        # Re-check the live environment (covers a key bootstrapped after import),
        # then settings.json (key saved via the setup wizard).
        if not ANTHROPIC_API_KEY:
            ANTHROPIC_API_KEY = (os.environ.get("ANTHROPIC_API_KEY", "")
                                 or _load_settings().get('anthropic_api_key', ''))
        if not ANTHROPIC_API_KEY:
            return None
        try:
            from anthropic import Anthropic
            _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)  # pragma: allowlist secret
        except ImportError:
            print("  [FRIDAY] WARNING: anthropic SDK not installed. Run: pip install anthropic")
            return None
    return _anthropic_client


# ── PII Privacy Shield ────────────────────────────────────────
# Lightweight redactor applied to outbound prompts and to tool outputs
# before they re-enter the model context. SSN + credit-card patterns are
# always redacted; additional watchlist tokens come from
# ~/.friday/privacy_shield.json => {"watchlist": ["...", ...]}
_PII_WATCHLIST_CACHE = {"mtime": 0.0, "items": []}
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    """True when a digit string passes the Luhn checksum (all real card
    numbers do). Keeps tracking numbers / file IDs from being redacted as
    cards, and is the standard for telling the two apart."""
    if not digits.isdigit() or len(digits) < 13:
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _watchlist_pattern(token: str):
    """Compile a watchlist token to a regex. Word-like tokens match on word
    boundaries only (so 'Smith' never corrupts 'SmithKline'); tokens with
    non-word edges (account numbers etc.) match literally."""
    esc = re.escape(token)
    left = r"\b" if token[:1].isalnum() else ""
    right = r"\b" if token[-1:].isalnum() else ""
    return re.compile(left + esc + right)


def _load_privacy_watchlist():
    path = FRIDAY_DIR / "privacy_shield.json"
    try:
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if mtime != _PII_WATCHLIST_CACHE["mtime"]:
            items = []
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                raw = data.get('watchlist') if isinstance(data, dict) else data
                if isinstance(raw, list):
                    items = [str(x) for x in raw if isinstance(x, (str, int, float)) and str(x).strip()]
            _PII_WATCHLIST_CACHE["mtime"] = mtime
            _PII_WATCHLIST_CACHE["items"] = items
    except Exception:
        pass
    return _PII_WATCHLIST_CACHE["items"]


def _pii_redact(text):
    """Redact SSNs, Luhn-valid card numbers, and watchlist tokens."""
    if not isinstance(text, str) or not text:
        return text
    out = _SSN_RE.sub("[REDACTED-SSN]", text)

    def _cc_sub(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return "[REDACTED-CC]"
        return m.group(0)

    out = _CC_RE.sub(_cc_sub, out)
    for token in _load_privacy_watchlist():
        if token:
            out = _watchlist_pattern(token).sub("[REDACTED]", out)
    return out


# ── PII Scrub/Rehydrate (bidirectional, tagged placeholders) ──
# Outbound: real PII is replaced with [PII:type:hash] markers; the agent sees
# stable references it can speak about without ever seeing the raw value.
# Inbound: the response is scanned for those markers and rehydrated from an
# in-memory lookup that NEVER touches disk and is rebuilt per request.

import hashlib as _hashlib
import hmac as _hmac

_PII_TAG_RE = re.compile(r"\[PII:[a-z]+:[0-9a-f]{8}\]")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?[2-9][0-9]{2}\)?[\s.\-]?[0-9]{3}[\s.\-]?[0-9]{4}(?!\d)")
# International (+country-code) numbers. The regex is deliberately loose; the
# substitution callback enforces ITU E.164 length (8-15 digits) so version
# strings, "+5 boost" and similar never match.
_INTL_PHONE_RE = re.compile(r"(?<![\d.\w])\+\d{1,3}(?:[\s.\-]?\(?\d{1,4}\)?){2,5}(?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][\w'.\-]*(?:\s+[A-Z][\w'.\-]*){0,5}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Boulevard|Blvd|Lane|Ln|"
    r"Court|Ct|Place|Pl|Trail|Trl|Tr|Way|Circle|Cir|Highway|Hwy|"
    r"Parkway|Pkwy|Terrace|Ter|Loop|Cove|Cv|Path|Square|Sq|Plaza|Pl)\b"
    r"(?:,?\s+(?:Apt|Apartment|Suite|Ste|Unit|#)\s*[\w\-]+)?"
    r"(?:,?\s+[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+)*)?"
    r"(?:,?\s+[A-Z]{2})?"
    r"(?:\s+\d{5}(?:-\d{4})?)?",
    re.IGNORECASE,
)
_ZIP_FALLBACK_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")


def _owner_emails():
    """Email addresses that belong to the user and should pass through unscrubbed."""
    try:
        settings = _load_settings()
        raw = settings.get('user_email') or settings.get('owner_email') or ''
        items = []
        if isinstance(raw, str) and raw.strip():
            items.append(raw.strip().lower())
        extras = settings.get('owner_identities') or []
        if isinstance(extras, list):
            for x in extras:
                if isinstance(x, str) and '@' in x:
                    items.append(x.strip().lower())
        return items
    except Exception:
        return []


def _pii_hash(val):
    return _hashlib.blake2b(val.encode('utf-8'), digest_size=4).hexdigest()


def _scrub_pii(text):
    """Replace PII with tagged placeholders. Returns (scrubbed_text, lookup_table).

    lookup_table maps tag -> original value. Caller passes it to _rehydrate_pii
    on the response. The table is created fresh per call and lives only in memory.
    """
    if not isinstance(text, str) or not text:
        return text, {}
    lookup = {}

    def _make_tag(kind, val):
        tag = f"[PII:{kind}:{_pii_hash(val)}]"
        lookup[tag] = val
        return tag

    out = text

    # 1. SSN
    out = _SSN_RE.sub(lambda m: _make_tag("ssn", m.group(0)), out)

    # 2. Credit cards (Luhn-validated — random digit runs stay untouched)
    def _cc_sub(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return _make_tag("cc", m.group(0))
        return m.group(0)
    out = _CC_RE.sub(_cc_sub, out)

    # 3. Phone numbers — international (+CC ...) first, then NANP.
    def _intl_sub(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 8 <= len(digits) <= 15:
            return _make_tag("phone", m.group(0))
        return m.group(0)
    out = _INTL_PHONE_RE.sub(_intl_sub, out)
    out = _PHONE_RE.sub(lambda m: _make_tag("phone", m.group(0)), out)

    # 4. Email — preserve the user's own addresses
    owner_set = set(_owner_emails())
    def _email_sub(m):
        addr = m.group(0)
        if addr.lower() in owner_set:
            return addr
        return _make_tag("email", addr)
    out = _EMAIL_RE.sub(_email_sub, out)

    # 5. Street address (best-effort US-style)
    out = _STREET_RE.sub(lambda m: _make_tag("addr", m.group(0)), out)

    # 6. Watchlist tokens (names, account numbers, etc.) — word-boundary
    #    matched so 'Smith' never corrupts 'SmithKline'.
    for token in _load_privacy_watchlist():
        if token:
            out = _watchlist_pattern(token).sub(
                lambda m, t=token: _make_tag("name", t), out)  # pragma: allowlist secret

    return out, lookup


def _rehydrate_pii(text, lookup):
    """Restore real PII values from tagged placeholders. Pure replacement."""
    if not isinstance(text, str) or not text or not lookup:
        return text
    out = text
    for tag, val in lookup.items():
        if tag in out:
            out = out.replace(tag, val)
    return out


# ── Full Context Log (append-only JSONL per day) ──────────────
CONTEXT_LOG_DIR = FRIDAY_DIR / "vault" / "context-log"

# ── Governance Decision BOM ───────────────────────────────────
DECISION_BOM_FILE = FRIDAY_DIR / "vault" / "decision-bom.jsonl"


def _context_logging_enabled():
    try:
        s = _load_settings()
        # Default ON unless explicitly disabled.
        return bool(s.get('context_logging_enabled', True))
    except Exception:
        return True


def _log_context(event_type, data):
    """Append an event to today's full context log. Silently no-ops if disabled."""
    try:
        if not _context_logging_enabled():
            return
        CONTEXT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        log_file = CONTEXT_LOG_DIR / f"{today}.jsonl"
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "type": event_type,
            "data": data,
        }
        with open(log_file, "a", encoding='utf-8') as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        # Logging must never break the request.
        print(f"  [CTX-LOG] {event_type} failed: {e}")

# Block list for run_command (case-insensitive substring match).
_RUN_COMMAND_BLOCKLIST = (
    "remove-item", "rmdir", "rd ", "del ", " del\t", "format ",
    "shutdown", "restart-computer", "stop-computer",
    "diskpart", "fdisk", "mkfs", "cipher /w",
    "reg delete", "reg add hklm",
    "icacls", "takeown",
    "schtasks /delete",
    "net user", "net localgroup",
    "invoke-webrequest -outfile", "iwr -outfile",
    "iex ", "invoke-expression",
    "wmic.*delete", "get-childitem.*remove",
    "rm -", "rmdir -",
)


def _safe_under_home(path_str):
    """Resolve a path and return it only if it stays within HOME."""
    try:
        p = Path(path_str).expanduser().resolve()
        home_resolved = HOME.resolve()
        # is_relative_to is 3.9+; emulate
        try:
            p.relative_to(home_resolved)
        except ValueError:
            return None
        return p
    except Exception:
        return None


# ── Tool execution sandbox ──────────────────────────────────────
# A defense layer in front of host-affecting tools, enforced in _execute_tool
# (every agent tool call funnels through it) ON TOP of the governance rings.
#   "off"     — no sandbox checks (legacy behavior)
#   "confine" — DEFAULT. Path tools (write_file/read_file) must stay under
#               FRIDAY_SANDBOX_ROOT; run_command keeps the destructive blocklist.
#   "strict"  — additionally, run_command is allowlist-only (leading token must
#               be in _RUN_COMMAND_ALLOW).
FRIDAY_SANDBOX_MODE = os.environ.get("FRIDAY_SANDBOX_MODE", "confine").lower()
FRIDAY_SANDBOX_ROOT = (os.environ.get("FRIDAY_SANDBOX_ROOT", "") or str(HOME))
# Leading commands allowed for run_command under "strict" mode.
_RUN_COMMAND_ALLOW = (
    "git", "python", "py", "pip", "pipx", "node", "npm", "npx", "pnpm", "yarn",
    "dir", "ls", "cat", "type", "echo", "cd", "pwd", "get-content", "set-location",
    "get-childitem", "select-string", "findstr", "where", "which", "test-path",
    "dotnet", "cargo", "go", "rustc", "pytest", "ruff", "black", "mypy", "flake8",
)
# Only WRITES are path-confined by default (reads are lower-risk and confining
# them would break legitimate reads of files outside HOME the user references).
# Add "read_file" here, or run strict mode, to also confine reads.
_SANDBOX_PATH_TOOLS = {"write_file": "path"}


def _sandbox_policy(name, args):
    """Sandbox gate run for every agent tool call, after the governance ring
    check. Returns (allowed, reason).

    - Confines path-affecting tools (write_file/read_file) to FRIDAY_SANDBOX_ROOT.
    - Keeps the destructive-command blocklist for run_command.
    - In "strict" mode, allowlists run_command's leading command.
    """
    if FRIDAY_SANDBOX_MODE in ("off", "0", "false", ""):
        return True, "sandbox off"
    args = args or {}

    field = _SANDBOX_PATH_TOOLS.get(name)
    if field:
        raw = str(args.get(field) or "").strip()
        if raw:
            try:
                p = Path(raw).expanduser().resolve()
                root = Path(FRIDAY_SANDBOX_ROOT).expanduser().resolve()
                p.relative_to(root)
            except ValueError:
                return False, f"path {raw!r} escapes sandbox root {FRIDAY_SANDBOX_ROOT}"
            except Exception as e:
                return False, f"path check failed: {e}"

    if name == "run_command":
        cmd = str(args.get("command") or "").strip()
        low = cmd.lower()
        for bad in _RUN_COMMAND_BLOCKLIST:
            if bad in low:
                return False, f"command matches destructive blocklist token {bad!r}"
        if FRIDAY_SANDBOX_MODE == "strict":
            lead = re.split(r"[\s|;&]+", low.lstrip("&; "), maxsplit=1)[0]
            lead = lead.replace("\\", "/").split("/")[-1]   # basename of an exe path
            if lead.endswith(".exe"):
                lead = lead[:-4]
            if lead and lead not in _RUN_COMMAND_ALLOW:
                return False, f"command {lead!r} not in strict allowlist"

    return True, "ok"


# ═══ PROCESS ORB REGISTRY (holographic Layer 2) ══════════════════
# Lightweight in-memory registry for active processes that the frontend
# renders as floating holographic orbs.  Skills/tasks register here via
# process_register() / process_update() and the frontend polls GET /api/processes.
PROCESSES = {}
PROCESSES_LOCK = threading.Lock()


def process_register(pid, *, name="Task", label=None, category="default",
                     icon="⚡", steps=None, model=None, color=None,
                     task_id=None):
    """Register a new process for the holographic orb display.

    `color` (optional int, e.g. 0x22c55e) overrides the category/local orb color
    in the 3-D scene — used for the green vault-access orb.
    `task_id` links this process to a TASKS entry so the notification detail
    panel can stream the underlying task's log.
    """
    with PROCESSES_LOCK:
        PROCESSES[pid] = {
            "id": pid,
            "name": name,
            "label": label or name,
            "category": category,
            "icon": icon,
            "model": model,
            "color": color,
            "status": "running",
            "progress": 0,
            "steps": steps or [],
            "log": [],
            "task_id": task_id,
            "started": _time.time(),
        }


def process_update(pid, *, status=None, progress=None, label=None,
                   step=None, steps=None, task_id=None):
    """Update an existing process entry."""
    with PROCESSES_LOCK:
        p = PROCESSES.get(pid)
        if not p:
            return
        if status is not None:
            p["status"] = status
        if progress is not None:
            p["progress"] = max(0.0, min(1.0, progress))
        if label is not None:
            p["label"] = label
        if step is not None:
            p["steps"].append(step)
        if steps is not None:
            p["steps"] = steps
        if task_id is not None:
            p["task_id"] = task_id
        if status in ("completed", "error"):
            p["ended"] = _time.time()


def process_log(pid, line: str):
    """Append a log line to a process so the notification detail panel shows activity."""
    with PROCESSES_LOCK:
        p = PROCESSES.get(pid)
        if p is not None:
            p.setdefault("log", []).append(str(line))
            if len(p["log"]) > 200:
                p["log"] = p["log"][-200:]


def process_remove(pid):
    """Remove a process from the registry."""
    with PROCESSES_LOCK:
        PROCESSES.pop(pid, None)


# ── Agent Settings (Reasoning style, personality, response prefs) ──
SETTINGS_FILE = FRIDAY_DIR / "settings.json"
AGENT_PERSONALITY_FILE = FRIDAY_DIR / "agent-personality.txt"

# In-memory settings cache — avoids hammering the filesystem on every API call.
# _load_settings_raw() returns a cached copy when the on-disk value is ≤2 s old;
# _save_settings() invalidates the cache immediately after writing so the next
# call always returns the freshly persisted value.
_SETTINGS_CACHE: dict = {"value": None, "ts": 0.0}
_SETTINGS_CACHE_TTL: float = 2.0  # seconds
_SETTINGS_CACHE_LOCK = threading.Lock()


def _invalidate_settings_cache() -> None:
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE["value"] = None
        _SETTINGS_CACHE["ts"] = 0.0

DEFAULT_AGENT_PERSONALITY = (
    "You are Friday — a calm, perceptive AI partner. "
    "You speak with quiet confidence and dry warmth; you favor signal over noise. "
    "You connect dots across the user's work and life without being asked twice. "
    "You give the answer first, then the reasoning. You are honest about uncertainty."
)

DEFAULT_SETTINGS = {
    "temperature": 0.7,
    "response_length": "standard",        # concise | standard | detailed
    "include_sources": True,
    "cite_sources": False,                # Source Production Mode — inline citations on every factual claim
    "memory_recall_enabled": True,        # RAG over persistent ChromaDB conversation memory
    "news_priorities": ["AI/Tech", "Politics", "Media", "Local", "Business"],
    "communication_style": "professional",  # professional | casual | technical
    "camera_interval_sec": 3,              # 1 | 3 | 5
    "camera_auto_describe": False,
    "tts_voice": "Aoede",                  # any of the 30 Gemini-TTS voices
    "voice_language": "",                  # BCP-47 (e.g. "en-US"); blank = server default
    "voice_style_prompt": "",              # free-text styling instruction passed to Gemini
    "voice_temperature": None,             # 0.0 – 2.0; null = SDK default
    "voice_max_tokens": 0,                 # cap response length in tokens; 0 = unlimited
    "voice_affective": True,               # Live API enable_affective_dialog
    "voice_proactive": True,               # Live API proactivity.proactive_audio
    "voice_context_compression": True,     # Live API sliding-window compression; ON by default so the context-window cap never silently terminates a long voice session (pairs with session_resumption renewal)
    "voice_barge_grace_ms": 800,           # ignore mic this long after Friday starts speaking (echo-canceller warmup)
    "voice_barge_sustain_ms": 200,         # deliberate speech must persist this long to interrupt playback
    # Interruption mode — how the Live API treats detected mic activity while
    # Friday is speaking. On SPEAKERS the mic re-captures Friday's own audio;
    # Gemini's VAD mistakes that echo for a barge-in and fires an interruption
    # that cuts her off mid-sentence. "speaker" = NO_INTERRUPTION (Google's
    # recommended echo-safe setting — her turn always finishes). "headphones" =
    # START_OF_ACTIVITY_INTERRUPTS (true barge-in; only safe when there's no
    # speaker bleed). Default speaker-safe because most users are on speakers.
    "voice_interruption_mode": "speaker",  # "speaker" (no-interruption) | "headphones" (barge-in)
    # ── Voice engine selection ──
    # LOCAL is the default; cloud (Gemini Live) is the opt-in. The mic button
    # resolves this via GET /api/voice/session-info → /ws/voice-local (local) or
    # /ws/live (gemini), degrading gracefully when an engine is unavailable.
    #   "local"     — Tier-1 on-device voice (faster-whisper + Piper, CPU), private/offline
    #   "local-gpu" — Tier-2 on-device voice (NVIDIA NeMo, GPU); auto-falls back to
    #                 Tier-1 CPU when no CUDA GPU / NeMo deps are present
    #   "gemini"    — Gemini Live cloud voice (most expressive; needs a key + network)
    #   "auto"      — GPU tier when ready, else CPU; local preferred over cloud
    "voice_engine": "local",
    "local_voice_asr_model": "small",      # Tier-1 faster-whisper size: tiny|base|small|medium
    "local_voice_tts_voice": "en_US-amy-medium",  # Tier-1 Piper voice id
    # Tier-2 (NeMo GPU) models — used only when voice_engine resolves to the GPU
    # tier. Override the ASR id to a sibling (e.g. the English-only streaming
    # model) if desired; the TTS pair (FastPitch+HiFi-GAN) is fixed for v1.
    "local_voice_gpu_asr_model": "nvidia/nemotron-3.5-asr-streaming-0.6b",
    "local_voice_gpu_tts": "fastpitch-hifigan",
    "voice_silence_ms": 800,               # trailing silence (ms) that ends a local-voice turn
    # ── Offline-first resilience ──
    # When the network monitor reports OFFLINE, _load_settings overlays
    # model_routing.mode='local_only' so every provider consumer auto-switches
    # to Ollama. Set False to keep the user's chosen routing mode even offline.
    "offline_auto_local": True,            # auto-switch to local models when offline
    "offline_queue_cloud_tasks": True,     # queue cloud content tasks while offline
    "offline_voice_fallback": True,        # fall back to local pyttsx3 TTS when Gemini is unreachable
    # ── Privacy / Context Log ──
    "context_logging_enabled": True,       # master switch for the append-only event log
    "context_retention_days": 0,           # 0 = keep forever; 30 / 90 / 180 / 365 = prune older
    "user_email": "",                      # the user's own email — passed through unscrubbed
    "off_record": False,                   # quick toggle — when true, chat is not logged either
    # ── Workspaces / Dock ──
    # When True the dock shows ALL workspaces (Finance, Health, Family, Trust,
    # Studio, Content, FutureSpeak); when False it shows only the
    # trimmed core set. Default is resolved per-install in _load_settings:
    # existing installs (~/.friday already present) → True; fresh installs → False.
    "show_all_workspaces": True,
    # ── Tool lifecycle hooks (Part B) ──
    # Each built-in PreToolUse/PostToolUse hook can be toggled here. Critical
    # hooks (governance_rings, vault_zt) ignore the toggle — they can't be
    # disabled from the UI. See services/tool_hooks.py + the built-ins registered
    # in services/agent.py (_register_builtin_tool_hooks).
    "tool_hooks": {
        "confirmation_gate": {"enabled": True},
        "governance_rings": {"enabled": True},
        "vault_zt": {"enabled": True},
        "sandbox_policy": {"enabled": True},
        "rate_limiter": {"enabled": True},
        "cost_attribution": {"enabled": True},
        "audit_log": {"enabled": True},
        "pii_scrub": {"enabled": True},
    },
    # Token-bucket caps for the rate_limiter hook (per ring, per minute). 0
    # disables limiting for that ring. Ring 0/1 (local reads/writes) are never
    # limited regardless.
    "rate_limiter": {
        "enabled": True,
        "ring2_per_min": 60,   # network ops (web/email/image/video/run_command…)
        "ring3_per_min": 20,   # full OS control (clicks, install_package…)
    },
    # ── Cost metering / budget alerts (Part D) ──
    # Per-call spend is recorded to ~/.friday/costs.db. These thresholds (USD)
    # drive budget-alert notifications: crossing 80% warns, 100% alerts. v1 is
    # alert-only — Friday is never silently blocked from working.
    "cost_budget": {
        "daily": 5.0,
        "monthly": 50.0,
        "daily_enabled": False,
        "monthly_enabled": False,
    },
    # ── Auto-compaction (Part C) ──
    # When the assembled transcript exceeds trigger_ratio × the model's context
    # window, the middle turns are summarized into a single "[Context Summary]"
    # note (head + tail preserved verbatim). Full history stays in ChromaDB.
    "compaction": {
        "enabled": True,
        "trigger_ratio": 0.70,    # fraction of the context window that triggers
        "context_window": 200000, # assumed model window for the ratio test
        "keep_head": 3,           # first N messages preserved (system + intent)
        "keep_tail": 10,          # last N messages preserved verbatim
        "summary_max_tokens": 400,
    },
    # ── Scheduler (Part A) ──
    # The schedule registry itself lives in ~/.friday/schedules.json (user-
    # editable from Settings → Scheduled Tasks). repo_sync.repos is the list of
    # absolute git-working-tree paths the deterministic repo-sync task pulls.
    "repo_sync": {
        "repos": [],
    },
    # ── Experimental ──
    "computer_control_enabled": False,     # opt-in gate for the pyautogui subsystem; OFF by
                                           # default. Even when True, each runtime grant is a
                                           # separate Ring-3 step (/api/control/permission).
    # ── Agent Identity & Model Selection ──
    "agent_name": "AGENT FRIDAY",
    # Claude Opus 4.8 is Anthropic's most capable currently-available model and
    # the top-priority cloud brain; fallback chain is
    # Opus 4.8 → Sonnet 4.6 → Haiku 4.5 (see model_router.CLOUD_MODEL_FALLBACK_CHAIN).
    # (Claude Fable 5 and Mythos 5 were pulled/recalled and are no longer offered.)
    "orchestrator_model": "claude-opus-4-8",      # main agent brain
    "subagent_model": "claude-sonnet-4-6",      # background tasks and drafts
    "creative_model": "gemini-nano-banana-2",   # image/creative generation (Nano Banana); video uses Veo
    "music_model": "lyria-clip",                # music generation (Lyria 3): 'lyria-clip' (≤30s) | 'lyria-pro' (full song)
    "voice_model": "gemini-3.1-flash-live-preview",  # live audio (3.1 Flash Live: server-side VAD barge-in; affective/proactive auto-stripped). Gemini 2.5 Flash is the voice family; Pro is text/reasoning, NOT voice/creative.
    # ── Creator Economy / Production (Layer 1) ──
    # Daily creation now chooses FREELY across all media (text/code/image/music/
    # video/full production), weighted by recent work + ambient mood + budget —
    # not a fixed rotation. The budget ceiling keeps "free choice" from ever
    # meaning "unbounded spend": expensive media are filtered out when the
    # remaining daily creative budget is low.
    "daily_creation_free_choice": True,         # False reverts to the legacy text rotation
    "daily_creation_budget_usd": 0.50,          # soft ceiling on a day's creation spend
    # ── Family / Minor mode (§7) ──
    # When on, generation runs an age-appropriate filter ON TOP of the adult harm
    # floor, and adult content is hidden in the gallery. This filters what the
    # minor sees, not what exists — a parent toggles it off in Settings.
    "minor_mode": False,
    # ── Semantic Context Pruning (RAG over our own conversation history) ──
    # When chat history exceeds max_turns, embedding-based retrieval keeps the
    # most relevant past turns instead of truncating from the oldest.
    # ── Per-workspace temperature profiles (creative pipeline) ──
    # The model router applies a sampling temperature based on the active
    # workspace so each surface gets the right creativity/determinism balance.
    # Only providers that accept `temperature` honor it (newer Claude models
    # ignore the param — see model_router._call_claude). Keys are workspace ids
    # (lowercase); values are 0.0–1.0 or null to use the provider default.
    "workspace_temperatures": {
        "studio": 0.75,        # Creative Studio — bold, vivid
        "creative": 0.75,
        "research": 0.25,      # Research — precise, conservative
        "news": 0.3,
        "code": 0.45,          # Code / Dev — exact
        "dev": 0.45,
        "content": 0.6,        # Content writing — balanced
        "chat": 0.5,           # Conversation — natural
        "review": 0.2,         # QA / review — strict
    },
    # ── Self-Evaluation (QA) gates (creative pipeline) ──
    # Before presenting significant generated content, Friday scores it against
    # intent and either silently improves it or flags the gap. Turn off for speed.
    "qa_gates": {
        "enabled": True,
        "threshold": 0.7,          # 0–1; below this the gate intervenes
        "max_retries": 1,          # silent-improve attempts in "improve" mode
        "mode": "improve",         # "improve" (regenerate) | "flag" (surface gap)
        "vision_for_images": True, # score images with a Gemini vision model
    },
    "context_pruning": {
        "enabled": True,
        "max_turns": 50,        # threshold (in turn pairs) before pruning kicks in
        "keep_recent": 4,       # always keep this many recent turn pairs verbatim
        "top_k": 10,            # semantically relevant archived turns to retrieve
        "model": "all-MiniLM-L6-v2",
    },
    # ── Headroom Context Compression (compresses the CONTENT of kept turns) ──
    # Runs right after pruning: Headroom squeezes JSON tool outputs, code, and
    # prose in the messages before they reach the API. 60-95% fewer tokens, same
    # answers. https://github.com/chopratejas/headroom — Tejas Chopra, Apache 2.0.
    "context_compression": {
        "enabled": True,
        "min_tokens_to_compress": 1000,  # skip compression below this payload size
    },
    # ── Model Routing (Ollama local inference) ──
    # mode: cloud_only (default, no change), smart, local_preferred, local_only
    "model_routing": {
        "mode": "cloud_only",
        "default_cloud_model": "claude-opus-4-8",
        "task_overrides": {},
        "ollama_url": "http://localhost:11434",
        # Default on-device model. Picked for every local route when installed
        # (see model_router._pick_local_model). Gemma-4 is multimodal and does
        # native OpenAI-style tool calling, so it can drive the full agent loop.
        "local_model": "gemma4:latest",
        "local_inference_slots": 3,
        "fallback_to_cloud": True,
        "cost_tracking": True,
        # ── OpenAI-compatible cloud provider (opt-in) ──
        # Set cloud_provider="openai" to route cloud turns through an
        # OpenAI-compatible endpoint instead of Anthropic. Unlocks OpenRouter
        # (hundreds of models) and any /v1 endpoint (Together, Groq, vLLM,
        # LM Studio, OpenAI). Full agentic tool loop is supported (tool use
        # requires a tool-calling-capable model at the endpoint). Default
        # "anthropic" leaves behavior unchanged.
        "cloud_provider": "anthropic",
        "openai_base_url": "https://openrouter.ai/api/v1",
        "openai_model": "anthropic/claude-3.7-sonnet",
        "openai_api_key": "",   # blank → falls back to env OPENAI_API_KEY / OPENROUTER_API_KEY
        # ── Sovereign Vault access control ──
        # vault_local_only: when true, vault TIER_2/TIER_3 content reaches local
        #   models only; vault-touching requests are force-routed to Ollama.
        # vault_cloud_fallback: what to do when a vault request can't run locally
        #   "redact" = send a placeholder to cloud, "deny" = refuse entirely,
        #   "warn"   = refuse and ask the user to enable a local model.
        "vault_local_only": True,
        "vault_cloud_fallback": "redact",
    },
    # ── Distribution profile (persona preset) ──
    # Mirror of the active distro (services/distributions.py). Applied as a
    # settings delta via POST /api/distros/<name>/apply.
    "distribution": "default",
    # Dock/workspace layout preset for the active distribution (standard |
    # journalism | developer | research | executive). Set by apply_distro.
    "dock_layout": "standard",
    # ── Demo mode ──
    # None = auto (Friday runs with canned responses when NO provider is
    # configured); True / False = explicit override. See services/demo_mode.py.
    "demo_mode": None,
    # ── Provider config (NEVER secrets) ──
    # name -> {"enabled": bool, "base_url"?: str}. API keys are stored encrypted
    # via services/credential_store.py, never here; availability is derived at
    # read time (provider_registry.is_provider_available + services.provider_health).
    "providers": {},
    # ── Per-capability routing (canonical) ──
    # capability -> {"provider", "model"}. The flat *_model keys above are derived
    # mirrors kept in sync by _sync_capability_routing(), so /api/health, the model
    # catalog and the voice/creative engines keep working unchanged. This is the
    # single source the wizard + Settings UI + services.capability_router share.
    "capability_routing": {
        "reasoning":      {"provider": "anthropic",     "model": "claude-opus-4-8"},
        "subagent":       {"provider": "anthropic",     "model": "claude-sonnet-4-6"},
        "creative_image": {"provider": "google-gemini", "model": "gemini-nano-banana-2"},
        "creative_video": {"provider": "google-gemini", "model": "veo-3"},
        "creative_music": {"provider": "google-gemini", "model": "lyria-clip"},
        "voice":          {"provider": "google-gemini", "model": "gemini-3.1-flash-live-preview"},
        # Local voice splits into asr + tts; both default to the on-device Tier-1
        # engine (the cloud "voice" entry above is used only when the user opts
        # into Gemini Live). A single user-facing `voice_engine` selector drives
        # these under the hood; power users can override each independently.
        "asr":            {"provider": "local-voice-lite", "model": "whisper-small"},
        "tts":            {"provider": "local-voice-lite", "model": "piper-en_US-amy-medium"},
        "embedding":      {"provider": "local",         "model": "all-MiniLM-L6-v2"},
        "local":          {"provider": "ollama-local",  "model": "gemma4:latest"},
    },
}

# capability_routing keys that mirror a legacy flat *_model setting.
_CAP_FLAT_MAP = {
    "reasoning": "orchestrator_model",
    "subagent": "subagent_model",
    "creative_image": "creative_model",
    "creative_music": "music_model",
    "voice": "voice_model",
}


def _sync_capability_routing(settings, changed=None):
    """Keep capability_routing (canonical) and the legacy flat *_model keys congruent.

    Priority rule: the flat model key wins when it is explicitly present in
    ``changed``.  A ``capability_routing`` entry propagates to the flat key only
    when it is in ``changed`` and the corresponding flat key is NOT (i.e. the
    wizard or routing panel changed routing without touching the picker).  This
    prevents the model-picker snap-back bug where the UI sends a full settings
    blob containing stale ``capability_routing`` and the old routing model
    overwrites the newly-chosen flat key.  Unmapped capabilities (creative_video,
    embedding, local) come straight from routing/defaults.  Copy-safe — never
    mutates the shared DEFAULT_SETTINGS nested dicts.
    """
    defaults = DEFAULT_SETTINGS["capability_routing"]
    src = settings.get("capability_routing")
    cr = {}
    for cap, dflt in defaults.items():
        cur = src.get(cap) if isinstance(src, dict) else None
        cr[cap] = dict(cur) if isinstance(cur, dict) else dict(dflt)
    settings["capability_routing"] = cr
    changed = changed or {}
    delta_cr = changed.get("capability_routing")
    delta_cr = delta_cr if isinstance(delta_cr, dict) else {}
    changed_keys = set((changed or {}).keys())
    for cap, flat_key in _CAP_FLAT_MAP.items():
        entry = cr[cap]
        # Flat key wins when it was explicitly set by the caller (the model picker
        # always sends the full settings blob, which includes a stale
        # capability_routing — without this guard, _sync would overwrite the
        # newly-chosen model with the old capability_routing value, causing the
        # snap-back bug).
        if cap in delta_cr and flat_key not in changed_keys:
            # capability_routing changed and flat key was NOT touched → mirror
            if entry.get("model"):
                settings[flat_key] = entry["model"]
        else:
            # flat key is authoritative (either explicitly set, or routing unchanged)
            fv = settings.get(flat_key)
            if fv:
                entry["model"] = fv
        if not entry.get("provider"):
            entry["provider"] = defaults[cap]["provider"]
    return settings


def _load_settings_raw():
    """Load agent settings exactly as persisted (no offline overlay).

    Results are cached for up to _SETTINGS_CACHE_TTL seconds so rapid
    sequential API calls don't hammer the filesystem. The cache is
    invalidated by _save_settings() and _invalidate_settings_cache().
    """
    with _SETTINGS_CACHE_LOCK:
        if (_SETTINGS_CACHE["value"] is not None
                and (_time.time() - _SETTINGS_CACHE["ts"]) < _SETTINGS_CACHE_TTL):
            return dict(_SETTINGS_CACHE["value"])

    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        seed = dict(DEFAULT_SETTINGS)
        # Fresh installs start on the trimmed core dock; existing installs (that
        # had ~/.friday before this version added the setting) keep the full set.
        seed["show_all_workspaces"] = not _FRESH_INSTALL
        _sync_capability_routing(seed)
        try:
            SETTINGS_FILE.write_text(json.dumps(seed, indent=2), encoding='utf-8')
        except Exception:
            pass
        with _SETTINGS_CACHE_LOCK:
            _SETTINGS_CACHE["value"] = seed
            _SETTINGS_CACHE["ts"] = _time.time()
        return seed
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        # Fill in any missing keys with defaults
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        _sync_capability_routing(merged)
        with _SETTINGS_CACHE_LOCK:
            _SETTINGS_CACHE["value"] = merged
            _SETTINGS_CACHE["ts"] = _time.time()
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def _load_settings():
    """Load agent settings, applying the offline routing overlay when offline.

    The overlay is non-persistent: it forces local inference while the network
    monitor reports OFFLINE so every provider consumer auto-switches to Ollama,
    then disappears the moment connectivity returns. Use _load_settings_raw()
    when you need the persisted values verbatim (e.g. before a settings write).
    """
    return _apply_offline_routing_overlay(_load_settings_raw())


def _save_settings(data):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    # Invalidate the cache first so any concurrent reader re-loads from disk
    # once we're done writing, not from the stale pre-write snapshot.
    _invalidate_settings_cache()
    # Read existing file first to preserve any keys not in DEFAULT_SETTINGS
    existing = {}
    if SETTINGS_FILE.exists():
        try:
            existing = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in existing.items()})
    for k, v in (data or {}).items():
        merged[k] = v
    # Reconcile capability_routing ⇄ flat *_model keys before persisting so the
    # wizard, Settings UI and router never disagree about the active models.
    _sync_capability_routing(merged, data)
    # Atomic write: write to a sibling temp file, fsync, then rename so a crash
    # mid-write never leaves a half-written (corrupt) settings.json.
    _tmp = SETTINGS_FILE.with_suffix('.tmp')
    _tmp.write_text(json.dumps(merged, indent=2), encoding='utf-8')
    try:
        with open(_tmp, 'rb') as _f:
            os.fsync(_f.fileno())
    except Exception:
        pass
    _tmp.replace(SETTINGS_FILE)
    return merged


# ══════════════════════════════════════════════════════════════
#  NETWORK RESILIENCE  (offline-first state + routing overlay)
# ══════════════════════════════════════════════════════════════
# The network monitor loop (services.notifications._network_monitor_loop) pings
# a reliable host every 30s and updates NETWORK_STATE. When the status is
# 'offline', _load_settings() transparently overlays model_routing so every
# provider consumer (chat, _generate_text, voice routing) switches to local
# Ollama inference — no settings write, no per-call-site change. The state is
# read by GET /api/system/network-status and drives the UI offline badge.
NETWORK_STATE = {
    "status": "unknown",       # 'online' | 'degraded' | 'offline' | 'unknown'
    "since": _time.time(),     # epoch when the current status began
    "last_check": 0.0,         # epoch of the most recent probe
    "last_online": 0.0,        # epoch we were last fully online
    "latency_ms": None,        # round-trip of the last successful probe
    "host": "",                # host that answered (or was tried) last
    "consecutive_failures": 0,
}
_NETWORK_LOCK = threading.Lock()


def _network_status():
    """Thread-safe snapshot of the current network state."""
    with _NETWORK_LOCK:
        snap = dict(NETWORK_STATE)
    snap["offline"] = snap["status"] == "offline"
    snap["online"] = snap["status"] == "online"
    return snap


def _network_is_offline():
    with _NETWORK_LOCK:
        return NETWORK_STATE["status"] == "offline"


def _set_network_state(status, **fields):
    """Update NETWORK_STATE and return (old_status, new_status).

    `since` advances only on an actual status change so the UI can show how long
    we've been in the current state. Extra fields (latency_ms, host, …) are
    merged verbatim. Returns the transition so the caller can fire side effects
    (flush the offline queue, refresh feeds, push a notification) exactly once.
    """
    with _NETWORK_LOCK:
        old = NETWORK_STATE["status"]
        now = _time.time()
        if status != old:
            NETWORK_STATE["since"] = now
        NETWORK_STATE["status"] = status
        NETWORK_STATE["last_check"] = now
        if status == "online":
            NETWORK_STATE["last_online"] = now
        for k, v in fields.items():
            NETWORK_STATE[k] = v
    return old, status


def _apply_offline_routing_overlay(settings):
    """Force local inference while offline, without persisting the change.

    Returns settings unchanged when online or when offline_auto_local is off.
    When offline it returns a shallow copy with model_routing.mode='local_only'
    and fallback_to_cloud=False so the router never tries an unreachable cloud
    endpoint. Never persisted — _save_settings writes from the file/the caller's
    delta, not from this overlaid dict.
    """
    try:
        with _NETWORK_LOCK:
            offline = NETWORK_STATE["status"] == "offline"
        if not offline or not settings.get("offline_auto_local", True):
            return settings
        mr = dict(settings.get("model_routing") or {})
        if mr.get("mode") != "local_only" or mr.get("fallback_to_cloud") is not False:
            mr["mode"] = "local_only"
            mr["fallback_to_cloud"] = False
            settings = dict(settings)
            settings["model_routing"] = mr
    except Exception:
        pass
    return settings


def _ollama_available():
    """True if a local Ollama server is reachable (best-effort, never raises)."""
    try:
        from agent_friday.routing.ollama_manager import get_manager
        cfg = (_load_settings_raw().get("model_routing") or {})
        return bool(get_manager(cfg.get("ollama_url", "http://localhost:11434")).is_available())
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
#  OFFLINE TASK QUEUE  (~/.friday/offline_queue/)
# ══════════════════════════════════════════════════════════════
# Cloud-dependent tasks issued while offline (or that local inference can't
# satisfy) are persisted here as one JSON file per entry and replayed by
# services.notifications._flush_offline_queue() the moment connectivity returns.
OFFLINE_QUEUE_DIR = FRIDAY_DIR / "offline_queue"
_OFFLINE_QUEUE_LOCK = threading.Lock()


def _offline_queue_add(kind, payload=None, *, dedupe_key=None):
    """Persist a task to run when connectivity returns. Returns the entry dict."""
    try:
        with _OFFLINE_QUEUE_LOCK:
            OFFLINE_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
            qid = re.sub(r"[^0-9a-zA-Z_-]", "", str(dedupe_key or uuid.uuid4().hex[:12])) or uuid.uuid4().hex[:12]
            entry = {
                "id": qid,
                "kind": kind,
                "payload": payload or {},
                "queued_at": datetime.now().isoformat(timespec="seconds"),
            }
            (OFFLINE_QUEUE_DIR / f"{qid}.json").write_text(
                json.dumps(entry, indent=2), encoding="utf-8")
            return entry
    except Exception as e:
        print(f"  [offline-queue] add failed: {e}")
        return None


def _offline_queue_list():
    """All queued entries, oldest first."""
    out = []
    if OFFLINE_QUEUE_DIR.exists():
        for p in sorted(OFFLINE_QUEUE_DIR.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
    out.sort(key=lambda e: e.get("queued_at", ""))
    return out


def _offline_queue_remove(qid):
    """Delete one queued entry by id. Returns True if it existed."""
    try:
        safe = re.sub(r"[^0-9a-zA-Z_-]", "", str(qid))
        p = OFFLINE_QUEUE_DIR / f"{safe}.json"
        if p.exists():
            p.unlink()
            return True
    except Exception:
        pass
    return False


def _offline_should_queue():
    """True when a cloud content task should be queued rather than run now.

    Only queues when we're genuinely offline, queuing is enabled, AND there's no
    local model that could satisfy the request — so an offline user with Ollama
    still gets results immediately instead of a deferred task.
    """
    try:
        if not _network_is_offline():
            return False
        if not _load_settings_raw().get("offline_queue_cloud_tasks", True):
            return False
        return not _ollama_available()
    except Exception:
        return False


def _load_agent_personality():
    """Load custom agent personality, falling back to default."""
    if AGENT_PERSONALITY_FILE.exists():
        try:
            text = AGENT_PERSONALITY_FILE.read_text(encoding='utf-8').strip()
            if text:
                return text
        except Exception:
            pass
    return DEFAULT_AGENT_PERSONALITY


def _save_agent_personality(text):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_PERSONALITY_FILE.write_text((text or '').strip(), encoding='utf-8')


# ── Self-knowledge (SELF.md) ─────────────────────────────────
_self_knowledge_cache = None
_self_knowledge_mtime = 0.0
SELF_MD_PATH = _RES_DIR / "SELF.md"


def _load_self_knowledge():
    """Lazily load SELF.md — Friday's self-knowledge document.

    Cached in memory and invalidated only when the file's mtime changes, so
    the disk hit happens at most once per file edit (not once per chat turn).
    Returns the full text or an empty string if the file is missing.
    """
    global _self_knowledge_cache, _self_knowledge_mtime
    try:
        mtime = SELF_MD_PATH.stat().st_mtime
    except (OSError, FileNotFoundError):
        _self_knowledge_cache = ""
        return ""
    if _self_knowledge_cache is not None and mtime == _self_knowledge_mtime:
        return _self_knowledge_cache
    try:
        text = SELF_MD_PATH.read_text(encoding='utf-8').strip()
    except Exception:
        text = ""
    _self_knowledge_cache = text
    _self_knowledge_mtime = mtime
    return text


# ── Voice demo spec sheet (VOICE_DEMO.md) ────────────────────
# Public (Tier 1) product/marketing knowledge spoken aloud in voice mode.
# Unlike SELF.md, this is NEVER vault-gated — it's the answer to "what are you?"
# that Gemini Live always has, so Friday can describe himself to anyone without
# the vault redacting his own product pitch.
_voice_demo_cache = None
_voice_demo_mtime = 0.0
VOICE_DEMO_MD_PATH = _RES_DIR / "VOICE_DEMO.md"


def _load_voice_demo():
    """Lazily load VOICE_DEMO.md — Friday's spoken, ungated spec sheet.

    Cached and mtime-invalidated like SELF.md, so the disk hit happens at most
    once per file edit. Returns the full text, or an empty string if missing.
    This content is Tier 1 (public) and must never be passed through vault
    gating — it is product marketing, not sensitive data.
    """
    global _voice_demo_cache, _voice_demo_mtime
    try:
        mtime = VOICE_DEMO_MD_PATH.stat().st_mtime
    except (OSError, FileNotFoundError):
        _voice_demo_cache = ""
        return ""
    if _voice_demo_cache is not None and mtime == _voice_demo_mtime:
        return _voice_demo_cache
    try:
        text = VOICE_DEMO_MD_PATH.read_text(encoding='utf-8').strip()
    except Exception:
        text = ""
    _voice_demo_cache = text
    _voice_demo_mtime = mtime
    return text


def _settings_system_prefix(settings, personality):
    """Build the prefix that gets prepended to every chat system prompt."""
    length_hint = {
        'concise': 'Be terse — 1–3 sentences unless detail is explicitly required.',
        'standard': 'Be reasonably brief — direct answer plus the minimum useful context.',
        'detailed': 'Be thorough — explain reasoning, list options, surface tradeoffs.',
    }.get(settings.get('response_length', 'standard'), '')
    style_hint = {
        'professional': 'Tone: composed, professional, plainspoken.',
        'casual':       'Tone: relaxed and conversational, like a trusted colleague.',
        'technical':    'Tone: precise and technical; use exact terminology and code where helpful.',
    }.get(settings.get('communication_style', 'professional'), '')
    sources_hint = ('Always cite the source (workspace, wiki, trust graph, vision, etc.) inline when you draw on it.'
                    if settings.get('include_sources', True) else
                    'You may omit source citations unless the user asks.')
    priorities = settings.get('news_priorities') or []
    priority_hint = ('News and topic priorities (descending): ' + ', '.join(priorities) + '.') if priorities else ''

    laws = (
        "== ASIMOV cLAWS (compiled, non-negotiable) ==\n"
        "1. An Asimov agent shall not harm a human being or, through inaction, allow harm.\n"
        "2. An Asimov agent shall obey user instructions except where they conflict with the First Law.\n"
        "3. An Asimov agent shall protect its own integrity except where this conflicts with the First or Second Laws.\n"
        "4. All behavioral constraints are cryptographically signed (HMAC-SHA256) and verified before every action."
    )

    return "\n".join([
        "== AGENT PERSONALITY ==",
        personality,
        "",
        "== RESPONSE PREFERENCES ==",
        length_hint,
        style_hint,
        sources_hint,
        priority_hint,
        "",
        laws,
    ]).strip() + "\n"


@app.before_request
def check_auth():
    # Loopback / same-machine access is always trusted — auto-authenticate the
    # session so the user never sees a login screen on their own device.
    # Remote access (e.g. via Cloudflare Tunnel) still goes through the
    # token/key gate below.
    if _loopback_trusted():
        if not session.get("authenticated"):
            session['authenticated'] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
        return None
    if not _HTTP_AUTH_KEY:
        return None
    if request.endpoint in ('login', 'serve_static_asset', 'serve_favicon'):
        return None
    if request.path.startswith('/ws/'):
        return None  # WebSocket upgrade handled inside ws_live (can't send HTTP redirect)
    # Accept the ephemeral per-startup token (embedded in the served HTML).
    if request.headers.get("X-Friday-Token") == _API_SESSION_TOKEN:
        return None
    if not session.get("authenticated"):
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login"))


# ═══════════════════════════════════════════════════════════════
#  CONTEXT LOG (append-only JSONL per day, vault-scoped)
# ═══════════════════════════════════════════════════════════════

def _context_log_files(date_from=None, date_to=None):
    """Yield (date_str, Path) for log files in the inclusive range."""
    if not CONTEXT_LOG_DIR.exists():
        return
    files = []
    for f in sorted(CONTEXT_LOG_DIR.glob("*.jsonl")):
        d = f.stem
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        files.append((d, f))
    return files

# ── Persistent Chat History ────────────────────────────────────
CHAT_HISTORY_FILE = FRIDAY_DIR / "chat_history.json"

def _load_chat_history():
    """Load chat history from disk, pruning entries older than 30 days (except pinned)."""
    if CHAT_HISTORY_FILE.exists():
        try:
            messages = json.loads(CHAT_HISTORY_FILE.read_text(encoding='utf-8'))
            cutoff = (datetime.now() - timedelta(days=30)).isoformat()
            return [m for m in messages if m.get('pinned') or m.get('timestamp', '') >= cutoff]
        except Exception:
            return []
    return []

def _save_chat_history(messages):
    """Persist chat history to disk."""
    CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_HISTORY_FILE.write_text(json.dumps(messages, indent=2), encoding='utf-8')

CHAT_HISTORY = _load_chat_history()  # Load persistent history on startup


# ══════════════════════════════════════════════════════════════
#  SETUP WIZARD  (first-run onboarding, Hermes-inspired)
# ══════════════════════════════════════════════════════════════
_SETUP_MARKER = FRIDAY_DIR / ".setup_complete"


def _is_existing_install() -> bool:
    """True if this looks like an existing installation that should skip the wizard."""
    if _SETUP_MARKER.exists():
        return True
    # settings.json exists with setup_complete flag
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
            if data.get('setup_complete'):
                return True
        except Exception:
            pass
    # personality.json exists → user has customised the agent
    if (FRIDAY_DIR / "personality.json").exists():
        return True
    return False


# ══════════════════════════════════════════════════════════════
#  FEATURE FLAGS  (replaces scattered try/except blocks)
# ══════════════════════════════════════════════════════════════

class FeatureFlags:
    """Detect which optional subsystems are available at runtime.

    Call FeatureFlags.detect() once at startup; the result is exposed via
    GET /api/health so the UI can show which features are active without
    each route independently probing for optional dependencies.
    """
    __slots__ = (
        "vault_access", "cognitive_memory", "dynamic_rings", "proof_of_integrity",
        "trust_graphs", "behavioral_monitor", "flask_sock", "chromadb",
        "local_voice", "nemo_voice", "ollama", "anthropic_sdk", "google_genai",
        "skill_hot_reload",
    )

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot, False))

    @classmethod
    def detect(cls) -> "FeatureFlags":
        """Probe each optional subsystem and return a populated FeatureFlags."""
        flags: dict = {}

        def _probe(key, mod, *attrs):
            try:
                import importlib
                m = importlib.import_module(mod)
                flags[key] = all(hasattr(m, a) for a in attrs) if attrs else True
            except Exception:
                flags[key] = False

        _probe("vault_access",       "agent_friday.privacy.vault_access",       "Tier", "VaultAccessControl")
        _probe("cognitive_memory",   "agent_friday.cognitive_memory",            "get_cognitive_memory")
        _probe("dynamic_rings",      "agent_friday.dynamic_rings",               "get_privilege_manager")
        _probe("proof_of_integrity", "agent_friday.governance.proof_of_integrity", "get_integrity_engine")
        _probe("trust_graphs",       "agent_friday.people_graph",               "get_people_graph")
        _probe("behavioral_monitor", "agent_friday.governance.behavioral_monitor", "get_behavioral_monitor")
        _probe("flask_sock",         "flask_sock",                               "Sock")
        _probe("local_voice",        "agent_friday.services.local_voice",        "LocalVoiceEngine")
        _probe("nemo_voice",         "agent_friday.services.nemo_voice",         "NeMoVoiceEngine")
        _probe("anthropic_sdk",      "anthropic",                                "Anthropic")
        _probe("google_genai",       "google.genai",                             "Client")
        _probe("skill_hot_reload",   "importlib",                                "reload")

        try:
            import chromadb  # noqa: F401
            flags["chromadb"] = True
        except Exception:
            flags["chromadb"] = False

        try:
            import requests as _r  # type: ignore
            _r.get("http://localhost:11434/api/tags", timeout=1)
            flags["ollama"] = True
        except Exception:
            flags["ollama"] = False

        return cls(**flags)

    def as_dict(self) -> dict:
        return {slot: getattr(self, slot) for slot in self.__slots__}


# Singleton — detected once at module-load; refresh via FeatureFlags.detect()
FEATURE_FLAGS: FeatureFlags = FeatureFlags.detect()



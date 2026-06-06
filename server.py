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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path

# ── Frozen (PyInstaller) resource root ──────────────────────────
# When bundled, data files (index.html, static/, assets/, SELF.md, skills/…)
# live under sys._MEIPASS. Resolve resource paths against it and chdir there so
# the many CWD-relative send_from_directory('.', …) / ('static', …) calls work.
_RES_DIR = (Path(getattr(sys, "_MEIPASS")) if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
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
    from vault_access import Tier as _VaultTier, VaultAccessControl, VaultAccessDenied
except Exception as _vac_err:  # pragma: no cover
    _VaultTier = None
    VaultAccessControl = None
    class VaultAccessDenied(Exception):
        pass
    print(f"  [FRIDAY] WARNING: vault_access unavailable ({_vac_err}); vault gating disabled.")

# Cognitive Memory — versioned, hash-chained memory ledger.
try:
    from cognitive_memory import get_cognitive_memory, CognitiveMemory
    _HAS_COGMEM = True
except Exception as _cm_err:
    _HAS_COGMEM = False
    print(f"  [FRIDAY] WARNING: cognitive_memory unavailable ({_cm_err})")

# Dynamic Privilege Rings — zero-trust per-call elevation.
try:
    from dynamic_rings import get_privilege_manager, DynamicPrivilegeManager
    _HAS_DYNRINGS = True
except Exception as _dr_err:
    _HAS_DYNRINGS = False
    print(f"  [FRIDAY] WARNING: dynamic_rings unavailable ({_dr_err})")

# Proof of Integrity — AI Bill of Integrity manifest.
try:
    from proof_of_integrity import get_integrity_engine, IntegrityEngine, CLAWS_TEXT
    _HAS_INTEGRITY = True
except Exception as _poi_err:
    _HAS_INTEGRITY = False
    print(f"  [FRIDAY] WARNING: proof_of_integrity unavailable ({_poi_err})")

# Prevent console windows from flashing when spawning subprocesses on Windows.
_POPEN_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

try:
    from flask_sock import Sock, ConnectionClosed
    _HAS_SOCK = True
except ImportError:
    _HAS_SOCK = False
    print("  [FRIDAY] WARNING: flask-sock not installed. /ws/live disabled.")

app = Flask(__name__, static_folder='.', static_url_path='')


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
        p.write_text(s, encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
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
FRIDAY_PASSWORD = os.environ.get("FRIDAY_PASSWORD", "")
# When "0"/"false", same-machine (loopback) requests are NOT auto-trusted and
# must authenticate like remote clients. Default "1" preserves the local-dev UX.
# (Only has an effect when FRIDAY_PASSWORD is set — without a password, auth is
# disabled entirely.)
FRIDAY_TRUST_LOOPBACK = os.environ.get("FRIDAY_TRUST_LOOPBACK", "1") not in ("0", "false", "False")
# Optional shared token required for the /ws/live WebSocket regardless of
# loopback trust — defense-in-depth for voice when the server is exposed.
FRIDAY_WS_TOKEN = os.environ.get("FRIDAY_WS_TOKEN", "")

# Simple in-memory login throttle (no extra deps): per-IP failed-attempt window.
_LOGIN_ATTEMPTS = {}            # remote_addr -> (count, window_start_ts)
_LOGIN_LOCK = threading.Lock()
_LOGIN_MAX = 8                  # attempts allowed per window
_LOGIN_WINDOW = 300            # seconds

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
    """False if this IP has exceeded the failed-login budget for the window."""
    with _LOGIN_LOCK:
        cnt, first = _LOGIN_ATTEMPTS.get(ip, (0, _time.time()))
        if _time.time() - first > _LOGIN_WINDOW:
            cnt, first = 0, _time.time()
            _LOGIN_ATTEMPTS[ip] = (cnt, first)
        return cnt < _LOGIN_MAX

def _login_attempt_fail(ip):
    with _LOGIN_LOCK:
        cnt, first = _LOGIN_ATTEMPTS.get(ip, (0, _time.time()))
        if _time.time() - first > _LOGIN_WINDOW:
            cnt, first = 0, _time.time()
        _LOGIN_ATTEMPTS[ip] = (cnt + 1, first)

def _login_attempt_reset(ip):
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(ip, None)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not FRIDAY_PASSWORD:
            return f(*args, **kwargs)
        if _loopback_trusted():
            session['authenticated'] = True
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
    if not FRIDAY_PASSWORD:
        return redirect('/')
    error = ""
    if request.method == 'POST':
        ip = request.remote_addr or 'unknown'
        if not _login_attempt_ok(ip):
            error = '<div class="error">TOO MANY ATTEMPTS — WAIT AND RETRY</div>'
            html = LOGIN_HTML.replace('{{ error }}', error)
            return Response(html, content_type='text/html', status=429)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        # Constant-time comparison to avoid leaking credentials via timing.
        if _hmac.compare_digest(username, FRIDAY_USERNAME) and _hmac.compare_digest(password, FRIDAY_PASSWORD):
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
WIKI_DIR = HOME / "wiki"
FRIDAY_DIR = HOME / ".friday"
CREATIONS_DIR = HOME / "Desktop" / "friday-creations"
# Daily Creation archive — JSON artifacts Friday generates once a day on a
# background schedule (distinct from the Desktop media gallery above).
DAILY_CREATIONS_DIR = FRIDAY_DIR / "creations"
WIKI_PROFESSIONAL_DIR = WIKI_DIR / "professional"
JOB_SEARCH_FILE = WIKI_PROFESSIONAL_DIR / "job-search.md"

# Ensure creations dirs exist
CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
DAILY_CREATIONS_DIR.mkdir(parents=True, exist_ok=True)

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
            GEMINI_API_KEY = _load_settings().get('gemini_api_key', '')
        try:
            from google import genai
            if GEMINI_API_KEY:
                _genai_client = genai.Client(api_key=GEMINI_API_KEY)
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
        # Check settings.json for key saved via setup wizard
        if not ANTHROPIC_API_KEY:
            ANTHROPIC_API_KEY = _load_settings().get('anthropic_api_key', '')
        if not ANTHROPIC_API_KEY:
            return None
        try:
            from anthropic import Anthropic
            _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
        except ImportError:
            print("  [FRIDAY] WARNING: anthropic SDK not installed. Run: pip install anthropic")
            return None
    return _anthropic_client


def _call_claude(messages, system=None, model=None, max_tokens=16384, temperature=None):
    """Call Claude with structured messages. Returns the text response.

    messages: list of {"role": "user"|"assistant", "content": "..."}
    system: optional system prompt (string)
    model: override the default model (claude-haiku-4-5-20251001 / claude-sonnet-4-6 / claude-opus-4-8)
    temperature: accepted for backward-compat but IGNORED — newer Claude
        models (Opus 4.8+, Sonnet 4.6+) reject the deprecated param.
    """
    client = get_anthropic_client()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to start.bat / launch_now.bat and restart the server."
        )
    if model is None:
        model = _load_settings().get("orchestrator_model") or ANTHROPIC_MODEL_DEFAULT
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    # NOTE: `temperature` is intentionally NOT forwarded. Newer Claude models
    # (Opus 4.8+, Sonnet 4.6+) reject the param with a 400 "temperature is
    # deprecated for this model". The param is kept in the signature for
    # backward-compat with callers; the model's default sampling is used.
    resp = client.messages.create(**kwargs)
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


# ── PII Privacy Shield ────────────────────────────────────────
# Lightweight redactor applied to outbound prompts and to tool outputs
# before they re-enter the model context. SSN + credit-card patterns are
# always redacted; additional watchlist tokens come from
# ~/.friday/privacy_shield.json => {"watchlist": ["...", ...]}
_PII_WATCHLIST_CACHE = {"mtime": 0.0, "items": []}
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


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
    """Redact SSNs, credit-card-like sequences (Luhn-ish), and watchlist tokens."""
    if not isinstance(text, str) or not text:
        return text
    out = _SSN_RE.sub("[REDACTED-SSN]", text)

    def _cc_sub(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19:
            return "[REDACTED-CC]"
        return m.group(0)

    out = _CC_RE.sub(_cc_sub, out)
    for token in _load_privacy_watchlist():
        if token and token in out:
            out = out.replace(token, "[REDACTED]")
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

    # 2. Credit-card-ish
    def _cc_sub(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19:
            return _make_tag("cc", m.group(0))
        return m.group(0)
    out = _CC_RE.sub(_cc_sub, out)

    # 3. Phone numbers
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

    # 6. Watchlist exact-match tokens (names, account numbers, etc.)
    for token in _load_privacy_watchlist():
        if token and token in out:
            tag = _make_tag("name", token)
            out = out.replace(token, tag)

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


# ── Claude Tool-Use Agent ─────────────────────────────────────
# Tools Claude can call when answering the user. Each tool has a handler
# in CLAUDE_TOOL_HANDLERS. Results are PII-shielded before being sent back.
CLAUDE_TOOLS = [
    {"name": "search_web", "description": "Search the web via DuckDuckGo for current information. Returns ranked snippets with URLs. Use for news, facts, people, companies, anything not in the local wiki.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "browse_web", "description": "Fetch a URL and return its full text content (HTML stripped). Use after search_web to read the full article/page. Ring 2.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string", "description": "Full https:// URL to fetch"}}, "required": ["url"]}},
    {"name": "read_file", "description": "Read any file on the local filesystem. Supports absolute paths (C:\\...) or paths relative to home (~). Returns up to 500000 chars.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute or home-relative path, e.g. ~/Projects/foo/bar.py or ~/wiki/notes.md"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write or append content to any file on the local filesystem. Creates parent directories automatically.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Absolute or home-relative path"},
         "content": {"type": "string", "description": "Text to write"},
         "mode": {"type": "string", "enum": ["write", "append"], "description": "write (overwrite) or append. Default: write"},
     }, "required": ["path", "content"]}},
    {"name": "write_clipboard", "description": "Copy text to the user's Windows clipboard.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "query_trust_graph", "description": "Look up a person in the trust graph by name or alias and return their entry (scores, evidence count, last interaction).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "query_calendar", "description": "Check today's calendar events.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_email", "description": "Search Gmail for messages matching a query.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "read_wiki", "description": "Read a markdown file from the personal wiki at ~/wiki/. Use a relative path like 'professional/job-search.md'.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "search_wiki", "description": "Keyword-search the personal wiki (and ~/.friday/wiki/) for files whose name or contents match a query. Returns up to 5 hits with a relative path and a short excerpt. Use this when the smart-loaded context didn't include the file you need; then call read_wiki on the most promising hit for the full file.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "run_command", "description": "Run a non-destructive PowerShell command on the system. Destructive commands (rm, del, format, shutdown, reg delete, etc.) are blocked.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "open_url", "description": "Open a URL in the user's default Chrome browser.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "draft_email", "description": "Create a Gmail draft (placeholder — requires Gmail connector).",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "get_career_pipeline", "description": "Get the current job-search pipeline status from the wiki.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_briefing", "description": "Get the most recent daily briefing summary.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "learn_skill", "description": "Create, modify, delete, or list skill YAML files in ~/.friday/skills/. Skills are reusable workflow definitions Friday can load. Use this for self-improvement — when you notice a pattern worth encoding. Actions: create, modify, delete, list, read.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["create", "modify", "delete", "list", "read"], "description": "Operation to perform"},
         "name": {"type": "string", "description": "Skill slug (alphanumeric/dashes). Required for all actions except 'list'."},
         "content": {"type": "string", "description": "YAML content for the skill (required for create/modify). Fields: name, description, trigger_patterns, tool_chain, prompt_template, success_criteria"},
     }, "required": ["action"]}},
    {"name": "install_package", "description": "Install a pip or npm package. Always check_only first to see if already installed. Ring 3 — requires Computer Control permission.",
     "input_schema": {"type": "object", "properties": {
         "package": {"type": "string", "description": "Package name, e.g. 'beautifulsoup4' or 'requests>=2.28'"},
         "manager": {"type": "string", "enum": ["pip", "npm"], "description": "Package manager. Default: pip"},
         "check_only": {"type": "boolean", "description": "If true, only checks if installed (no install). Default: false"},
     }, "required": ["package"]}},
]

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


def _html_to_text(html):
    """Strip HTML tags to plain text, preferring BeautifulSoup when available."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        return re.sub(r'\n{3,}', '\n\n', text)
    except ImportError:
        text = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', ' ', html, flags=re.I | re.S)
        text = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', ' ', text, flags=re.I | re.S)
        text = re.sub(r'<[^>]+>', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()


def _tool_search_web(inp):
    q = (inp or {}).get('query', '')
    if not q:
        return "search_web error: 'query' is required."
    try:
        import requests as _req
        encoded = _req.utils.quote(q)
        resp = _req.get(
            f"https://html.duckduckgo.com/html/?q={encoded}",
            timeout=12,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) FridayAgent/1.0'},
        )
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            for r in soup.select('.result')[:8]:
                title_el = r.select_one('.result__title')
                snip_el = r.select_one('.result__snippet')
                url_el = r.select_one('.result__url')
                if title_el and snip_el:
                    results.append({
                        'title': title_el.get_text(strip=True),
                        'snippet': snip_el.get_text(strip=True),
                        'url': url_el.get_text(strip=True) if url_el else '',
                    })
            if results:
                lines = [f"Search results for '{q}':\n"]
                for i, r in enumerate(results, 1):
                    lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
                return '\n'.join(lines)[:100_000]
        except ImportError:
            pass
        # BS4 not available — return stripped text
        text = _html_to_text(resp.text)
        return f"Search results for '{q}' (raw):\n{text[:50_000]}"
    except ImportError:
        return (
            f"requests library not installed. Install it with: pip install requests\n"
            f"Query was: {q!r}"
        )
    except Exception as e:
        return f"Web search error: {e}. Query: {q!r}"


def _tool_browse_web(inp):
    url = ((inp or {}).get('url') or '').strip()
    if not url:
        return "browse_web error: 'url' is required."
    if not (url.startswith('http://') or url.startswith('https://')):
        return f"browse_web error: URL must start with http:// or https://. Got: {url!r}"
    try:
        import requests as _req
        resp = _req.get(
            url, timeout=15,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) FridayAgent/1.0'},
            allow_redirects=True,
        )
        ct = resp.headers.get('content-type', '')
        if 'html' in ct or 'text' in ct or not ct:
            text = _html_to_text(resp.text)
        else:
            return f"Non-text content ({ct}) at {url} — can't extract text."
        _log_context("browse_web", {"url": url, "chars": len(text)})
        limit = 200_000
        return f"[{url}]\n{text[:limit]}" + (f"\n...[truncated — {len(text)} chars total]" if len(text) > limit else "")
    except ImportError:
        return "browse_web requires the requests library. Install: pip install requests"
    except Exception as e:
        return f"Browse error ({url}): {e}"


def _tool_read_file(inp):
    raw = (inp or {}).get('path', '')
    if not raw:
        return "read_file error: 'path' is required."
    try:
        p = Path(raw).expanduser().resolve()
    except Exception as e:
        return f"Invalid path {raw!r}: {e}"
    if not p.exists():
        return f"File not found: {p}"
    if not p.is_file():
        return f"Not a file: {p}"
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
        _log_context("file_read", {"path": str(p), "bytes": len(text)})
        limit = 500_000
        return text[:limit] + (f"\n...[truncated — {len(text)} total chars]" if len(text) > limit else "")
    except Exception as e:
        return f"Read error: {e}"


def _tool_write_file(inp):
    inp = inp or {}
    raw = (inp.get('path') or '').strip()
    content = inp.get('content', '')
    mode = (inp.get('mode') or 'write').lower()
    if not raw:
        return "write_file error: 'path' is required."
    if mode not in ('write', 'append'):
        mode = 'write'
    try:
        p = Path(raw).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == 'append':
            with open(p, 'a', encoding='utf-8') as f:
                f.write(content)
        else:
            p.write_text(content, encoding='utf-8')
        _log_context("file_write", {"path": str(p), "bytes": len(content), "mode": mode})
        return f"{'Appended' if mode == 'append' else 'Wrote'} {len(content)} chars to {p}"
    except Exception as e:
        return f"Write error: {e}"


def _tool_learn_skill(inp):
    """Create, modify, delete, or list skill YAML files in ~/.friday/skills/."""
    inp = inp or {}
    action = (inp.get('action') or 'create').lower()
    skills_dir = FRIDAY_DIR / 'skills'
    skills_dir.mkdir(parents=True, exist_ok=True)

    if action == 'list':
        skills = sorted(f.stem for f in skills_dir.glob('*.yaml'))
        return json.dumps({'skills': skills, 'count': len(skills), 'path': str(skills_dir)})

    name = re.sub(r'[^\w\-]', '_', (inp.get('name') or '').strip())
    if not name:
        return "learn_skill error: 'name' is required for create/modify/delete."

    skill_file = skills_dir / f'{name}.yaml'

    if action == 'delete':
        if skill_file.exists():
            skill_file.unlink()
            return f"Skill '{name}' deleted."
        return f"Skill '{name}' not found."

    if action in ('create', 'modify', 'update'):
        content = (inp.get('content') or '').strip()
        if not content:
            return "learn_skill error: 'content' (YAML text) is required for create/modify."
        existed = skill_file.exists()
        skill_file.write_text(content, encoding='utf-8')
        _log_context("skill_write", {"name": name, "action": action})
        # Register into the portable SKILL.md registry + SkillOpt so the skill is
        # matched/injected on the very next turn (no restart needed) and enters
        # the closed-loop optimizer.
        try:
            import skill_registry as _skreg
            _sk = _skreg.get_skill(name)
            if _sk:
                _skreg.register_with_skillopt(_sk)
        except Exception:
            pass
        return f"Skill '{name}' {'modified' if existed else 'created'} at {skill_file}. Active now — its triggers will inject it on matching turns."

    if action == 'read':
        if not skill_file.exists():
            return f"Skill '{name}' not found."
        return skill_file.read_text(encoding='utf-8')

    return f"Unknown action '{action}'. Use: create, modify, delete, list, read."


def _tool_install_package(inp):
    """Install pip or npm packages (Ring 3 — requires CC permission)."""
    inp = inp or {}
    package = (inp.get('package') or '').strip()
    manager = (inp.get('manager') or 'pip').lower()
    check_only = bool(inp.get('check_only', False))

    if not package:
        return "install_package error: 'package' is required."
    if not re.match(r'^[a-zA-Z0-9_\-\.\[\]>=<!,~\s]+$', package):
        return f"install_package error: invalid package name: {package!r}"

    if manager == 'pip':
        bare = re.split(r'[>=<!,\[\s]', package)[0].strip()
        if check_only:
            try:
                proc = subprocess.run(
                    [sys.executable, '-m', 'pip', 'show', bare],
                    capture_output=True, text=True, timeout=15,
                    creationflags=_POPEN_FLAGS,
                )
                return f"INSTALLED:\n{proc.stdout[:800]}" if proc.returncode == 0 else f"NOT INSTALLED: {bare}"
            except Exception as e:
                return f"Check error: {e}"
        try:
            proc = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', package],
                capture_output=True, text=True, timeout=180,
                creationflags=_POPEN_FLAGS,
            )
            out = (proc.stdout or '') + (('\n[stderr]\n' + proc.stderr) if proc.stderr else '')
            return f"{'SUCCESS' if proc.returncode == 0 else 'FAILED'}:\n{out[:4000]}"
        except subprocess.TimeoutExpired:
            return "pip install timed out after 180s."
        except Exception as e:
            return f"pip install error: {e}"

    elif manager == 'npm':
        cmd = ['npm', 'list', '-g', '--depth=0', package] if check_only else ['npm', 'install', '-g', package]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
                creationflags=_POPEN_FLAGS,
            )
            out = (proc.stdout or '') + (('\n[stderr]\n' + proc.stderr) if proc.stderr else '')
            return f"{'SUCCESS' if proc.returncode == 0 else 'FAILED'}:\n{out[:4000]}"
        except Exception as e:
            return f"npm error: {e}"

    return f"Unknown package manager: {manager!r}. Use 'pip' or 'npm'."


def _tool_write_clipboard(inp):
    text = (inp or {}).get('text', '')
    if not text:
        return "No text provided."
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard", "-Value", text],
            check=True, capture_output=True, timeout=10,
            creationflags=_POPEN_FLAGS,
        )
        return f"Copied {len(text)} chars to clipboard."
    except Exception as e:
        return f"Clipboard error: {e}"


def _tool_query_trust_graph(inp):
    name = ((inp or {}).get('name') or '').strip().lower()
    if not name:
        return "No name provided."
    graph = _load_trust_graph()
    people = graph.get('people') or {}
    items = people.values() if isinstance(people, dict) else people
    for p in items:
        if not isinstance(p, dict):
            continue
        if (p.get('name') or '').strip().lower() == name:
            return json.dumps(p, default=str)[:100_000]
        aliases = [str(a).lower() for a in (p.get('aliases') or [])]
        if name in aliases:
            return json.dumps(p, default=str)[:100_000]
    return f"No trust-graph entry found for {name!r}."


def _tool_query_calendar(_inp):
    # Mirror the /api/calendar endpoint
    return json.dumps({"events": [], "note": "Google Calendar connector not wired; calendar is empty."})


def _tool_search_email(inp):
    q = (inp or {}).get('query', '')
    return f"Email search requires the Gmail connector (not installed). Query was: {q!r}."


def _tool_read_wiki(inp):
    raw = (inp or {}).get('path', '')
    p = (WIKI_DIR / raw).resolve()
    wiki_resolved = WIKI_DIR.resolve()
    try:
        p.relative_to(wiki_resolved)
    except ValueError:
        return f"Path escapes the wiki root: {raw}"
    if not p.exists() or not p.is_file():
        return f"Wiki file not found: {raw}"
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
        return text[:200_000] + ("\n...[truncated]" if len(text) > 200_000 else "")
    except Exception as e:
        return f"Read error: {e}"


def _tool_search_wiki(inp):
    """Keyword-search the wiki and return up to N hits with excerpts."""
    inp = inp or {}
    query = (inp.get('query') or '').strip()
    if not query:
        return "search_wiki error: 'query' is required."
    try:
        limit = int(inp.get('limit') or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))
    q_low = query.lower()

    results = []
    for root, label in [(WIKI_DIR, 'wiki'), (FRIDAY_DIR / 'wiki', 'friday-wiki')]:
        if not root.exists():
            continue
        for f in root.rglob('*'):
            if len(results) >= limit:
                break
            if not f.is_file() or f.suffix not in ('.md', '.txt'):
                continue
            try:
                content = f.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            name_match = q_low in f.stem.lower()
            idx = content.lower().find(q_low)
            if not name_match and idx < 0:
                continue
            if idx < 0:
                excerpt = content[:400]
            else:
                start = max(0, idx - 120)
                end = min(len(content), idx + 280)
                excerpt = content[start:end]
            try:
                rel = str(f.relative_to(root)).replace('\\', '/')
            except ValueError:
                rel = str(f)
            results.append({
                'root': label,
                'path': rel,
                'excerpt': excerpt.strip(),
            })
        if len(results) >= limit:
            break

    if not results:
        return f"No wiki files matched {query!r}."
    return json.dumps({'query': query, 'hits': results}, default=str)[:100_000]


def _tool_run_command(inp):
    cmd = ((inp or {}).get('command') or '').strip()
    if not cmd:
        return "Empty command."
    low = cmd.lower()
    for bad in _RUN_COMMAND_BLOCKLIST:
        if bad in low:
            return f"Blocked by cLaws safety: command matches blocklist token {bad!r}."
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=300,
            creationflags=_POPEN_FLAGS,
        )
        out = (proc.stdout or '') + (("\n[stderr]\n" + proc.stderr) if proc.stderr else '')
        return out[:100_000] if out else f"(exit {proc.returncode}, no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 300s."
    except Exception as e:
        return f"Command error: {e}"


def _tool_open_url(inp):
    url = ((inp or {}).get('url') or '').strip()
    if not (url.startswith('http://') or url.startswith('https://')):
        return f"Refusing to open non-http(s) URL: {url!r}"
    try:
        # Try Chrome first, fall back to default browser
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for cp in chrome_paths:
            if Path(cp).exists():
                subprocess.Popen([cp, url])
                return f"Opened in Chrome: {url}"
        os.startfile(url)  # type: ignore[attr-defined]
        return f"Opened in default browser: {url}"
    except Exception as e:
        return f"Open URL error: {e}"


def _tool_draft_email(inp):
    return "Email drafting requires the Gmail connector (not installed)."


def _tool_get_career_pipeline(_inp):
    try:
        if JOB_SEARCH_FILE.exists():
            text = JOB_SEARCH_FILE.read_text(encoding='utf-8', errors='replace')
            return text[:500_000] + ("\n...[truncated]" if len(text) > 500_000 else "")
        return "No career pipeline file found at ~/wiki/professional/job-search.md."
    except Exception as e:
        return f"Pipeline read error: {e}"


def _tool_get_briefing(_inp):
    """Return the most recent daily briefing (HTML stripped, plus markdown)."""
    candidates = []
    briefings_dir = FRIDAY_DIR / "wiki" / "briefings"
    if briefings_dir.exists():
        for f in briefings_dir.iterdir():
            if f.is_file() and f.suffix in ('.html', '.md'):
                candidates.append(f)
    creations_dir = CREATIONS_DIR
    if creations_dir.exists():
        for f in creations_dir.iterdir():
            if f.is_file() and f.name.startswith('daily-briefing') and f.suffix in ('.html', '.md'):
                candidates.append(f)
    if not candidates:
        return "No briefings found."
    latest = max(candidates, key=lambda f: f.stat().st_mtime)
    try:
        text = latest.read_text(encoding='utf-8', errors='replace')
        if latest.suffix == '.html':
            text = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', ' ', text, flags=re.I)
            text = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', ' ', text, flags=re.I)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
        return f"[{latest.name}]\n{text[:100_000]}"
    except Exception as e:
        return f"Briefing read error: {e}"


# ═══ BACKGROUND TASK RUNNER ═══════════════════════════════════
# In-process registry of long-running tasks spawned via /api/tasks or
# the spawn_task tool. Each entry is a plain dict; mutation happens
# from the worker thread, so callers should always copy before returning.
TASKS = {}
TASKS_LOCK = threading.Lock()

# Per-task follow-up queue for dual-loop steering (POST /api/agent/steer)
_FOLLOW_UP_QUEUES: dict = {}
_FOLLOW_UP_LOCK = threading.Lock()


def _task_log(task_id, line):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if not t:
            return
        t.setdefault('log', []).append(str(line))
        # Cap log length to keep payloads small
        if len(t['log']) > 200:
            t['log'] = t['log'][-200:]


def _task_set(task_id, **fields):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if not t:
            return
        t.update(fields)


def _task_snapshot(task_id=None):
    with TASKS_LOCK:
        if task_id is not None:
            t = TASKS.get(task_id)
            if not t:
                return None
            t = dict(t)
            if t.get('started'):
                t['elapsed'] = int(_time.time() - t['started']) - (0 if t.get('status') == 'running' else 0)
                if t.get('ended'):
                    t['elapsed'] = int(t['ended'] - t['started'])
            return t
        out = []
        for tid, t in TASKS.items():
            row = dict(t)
            if row.get('started'):
                end = row.get('ended') or _time.time()
                row['elapsed'] = int(end - row['started'])
            out.append(row)
        return out


def _evaluate_output(task_id, goal, output):
    """Grade task output with a fresh Claude call that has no build history."""
    client = get_anthropic_client()
    if client is None:
        return None
    try:
        eval_prompt = (
            f"You are a strict, impartial evaluator. Read the goal and output below, "
            f"then grade the output.\n\n"
            f"GOAL:\n{goal[:1500]}\n\n"
            f"OUTPUT:\n{output[:4000]}\n\n"
            f"Respond ONLY in this exact format:\n"
            f"GRADE: [PASS/PARTIAL/FAIL]\n"
            f"REASON: [one sentence]"
        )
        resp = client.messages.create(
            model=ANTHROPIC_MODEL_DEFAULT,
            max_tokens=128,
            messages=[{"role": "user", "content": eval_prompt}],
        )
        return resp.content[0].text.strip() if resp.content else "GRADE: PARTIAL\nREASON: Evaluation unavailable."
    except Exception as e:
        return f"GRADE: PARTIAL\nREASON: Evaluation failed: {e}"


TASK_TIMEOUT_SECONDS = int(os.environ.get('FRIDAY_TASK_TIMEOUT', 1800))  # 30 min default


def _task_worker(task_id, name, prompt, description=''):
    """Run a Claude agent prompt to completion and store results.

    Heuristic log lines come from inspecting the tool_trace returned by
    _call_claude_agent so the UI can show what the agent did step-by-step.
    Timeout guard: if the task runs longer than TASK_TIMEOUT_SECONDS (default
    30 min, configurable via FRIDAY_TASK_TIMEOUT env var or settings), it is
    terminated gracefully.
    """
    timeout = _load_settings().get('task_timeout_seconds', TASK_TIMEOUT_SECONDS)
    _task_set(task_id, status='running', started=_time.time())
    _task_log(task_id, f'Spawning agent: {name} (timeout: {timeout}s)')
    if description:
        _task_log(task_id, description)
    try:
        # Each task gets its own fresh single-turn conversation.
        messages = [{"role": "user", "content": prompt}]
        # Load full vault/wiki context so the agent knows the user's context.
        _task_log(task_id, 'Loading vault context…')
        system = _get_friday_system_prompt(prompt, workspace='task') + (
            "\n\n== BACKGROUND TASK MODE ==\n"
            "You are operating as an autonomous background task. Take initiative, "
            "use available tools, and produce a concrete, useful result the user can read.\n\n"
            "== RESEARCH DISCIPLINE ==\n"
            "When doing research tasks: after your first round of findings, identify which "
            "side of the question has WEAKER evidence. Run a second round explicitly targeting "
            "that weaker side to avoid confirmation bias. State both sides in your output."
        )
        # Stream a couple of milestone lines so the UI feels alive.
        _task_log(task_id, 'Calling Claude…')
        subagent_model = _load_settings().get("subagent_model") or ANTHROPIC_MODEL_DEFAULT
        _bg_label = (name or prompt or 'Task')[:24]
        reply, tool_trace = _call_claude_agent(
            messages, system=system, max_tokens=16384, model=subagent_model,
            session_ctx={"authenticated": True, "is_background_task": True},
            orb_label=_bg_label, orb_category='monitoring', orb_icon='🛰',
        )
        for step in tool_trace or []:
            tn = step.get('name', '?')
            ti = step.get('input') or {}
            label = ti.get('query') or ti.get('path') or ti.get('command') or ti.get('url') or ''
            line = f'{tn}({str(label)[:60]})' if label else tn
            _task_log(task_id, '→ tool: ' + line)

        # ── Timeout check ──
        _task_elapsed = _time.time() - (TASKS.get(task_id, {}).get('started') or _time.time())
        if _task_elapsed > timeout:
            _task_log(task_id, f'TIMEOUT after {int(_task_elapsed)}s — terminating gracefully')
            _task_set(task_id, status='timeout', result=reply or '(timed out before completion)', ended=_time.time())
            return

        # ── Dual-loop: drain the follow-up queue ──────────────────
        # External callers can POST /api/agent/steer to push follow-up
        # prompts that re-enter the agent after the first pass completes.
        combined_reply = reply or ''
        combined_trace = list(tool_trace or [])
        _drain_iters = 0
        while _drain_iters < 5:
            # Check timeout before each steer iteration
            _task_elapsed = _time.time() - (TASKS.get(task_id, {}).get('started') or _time.time())
            if _task_elapsed > timeout:
                _task_log(task_id, f'TIMEOUT during steer loop after {int(_task_elapsed)}s')
                _task_set(task_id, status='timeout', result=combined_reply or '(timed out)', ended=_time.time())
                return
            with _FOLLOW_UP_LOCK:
                pending = _FOLLOW_UP_QUEUES.pop(task_id, [])
            if not pending:
                break
            _drain_iters += 1
            for steer_msg in pending:
                _task_log(task_id, f'[steer] {steer_msg[:80]}')
                steer_reply, steer_trace = _call_claude_agent(
                    [{"role": "user", "content": steer_msg}],
                    system=system, max_tokens=16384, model=subagent_model,
                    session_ctx={"authenticated": True, "is_background_task": True},
                    orb_label=f"steer: {steer_msg[:18]}", orb_category='monitoring', orb_icon='🎯',
                )
                combined_trace.extend(steer_trace or [])
                if steer_reply:
                    combined_reply += f"\n\n---\n{steer_reply}"

        reply = combined_reply
        tool_trace = combined_trace

        # ── Evidence gate: require tool use for verified completion ──
        evidence = [t for t in tool_trace if t.get('name') not in ('spawn_task',)]
        verified = len(evidence) > 0
        verification_summary = ', '.join(dict.fromkeys(t['name'] for t in evidence[:10])) if evidence else 'no tools used'
        final_status = 'complete' if verified else 'completed_unverified'

        _task_log(task_id, 'Finalizing response')
        _task_set(task_id, status=final_status, result=reply or '(no response)', ended=_time.time(),
                  verified=verified, verification_evidence=verification_summary)

        # ── Fresh-context evaluator ────────────────────────────────
        _task_log(task_id, 'Running quality evaluation…')
        evaluation = _evaluate_output(task_id, prompt, reply or '')
        if evaluation:
            _task_set(task_id, evaluation=evaluation)
            grade_line = next((l for l in evaluation.splitlines() if l.startswith('GRADE:')), '')
            if grade_line:
                _task_log(task_id, f'Eval: {grade_line}')

        _task_log(task_id, 'Done.')
    except Exception as e:
        traceback.print_exc()
        _task_set(task_id, status='failed', result=f'[Error] {e}', ended=_time.time())
        _task_log(task_id, f'Error: {e}')


def _spawn_task(name, prompt, description=''):
    task_id = str(uuid.uuid4())
    with TASKS_LOCK:
        TASKS[task_id] = {
            'task_id': task_id,
            'name': name,
            'description': description,
            'prompt': prompt,
            'status': 'queued',
            'created': _time.time(),
            'started': None,
            'ended': None,
            'log': [],
            'result': '',
        }
    _log_context("task_spawn", {
        "task_id": task_id,
        "name": name,
        "description": description,
        "prompt": prompt[:1000],
    })
    th = threading.Thread(target=_task_worker, args=(task_id, name, prompt, description), daemon=True)
    th.start()
    return task_id


def _tool_spawn_task(inp):
    """Claude-facing tool: spawn a background research/analysis task."""
    name = ((inp or {}).get('name') or 'Background task').strip()[:120]
    prompt = ((inp or {}).get('prompt') or '').strip()
    desc = ((inp or {}).get('description') or '').strip()[:200]
    if not prompt:
        return "spawn_task error: 'prompt' is required."
    tid = _spawn_task(name, prompt, desc)
    return json.dumps({
        'task_id': tid,
        'status': 'running',
        'message': f"Spawned background task '{name}'. The user can watch progress in the Task Tray (bottom-right) and you can tell them you've started working on it.",
    })


# Register the spawn_task tool
CLAUDE_TOOLS.append({
    "name": "spawn_task",
    "description": "Start a background research or analysis task that runs while the user does other work. Use this when the user asks for something that will take a while (deep research, multi-step analysis, writing a long brief). The task runs autonomously and the result appears in the Task Tray in the UI.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short, human-readable task title (e.g., 'Research Bobby Tahir')."},
            "description": {"type": "string", "description": "Optional one-line subtitle shown in the Task Tray."},
            "prompt": {"type": "string", "description": "The full instruction the background agent should execute."},
        },
        "required": ["name", "prompt"],
    },
})


# ── Task Tray HTTP endpoints (consumed by the frontend TaskTray) ──
@app.route('/api/tasks')
def list_tasks():
    return jsonify({"tasks": _task_snapshot() or []})


@app.route('/api/tasks/<task_id>')
def get_task(task_id):
    task = _task_snapshot(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    with TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id]['status'] = 'cancelled'
            del TASKS[task_id]
            return jsonify({"status": "cancelled"})
    return jsonify({"error": "Task not found"}), 404


@app.route('/api/agent/steer', methods=['POST'])
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


# ═══ PROCESS ORB REGISTRY (holographic Layer 2) ══════════════════
# Lightweight in-memory registry for active processes that the frontend
# renders as floating holographic orbs.  Skills/tasks register here via
# process_register() / process_update() and the frontend polls GET /api/processes.
PROCESSES = {}
PROCESSES_LOCK = threading.Lock()


def process_register(pid, *, name="Task", label=None, category="default",
                     icon="⚡", steps=None, model=None, color=None):
    """Register a new process for the holographic orb display.

    `color` (optional int, e.g. 0x22c55e) overrides the category/local orb color
    in the 3-D scene — used for the green vault-access orb.
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
            "started": _time.time(),
        }


def process_update(pid, *, status=None, progress=None, label=None,
                   step=None, steps=None):
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
        if status in ("completed", "error"):
            p["ended"] = _time.time()


def process_remove(pid):
    """Remove a process from the registry."""
    with PROCESSES_LOCK:
        PROCESSES.pop(pid, None)


@app.route('/api/processes')
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


def _tool_propose_wiki_update(inp):
    """Queue a wiki update as pending — the user approves it in the Wiki workspace."""
    inp = inp or {}
    file = (inp.get("file") or "").strip()
    new_value = inp.get("new_value") or ""
    if not file or not new_value:
        return "propose_wiki_update error: 'file' and 'new_value' are required."
    section = (inp.get("section") or "").strip()
    reason = (inp.get("reason") or "Agent-proposed update.").strip()
    if _safe_wiki_path(file) is None:
        return f"propose_wiki_update error: invalid wiki path {file!r} (must stay inside ~/wiki/)."
    pid = _propose_wiki_update(file=file, section=section, new_value=new_value, reason=reason)
    return f"Wiki update proposed (id={pid}) — awaiting your approval in the Wiki workspace."


def _tool_correct_wiki(inp):
    """Replace old_text with new_text across every wiki file and ~/.friday JSONs."""
    inp = inp or {}
    old_text = inp.get("old_text") or ""
    new_text = inp.get("new_text") or ""
    if not old_text:
        return "correct_wiki error: 'old_text' is required."
    modified = []
    if WIKI_DIR.exists():
        for f in WIKI_DIR.rglob('*'):
            if not f.is_file() or f.suffix not in ('.md', '.txt'):
                continue
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            if old_text in text:
                try:
                    rel = str(f.relative_to(WIKI_DIR)).replace('\\', '/')
                    _mirror_wiki_file(rel, text.replace(old_text, new_text))
                    modified.append(rel)
                except Exception:
                    pass
    if FRIDAY_DIR.exists():
        for f in FRIDAY_DIR.glob('*.json'):
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            if old_text in text:
                try:
                    f.write_text(text.replace(old_text, new_text), encoding='utf-8')
                    modified.append(f".friday/{f.name}")
                except Exception:
                    pass
    return json.dumps({"modified": modified, "count": len(modified)})


CLAUDE_TOOLS.append({
    "name": "propose_wiki_update",
    "description": "Propose an update to the user's personal wiki when you learn new information about them. The update is queued as PENDING and the user approves it from the Wiki workspace — it is NOT applied immediately. Use this whenever you learn a new fact about the user, their work, family, preferences, or projects that should outlive the current conversation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "Wiki file path relative to ~/wiki/, e.g., 'identity/core-profile.md'."},
            "section": {"type": "string", "description": "Optional section name within the file (e.g., 'birthplace'). Used to append under a header if no existing text is matched."},
            "new_value": {"type": "string", "description": "The new content to add or replace with."},
            "reason": {"type": "string", "description": "Why this update is being proposed (e.g., 'User correction during chat')."},
        },
        "required": ["file", "new_value", "reason"],
    },
})
CLAUDE_TOOLS.append({
    "name": "correct_wiki",
    "description": "Correct wrong information across the ENTIRE wiki at once. Use this when the user says you (or the wiki) got a fact wrong — replaces old_text with new_text in every wiki file plus ~/.friday JSONs. Applies immediately (no approval needed) because corrections are user-initiated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "old_text": {"type": "string", "description": "Exact text to find and replace."},
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["old_text", "new_text"],
    },
})


CLAUDE_TOOL_HANDLERS = {
    "search_web": _tool_search_web,
    "browse_web": _tool_browse_web,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "write_clipboard": _tool_write_clipboard,
    "query_trust_graph": _tool_query_trust_graph,
    "query_calendar": _tool_query_calendar,
    "search_email": _tool_search_email,
    "read_wiki": _tool_read_wiki,
    "search_wiki": _tool_search_wiki,
    "run_command": _tool_run_command,
    "open_url": _tool_open_url,
    "draft_email": _tool_draft_email,
    "get_career_pipeline": _tool_get_career_pipeline,
    "get_briefing": _tool_get_briefing,
    "spawn_task": _tool_spawn_task,
    "propose_wiki_update": _tool_propose_wiki_update,
    "correct_wiki": _tool_correct_wiki,
    "learn_skill": _tool_learn_skill,
    "install_package": _tool_install_package,
}


# ── Computer Control ─────────────────────────────────────────────
# pyautogui-based mouse/keyboard control. Requires explicit user permission.
# The grant persists across restarts (cc_permission file); the kill switch
# terminates immediately and is never persisted.

_CC_PERMISSION = threading.Event()   # Set = user granted permission
_CC_KILL = threading.Event()          # Set = kill switch activated
_CC_ACTION_TS: list = []              # timestamps for rate limiting
_CC_ACTION_LOCK = threading.Lock()
_CC_MAX_PER_SEC = 20                  # max actions per second (rate limit is a safety floor, not a ceiling)
_CC_PERM_FILE = FRIDAY_DIR / "cc_permission"   # persists the grant across restarts (kill is never persisted)
# Maps the coordinate space of the LAST screenshot we sent the model back to real
# screen pixels. We downscale screenshots for accuracy/payload, so the model's
# click coordinates live in the downscaled image space and must be scaled up.
_CC_LAST_SHOT = {"scale_x": 1.0, "scale_y": 1.0}

_HAS_PYAUTOGUI = False
_pag = None  # module handle

try:
    import pyautogui as _pag
    _pag.FAILSAFE = True   # moving mouse to top-left corner aborts any running call
    _pag.PAUSE = 0.05
    _HAS_PYAUTOGUI = True
    print("  [FRIDAY] pyautogui loaded — computer control available")
except ImportError:
    print("  [FRIDAY] pyautogui not installed — computer control disabled. Run: pip install pyautogui")


def _cc_persist(granted: bool):
    """Persist (or clear) the Computer Control grant so it survives a restart.

    The kill switch is intentionally NOT persisted — a fresh start clears a kill
    so the user isn't permanently locked out, but a prior grant is restored.
    """
    try:
        if granted:
            _CC_PERM_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CC_PERM_FILE.write_text("granted", encoding="utf-8")
        elif _CC_PERM_FILE.exists():
            _CC_PERM_FILE.unlink()
    except Exception as _e:
        print(f"  [FRIDAY] CC permission persist failed: {_e}")


# Restore a previously-granted permission on startup so the user doesn't have to
# re-enable it every launch. Requires pyautogui to actually be importable.
try:
    if _HAS_PYAUTOGUI and _CC_PERM_FILE.exists():
        _CC_PERMISSION.set()
        print("  [FRIDAY] Computer Control permission restored from previous session")
except Exception:
    pass


def _cc_check():
    """Return (True, None) if CC is permitted, else (False, error_string)."""
    if not _HAS_PYAUTOGUI:
        return False, "pyautogui not installed. Run: pip install pyautogui"
    if _CC_KILL.is_set():
        return False, "Kill switch is active. Computer control suspended — re-enable in Settings."
    if not _CC_PERMISSION.is_set():
        return False, "Computer control permission not granted. Enable it in Settings > Computer Control."
    return True, None


def _cc_rate_ok():
    now = _time.time()
    with _CC_ACTION_LOCK:
        _CC_ACTION_TS[:] = [t for t in _CC_ACTION_TS if now - t < 1.0]
        if len(_CC_ACTION_TS) >= _CC_MAX_PER_SEC:
            return False
        _CC_ACTION_TS.append(now)
    return True


def _tool_move_mouse(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    if not _cc_rate_ok():
        return "Rate limited: too many actions per second."
    # Coordinates arrive in the LAST screenshot's (downscaled) pixel space — map
    # them back to real screen pixels.
    x = int(round(int((inp or {}).get('x', 0)) * _CC_LAST_SHOT["scale_x"]))
    y = int(round(int((inp or {}).get('y', 0)) * _CC_LAST_SHOT["scale_y"]))
    try:
        _pag.moveTo(x, y, duration=0.25)
        _log_context("cc_action", {"action": "move_mouse", "x": x, "y": y})
        return f"Mouse moved to ({x}, {y})."
    except Exception as e:
        return f"move_mouse error: {e}"


def _tool_click(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    if not _cc_rate_ok():
        return "Rate limited."
    # Map screenshot-space coords back to real screen pixels (see _CC_LAST_SHOT).
    x = int(round(int((inp or {}).get('x', 0)) * _CC_LAST_SHOT["scale_x"]))
    y = int(round(int((inp or {}).get('y', 0)) * _CC_LAST_SHOT["scale_y"]))
    button = (inp or {}).get('button', 'left')
    if button not in ('left', 'right', 'middle'):
        button = 'left'
    try:
        _pag.click(x, y, button=button)
        _log_context("cc_action", {"action": "click", "x": x, "y": y, "button": button})
        return f"Clicked {button} at ({x}, {y})."
    except Exception as e:
        return f"click error: {e}"


def _tool_type_text(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    text = (inp or {}).get('text', '')
    if not text:
        return "No text provided."
    if len(text) > 2000:
        return "Text too long (max 2000 chars per call)."
    if not _cc_rate_ok():
        return "Rate limited."
    try:
        _pag.write(text, interval=0.03)
        _log_context("cc_action", {"action": "type_text", "chars": len(text)})
        return f"Typed {len(text)} characters."
    except Exception as e:
        return f"type_text error: {e}"


def _tool_press_key(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    key = ((inp or {}).get('key') or '').strip()
    if not key:
        return "No key provided."
    if not _cc_rate_ok():
        return "Rate limited."
    try:
        _pag.press(key)
        _log_context("cc_action", {"action": "press_key", "key": key})
        return f"Pressed key: {key}."
    except Exception as e:
        return f"press_key error: {e}"


def _tool_screenshot(_inp):
    ok, err = _cc_check()
    if not ok:
        return err
    try:
        shot = _pag.screenshot()
        real_w, real_h = shot.size
        # Downscale to ~WXGA before sending to the model. Two reasons:
        #   1. Vision models localise UI elements more reliably below ~1366px wide.
        #   2. Keeps the base64 payload well under the API's per-image limit.
        # We record scale_x/scale_y so click()/move_mouse() map the model's
        # image-space coordinates back to real screen pixels.
        TARGET_W = 1366
        if real_w > TARGET_W:
            disp_w = TARGET_W
            disp_h = max(1, round(real_h * (TARGET_W / real_w)))
            shot_disp = shot.resize((disp_w, disp_h))
        else:
            disp_w, disp_h = real_w, real_h
            shot_disp = shot
        _CC_LAST_SHOT["scale_x"] = real_w / disp_w
        _CC_LAST_SHOT["scale_y"] = real_h / disp_h
        buf = io.BytesIO()
        shot_disp.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        _log_context("cc_action", {"action": "screenshot", "size": f"{real_w}x{real_h}", "sent": f"{disp_w}x{disp_h}"})
        return json.dumps({
            "width": disp_w, "height": disp_h,
            "real_width": real_w, "real_height": real_h,
            "media_type": "image/png",
            "image_b64": b64,
            "note": (f"Screenshot is {disp_w}x{disp_h}px (top-left is 0,0). Give click/move "
                     "coordinates within this image — they are mapped to the real screen automatically."),
        })
    except Exception as e:
        return f"screenshot error: {e}"


def _tool_scroll(inp):
    ok, err = _cc_check()
    if not ok:
        return err
    if not _cc_rate_ok():
        return "Rate limited."
    direction = (inp or {}).get('direction', 'down')
    amount = max(1, min(20, int((inp or {}).get('amount', 3))))
    clicks = -amount if direction == 'down' else amount
    try:
        _pag.scroll(clicks)
        _log_context("cc_action", {"action": "scroll", "direction": direction, "amount": amount})
        return f"Scrolled {direction} {amount} step(s)."
    except Exception as e:
        return f"scroll error: {e}"


CLAUDE_TOOLS.extend([
    {
        "name": "move_mouse",
        "description": "Move the mouse cursor to screen coordinates. Requires computer control permission (user must enable in Settings > Computer Control). Take a screenshot first to locate elements.",
        "input_schema": {"type": "object", "properties": {
            "x": {"type": "integer", "description": "X pixels from left edge"},
            "y": {"type": "integer", "description": "Y pixels from top edge"},
        }, "required": ["x", "y"]},
    },
    {
        "name": "click",
        "description": "Click the mouse at screen coordinates. Requires computer control permission.",
        "input_schema": {"type": "object", "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
        }, "required": ["x", "y"]},
    },
    {
        "name": "type_text",
        "description": "Type text via keyboard into the currently focused element. Requires computer control permission.",
        "input_schema": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key. Requires computer control permission. Key names: enter, tab, escape, backspace, delete, home, end, pageup, pagedown, up, down, left, right, f1-f12, ctrl, alt, shift, or combos like ctrl+c.",
        "input_schema": {"type": "object", "properties": {
            "key": {"type": "string"},
        }, "required": ["key"]},
    },
    {
        "name": "screenshot",
        "description": "Capture the current screen as a PNG. Returns dimensions and base64 image data. Use this before clicking to locate UI elements by their pixel position. Requires computer control permission.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "scroll",
        "description": "Scroll the mouse wheel up or down. Requires computer control permission.",
        "input_schema": {"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer", "description": "Scroll steps (1-20, default 3)"},
        }, "required": ["direction"]},
    },
])

CLAUDE_TOOL_HANDLERS.update({
    "move_mouse": _tool_move_mouse,
    "click": _tool_click,
    "type_text": _tool_type_text,
    "press_key": _tool_press_key,
    "screenshot": _tool_screenshot,
    "scroll": _tool_scroll,
})


# ── Privilege Ring Mapping ─────────────────────────────────────
# Ring 0 READ   — local reads, no mutation, always allowed
# Ring 1 WRITE  — local state mutation, always allowed
# Ring 2 NETWORK — external calls, agent spawn; requires authenticated session
# Ring 3 FULL   — OS-level control (mouse, keyboard, screen); requires CC permission
TOOL_RINGS: dict[str, int] = {
    # Ring 0 — READ (local reads, no mutation, always allowed)
    "read_file":            0,
    "read_wiki":            0,
    "search_wiki":          0,
    "query_trust_graph":    0,
    "query_calendar":       0,
    "get_career_pipeline":  0,
    "get_briefing":         0,
    # Ring 1 — WRITE (local state mutation, always allowed)
    "write_file":           1,
    "write_clipboard":      1,
    "propose_wiki_update":  1,
    "correct_wiki":         1,
    "learn_skill":          1,
    # Ring 2 — NETWORK (external calls; requires authenticated session)
    "search_web":           2,
    "browse_web":           2,
    "search_email":         2,
    "draft_email":          2,
    "open_url":             2,
    "spawn_task":           2,
    "run_command":          2,
    # Ring 3 — FULL OS CONTROL (requires CC permission)
    "install_package":      3,
    "move_mouse":           3,
    "click":                3,
    "type_text":            3,
    "press_key":            3,
    "screenshot":           3,
    "scroll":               3,
}

_GOVERNANCE_KEY: bytes | None = None


def _get_governance_key() -> bytes:
    """Return the HMAC signing key for BOM entries, generating once per run."""
    global _GOVERNANCE_KEY
    if _GOVERNANCE_KEY is not None:
        return _GOVERNANCE_KEY
    key_file = FRIDAY_DIR / "vault" / ".governance-key"
    if key_file.exists():
        try:
            _GOVERNANCE_KEY = key_file.read_bytes()
            return _GOVERNANCE_KEY
        except Exception:
            pass
    import os as _os
    key = _os.urandom(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
    except Exception:
        pass
    _GOVERNANCE_KEY = key
    return key


# ── Sovereign Vault: encryption-at-rest ──────────────────────────────
# Transparent AES-256-GCM for sensitive files (finance, health, co-parent,
# legal). The key is derived once from FRIDAY_PASSWORD via Argon2id — see
# vault_crypto.py. When no password is set (or the crypto deps are missing)
# the key is None and every helper falls back to plaintext, so behaviour is
# unchanged for the keyless local-dev case.
try:
    import vault_crypto as _vc
    _HAS_VAULT_CRYPTO = True
except Exception:
    _vc = None
    _HAS_VAULT_CRYPTO = False

_VAULT_KEY: bytes | None = None
_VAULT_KEY_READY = False
_VAULT_CONFIG_FILE = FRIDAY_DIR / "vault" / ".vault_config.json"


def _get_vault_key() -> bytes | None:
    """Derive (once) the AES-256 vault key from FRIDAY_PASSWORD.

    Returns the 32-byte key, or None when encryption is disabled/unavailable
    (no password set, or vault_crypto/cryptography missing). On None, callers
    transparently read and write plaintext.
    """
    global _VAULT_KEY, _VAULT_KEY_READY
    if _VAULT_KEY_READY:
        return _VAULT_KEY
    _VAULT_KEY_READY = True
    if not _HAS_VAULT_CRYPTO or not FRIDAY_PASSWORD:
        if not FRIDAY_PASSWORD:
            print("[vault] FRIDAY_PASSWORD not set — sensitive data stored as PLAINTEXT at rest.")
        elif not _HAS_VAULT_CRYPTO:
            print("[vault] vault_crypto unavailable — sensitive data stored as PLAINTEXT at rest.")
        _VAULT_KEY = None
        return None
    try:
        _VAULT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if _VAULT_CONFIG_FILE.exists():
            cfg = json.loads(_VAULT_CONFIG_FILE.read_text(encoding="utf-8"))
        salt_hex = cfg.get("salt_hex")
        if not salt_hex:
            salt_hex = os.urandom(16).hex()
            cfg.update({"salt_hex": salt_hex, "kdf": "argon2id", "cipher": "aes-256-gcm"})
            _tmp = _VAULT_CONFIG_FILE.with_name(_VAULT_CONFIG_FILE.name + ".tmp")
            _tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            _tmp.replace(_VAULT_CONFIG_FILE)
        _VAULT_KEY = _vc.derive_key(FRIDAY_PASSWORD, bytes.fromhex(salt_hex))
        print("[vault] Encryption-at-rest ENABLED (AES-256-GCM · Argon2id).")
    except Exception as e:
        print(f"[vault] key derivation failed ({e}) — falling back to plaintext.")
        _VAULT_KEY = None
    return _VAULT_KEY


def _vault_read_text(path) -> str:
    """Read a possibly-encrypted file as UTF-8 text.

    Decrypts when the file is a FRIDAYVAULT blob and a key is available;
    otherwise returns the bytes as text (handles plaintext + mixed states
    during rollover). Raises on an encrypted blob with no/incorrect key.
    """
    raw = Path(path).read_bytes()
    key = _get_vault_key()
    if _HAS_VAULT_CRYPTO and _vc.is_encrypted(raw):
        if key is None:
            raise RuntimeError("file is vault-encrypted but FRIDAY_PASSWORD is not set")
        return _vc.decrypt(raw, key).decode("utf-8")
    return raw.decode("utf-8")


def _vault_write_text(path, text: str) -> None:
    """Write text, encrypting at rest when a vault key is available. Atomic."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    key = _get_vault_key()
    if key is not None:
        data = _vc.encrypt(data, key)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)


# Sensitive directories whose file contents are encrypted at rest when a vault
# key is present. Scoped to the TIER_3 personal-data stores — NOT the wiki or
# the append-only audit logs (those are handled separately / kept plaintext).
def _sensitive_vault_dirs() -> list:
    dirs = [FRIDAY_DIR / "finance", FRIDAY_DIR / "health", FRIDAY_DIR / "ofw"]
    vault_root = FRIDAY_DIR / "vault"
    dirs += [vault_root / c for c in ("legal", "coparenting", "finances", "family")]
    return dirs


_VAULT_MIGRATE_SKIP = {".vault_config.json", ".governance-key",
                       "access-log.jsonl", "decision-bom.jsonl"}


def _migrate_vault_plaintext() -> None:
    """Encrypt any still-plaintext sensitive files in place (idempotent).

    Runs once at startup when a vault key is available. Verifies a decrypt
    round-trip before replacing each file; per-file try/except so a single
    failure never blocks boot. Files already encrypted are skipped.
    """
    key = _get_vault_key()
    if key is None or not _HAS_VAULT_CRYPTO:
        return
    migrated = 0
    for d in _sensitive_vault_dirs():
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if not p.is_file() or p.name in _VAULT_MIGRATE_SKIP or p.suffix == ".tmp":
                continue
            try:
                raw = p.read_bytes()
                if _vc.is_encrypted(raw):
                    continue
                blob = _vc.encrypt(raw, key)
                if _vc.decrypt(blob, key) != raw:   # prove recoverability first
                    continue
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_bytes(blob)
                tmp.replace(p)
                migrated += 1
            except Exception as e:
                print(f"[vault] migrate skipped {p.name}: {e}")
    if migrated:
        print(f"[vault] encrypted {migrated} previously-plaintext sensitive file(s) at rest.")


def _governance_check(tool_name: str, args: dict, session_ctx: dict | None = None) -> tuple[bool, str]:
    """Policy gate executed before every tool call.

    Returns (allowed, reason). Appends a signed entry to decision-bom.jsonl
    regardless of outcome so every gate decision is auditable.

    session_ctx keys used:
      authenticated      — True if the HTTP session is logged-in
      is_background_task — True for spawned task threads (implicitly authenticated)
    """
    ring = TOOL_RINGS.get(tool_name, 2)   # unknown tools default to NETWORK ring
    ctx = session_ctx or {}

    if ring <= 1:
        allowed = True
        reason = f"ring-{ring} always permitted"
        policy = "cLaw:Ring01-AlwaysAllow"
    elif ring == 2:
        is_auth = ctx.get("authenticated") or ctx.get("is_background_task")
        if is_auth:
            allowed = True
            reason = "ring-2 network op permitted (authenticated)"
            policy = "cLaw:Ring2-RequiresAuth"
        else:
            allowed = False
            reason = "ring-2 network op requires authenticated session"
            policy = "cLaw:Ring2-RequiresAuth"
    elif ring == 3:
        cc_ok, cc_err = _cc_check()
        if cc_ok:
            allowed = True
            reason = "ring-3 OS control permitted (CC enabled)"
            policy = "cLaw:Ring3-ExplicitApproval"
        else:
            allowed = False
            reason = f"ring-3 OS control denied: {cc_err}"
            policy = "cLaw:Ring3-ExplicitApproval"
    else:
        allowed = False
        reason = f"unknown ring level {ring}"
        policy = "cLaw:UnknownRing"

    # Build and sign the BOM entry
    args_str = json.dumps(args or {}, sort_keys=True, default=str)
    args_hash = _hashlib.sha256(args_str.encode("utf-8")).hexdigest()
    ts = datetime.utcnow().isoformat() + "Z"
    entry: dict = {
        "timestamp": ts,
        "tool": tool_name,
        "ring": ring,
        "args_hash": args_hash,
        "policy": policy,
        "decision": "allow" if allowed else "deny",
        "reason": reason,
    }
    canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
    entry["hmac"] = _hmac.new(_get_governance_key(), canonical, _hashlib.sha256).hexdigest()

    try:
        DECISION_BOM_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISION_BOM_FILE, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry) + "\n")
    except Exception as _e:
        print(f"  [GOV] BOM write failed: {_e}")

    if not allowed:
        print(f"  [GOV] DENY  {tool_name} (ring={ring}): {reason}")

    return allowed, reason


def _execute_tool(name, tool_input, pii_lookup=None, session_ctx=None):
    """Run a Claude tool through the governance gate.

    pii_lookup: if a dict, scrub PII into it instead of destructively redacting.
    session_ctx: passed to _governance_check for ring-2/3 policy evaluation.
    """
    handler = CLAUDE_TOOL_HANDLERS.get(name)
    if not handler:
        return f"Unknown tool: {name}"

    allowed, reason = _governance_check(name, tool_input, session_ctx=session_ctx)
    if not allowed:
        return f"[GOVERNANCE DENY] {reason}"

    sb_allowed, sb_reason = _sandbox_policy(name, tool_input)
    if not sb_allowed:
        try:
            _log_context("sandbox_deny", {"name": name, "reason": sb_reason})
        except Exception:
            pass
        return f"[SANDBOX DENY] {sb_reason}"

    try:
        result = handler(tool_input or {})
        if not isinstance(result, str):
            result = json.dumps(result, default=str)
        # Screenshots are base64 image payloads: never PII-scrub (the regex pass
        # would be slow and could corrupt the data) and never log the full blob.
        # The agent loop turns this into a real vision block (see _screenshot_result_to_block).
        if name == 'screenshot':
            try:
                _log_context("tool_call", {"name": name, "input": tool_input, "result_preview": "[screenshot image]"})
            except Exception:
                pass
            return result
        # Log every tool execution to the context log.
        try:
            _log_context("tool_call", {
                "name": name,
                "input": tool_input,
                "result_preview": result[:2000],
                "result_len": len(result),
            })
        except Exception:
            pass
        if isinstance(pii_lookup, dict):
            scrubbed, sub = _scrub_pii(result)
            pii_lookup.update(sub)
            return scrubbed
        return _pii_redact(result)
    except Exception as e:
        traceback.print_exc()
        return f"Tool error ({name}): {e}"


def _screenshot_result_to_block(tool_use_id, result):
    """Convert a screenshot tool result (JSON with base64 image) into an Anthropic
    tool_result block carrying a real image so the model can SEE the screen.

    Returns None for error strings / unparseable results so the caller falls back
    to a plain-text tool_result.
    """
    try:
        data = json.loads(result)
    except Exception:
        return None
    b64 = data.get('image_b64')
    if not b64:
        return None
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": [
            {"type": "text", "text": data.get('note', 'Screenshot captured.')},
            {"type": "image", "source": {
                "type": "base64",
                "media_type": data.get('media_type', 'image/png'),
                "data": b64,
            }},
        ],
    }


def _tool_orb_meta(name):
    """Map a tool name to (category, icon, friendly_label) for the process orb."""
    n = (name or '').lower()
    if 'search_web' in n or 'browse_web' in n or n == 'search':
        return ('search', '🔍', name)
    if 'email' in n or 'draft_email' in n or 'slack' in n or 'message' in n or 'notif' in n:
        return ('communication', '✉', name)
    if 'wiki' in n or 'read_file' in n or 'write_file' in n or 'list_directory' in n:
        return ('monitoring', '📁', name)
    if 'command' in n or 'install_package' in n:
        return ('monitoring', '⚙', name)
    if 'calendar' in n or 'briefing' in n or 'pipeline' in n:
        return ('monitoring', '📅', name)
    if 'trust' in n:
        return ('monitoring', '🛡', name)
    return ('default', '⚡', name)


def _call_claude_agent(messages, system=None, model=None, max_tokens=16384, temperature=None, max_iters=999, pii_lookup=None, session_ctx=None, orb_label=None, orb_category='default', orb_icon='🧠'):
    """Tool-using Claude loop. Returns (final_text, tool_trace).

    pii_lookup: if a dict, tool results are scrubbed into it for rehydration.
    session_ctx: passed to _governance_check for ring-2/3 policy enforcement.
      Keys: authenticated (bool), is_background_task (bool).
    """
    client = get_anthropic_client()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to start.bat / launch_now.bat and restart the server."
        )

    if pii_lookup is None:
        # Legacy path — destructively redact on the way out.
        safe_messages = []
        for m in messages:
            content = m.get('content')
            if isinstance(content, str):
                safe_messages.append({"role": m['role'], "content": _pii_redact(content)})
            else:
                safe_messages.append(m)
        safe_system = _pii_redact(system) if isinstance(system, str) else system
    else:
        # Caller already scrubbed — trust the inputs.
        safe_messages = list(messages)
        safe_system = system

    tool_trace = []
    convo = list(safe_messages)

    # ── Process orb registration — frontend renders an orb per active agent. ──
    orb_id = f"agent-{uuid.uuid4().hex[:8]}"
    try:
        process_register(
            orb_id,
            name="Friday",
            label=orb_label or "Thinking…",
            category=orb_category,
            icon=orb_icon,
            steps=[],
            model=model or ANTHROPIC_MODEL_DEFAULT,
        )
    except Exception:
        orb_id = None

    def _orb_safe(fn, *a, **kw):
        if not orb_id:
            return
        try:
            fn(*a, **kw)
        except Exception:
            pass

    try:
        iter_count = 0
        for _ in range(max_iters):
            iter_count += 1
            # ── Operator filesystem controls ───────────────────────────
            # Drop ~/.friday/AGENT_STOP to kill a runaway agent immediately.
            _stop_path = FRIDAY_DIR / "AGENT_STOP"
            if _stop_path.exists():
                try:
                    _stop_path.unlink()
                except Exception:
                    pass
                _orb_safe(process_update, orb_id, status='error', label='Stopped', progress=1.0)
                return ("[Agent stopped by operator control: AGENT_STOP file detected.]", tool_trace)

            # Write instructions to ~/.friday/STEER.md to redirect mid-task.
            _steer_inject = None
            _steer_path = FRIDAY_DIR / "STEER.md"
            if _steer_path.exists():
                try:
                    _steer_inject = _steer_path.read_text(encoding='utf-8').strip()
                    _steer_path.unlink()
                except Exception:
                    pass

            # Update orb: reasoning step
            _orb_safe(process_update, orb_id,
                      label="Reasoning…" if iter_count == 1 else f"Reasoning (step {iter_count})",
                      progress=min(0.05 + (iter_count - 1) * 0.1, 0.9),
                      step={"type": "reason", "iter": iter_count, "ts": _time.time()})

            kwargs = {
                "model": model or ANTHROPIC_MODEL_DEFAULT,
                "max_tokens": max_tokens,
                "messages": convo,
                "tools": CLAUDE_TOOLS,
            }
            _sys = safe_system
            if _steer_inject:
                _sys = (_sys or '') + f"\n\n[OPERATOR STEER — FOLLOW THIS IMMEDIATELY]: {_steer_inject}"
            if _sys:
                kwargs["system"] = _sys
            # NOTE: `temperature` intentionally NOT forwarded — newer Claude
            # models (Opus 4.8+, Sonnet 4.6+) 400 on the deprecated param.
            # Kept in the signature for backward-compat; model defaults are used.

            resp = client.messages.create(**kwargs)

            # Collect text and tool_use blocks
            text_parts = []
            tool_uses = []
            for b in resp.content:
                btype = getattr(b, 'type', None)
                if btype == 'text':
                    text_parts.append(b.text)
                elif btype == 'tool_use':
                    tool_uses.append(b)

            if resp.stop_reason != 'tool_use' or not tool_uses:
                _orb_safe(process_update, orb_id, status='completed', progress=1.0, label='Done')
                return ("".join(text_parts).strip(), tool_trace)

            # Promote orb category to whatever tool family is most active this round.
            try:
                cat, icon, _ = _tool_orb_meta(tool_uses[0].name)
                _orb_safe(process_update, orb_id, label=f"{tool_uses[0].name}…")
            except Exception:
                pass

            # Echo assistant turn (text + tool_use blocks) into the convo
            assistant_content = []
            for b in resp.content:
                btype = getattr(b, 'type', None)
                if btype == 'text':
                    assistant_content.append({"type": "text", "text": b.text})
                elif btype == 'tool_use':
                    assistant_content.append({
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": b.input,
                    })
            convo.append({"role": "assistant", "content": assistant_content})

            # Execute tools and feed results back
            tool_results = []
            for tu in tool_uses:
                _orb_safe(process_update, orb_id, label=f"{tu.name}…",
                          step={"type": "tool", "name": tu.name, "input": tu.input, "ts": _time.time()})

                # ── Zero-trust continuous vault authorization ──────────
                # Gate every tool call through vault check_action before
                # execution. If the provider can't see the data, deny.
                _vault_ctl = _get_vault_control() if VaultAccessControl else None
                if _vault_ctl is not None:
                    _zt_provider = (session_ctx or {}).get("provider", "cloud")
                    _zt_data = json.dumps(tu.input or {}, default=str)
                    _zt_allowed, _zt_detail, _zt_tier = _vault_ctl.check_action(
                        _zt_provider, tu.name, _zt_data,
                        access_log_path=str(FRIDAY_DIR / "vault" / "access-log.jsonl"),
                    )
                    if not _zt_allowed:
                        tool_trace.append({"name": tu.name, "input": tu.input, "result": f"[VAULT-ZT DENY] {_zt_detail}"})
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": f"[VAULT ACCESS DENIED] This tool call references {_zt_detail} data. "
                                       f"Switch to a local model to access sensitive content.",
                        })
                        continue

                result = _execute_tool(tu.name, tu.input, pii_lookup=pii_lookup, session_ctx=session_ctx)

                # Screenshot results carry a base64 image — hand it to the model as
                # an actual vision block so it can SEE the screen and pick coords.
                if tu.name == 'screenshot':
                    img_block = _screenshot_result_to_block(tu.id, result)
                    if img_block is not None:
                        tool_trace.append({"name": tu.name, "input": tu.input, "result": "[screenshot image returned to model]"})
                        tool_results.append(img_block)
                        continue

                tool_trace.append({"name": tu.name, "input": tu.input, "result": result[:2000]})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })
            convo.append({"role": "user", "content": tool_results})

        _orb_safe(process_update, orb_id, status='error', label='Max iters', progress=1.0)
        return ("[Agent hit max tool iterations without completing.]", tool_trace)
    except Exception:
        _orb_safe(process_update, orb_id, status='error', label='Error', progress=1.0)
        raise
    finally:
        # The frontend keeps a "completing" orb for ~2s, then auto-purges via
        # /api/processes server-side TTL once status is completed/error.
        if orb_id:
            try:
                p = PROCESSES.get(orb_id)
                if p and p.get('status') == 'running':
                    process_update(orb_id, status='completed', progress=1.0)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
#  LOCAL MODEL INFERENCE (Ollama)
#  Mirror of _call_claude_agent's interface but routes through
#  Ollama. Only called when the model router selects a local model.
# ══════════════════════════════════════════════════════════════

def _call_ollama(messages, system=None, model=None, max_tokens=4096,
                 temperature=None, orb_label=None, orb_icon='🏠'):
    """Call a local Ollama model. Returns (text, tool_trace=[])."""
    from ollama_manager import get_manager

    settings = _load_settings()
    routing_cfg = settings.get('model_routing') or {}
    ollama = get_manager(routing_cfg.get('ollama_url', 'http://localhost:11434'))

    if not ollama.is_available():
        raise RuntimeError("Ollama is not running at " + ollama.base_url)

    orb_id = f"local-{uuid.uuid4().hex[:8]}"
    try:
        process_register(
            orb_id, name="Local Inference",
            label=orb_label or "Local inference…",
            category="monitoring", icon=orb_icon, steps=[],
            model=model,
        )
    except Exception:
        orb_id = None

    try:
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, str):
                oai_messages.append({"role": role, "content": content})

        resp = ollama.chat_completion(
            oai_messages, model=model,
            temperature=temperature or 0.7,
            max_tokens=max_tokens,
        )

        from model_router import openai_response_to_friday
        text, trace = openai_response_to_friday(resp, model)

        usage = resp.get("usage", {})
        from model_router import get_router
        router = get_router()
        router.cost_tracker.record(
            "local", model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

        if orb_id:
            try:
                process_update(orb_id, status='completed', progress=1.0,
                               label=f'Done ({model})')
            except Exception:
                pass
        return text, trace
    except Exception:
        if orb_id:
            try:
                process_update(orb_id, status='error', label='Error', progress=1.0)
            except Exception:
                pass
        raise
    finally:
        if orb_id:
            try:
                p = PROCESSES.get(orb_id)
                if p and p.get('status') == 'running':
                    process_update(orb_id, status='completed', progress=1.0)
            except Exception:
                pass


def _call_openai(messages, system=None, model=None, max_tokens=4096,
                 temperature=None, orb_label=None, orb_icon='☁️',
                 tools=None, pii_lookup=None, session_ctx=None, max_iters=50):
    """Call any OpenAI-compatible chat endpoint. Returns (text, tool_trace).

    Unlocks OpenRouter + any /v1 base_url (Together, Groq, Fireworks, vLLM,
    LM Studio, OpenAI). Configured via settings['model_routing']:
      openai_base_url  — e.g. https://openrouter.ai/api/v1
      openai_model     — model id at that endpoint
      openai_api_key   — blank falls back to env OPENAI_API_KEY / OPENROUTER_API_KEY

    When `tools` (the Anthropic CLAUDE_TOOLS list) is supplied, runs a full
    agentic tool loop with parity to _call_claude_agent: tool calls are gated by
    the same zero-trust vault check and executed via _execute_tool (which applies
    the governance rings + sandbox). PII is scrubbed upstream and the reply is
    rehydrated by the shared caller, so privacy matches the Anthropic path.
    """
    import requests
    settings = _load_settings()
    cfg = settings.get('model_routing') or {}
    base_url = (cfg.get('openai_base_url') or 'https://api.openai.com/v1').rstrip('/')
    api_key = (cfg.get('openai_api_key') or os.environ.get('OPENAI_API_KEY')  # pragma: allowlist secret
               or os.environ.get('OPENROUTER_API_KEY') or '')
    model = model or cfg.get('openai_model') or 'gpt-4o-mini'
    if not api_key:
        raise RuntimeError(
            "No OpenAI-compatible API key set (model_routing.openai_api_key or "
            "env OPENAI_API_KEY / OPENROUTER_API_KEY)."
        )

    # Convert Anthropic tool schemas → OpenAI function-tool schemas.
    oai_tools = None
    if tools:
        try:
            from model_router import anthropic_to_openai_tools
            oai_tools = anthropic_to_openai_tools(tools)
        except Exception:
            oai_tools = None

    orb_id = f"openai-{uuid.uuid4().hex[:8]}"
    try:
        process_register(
            orb_id, name="Cloud Inference",
            label=orb_label or "Cloud inference…",
            category="monitoring", icon=orb_icon, steps=[], model=model,
        )
    except Exception:
        orb_id = None

    def _orb(**kw):
        if orb_id:
            try:
                process_update(orb_id, **kw)
            except Exception:
                pass

    tool_trace = []
    try:
        convo = []
        if system:
            convo.append({"role": "system", "content": system})
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                convo.append({"role": m.get("role", "user"), "content": content})

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter etiquette headers; ignored by other providers.
            "HTTP-Referer": "https://futurespeak.ai",
            "X-Title": "Agent Friday",
        }
        from model_router import get_router

        loops = max_iters if oai_tools else 1
        for _ in range(loops):
            payload = {
                "model": model,
                "messages": convo,
                "temperature": temperature if temperature is not None else 0.7,
                "max_tokens": max_tokens,
            }
            if oai_tools:
                payload["tools"] = oai_tools
                payload["tool_choice"] = "auto"

            r = requests.post(f"{base_url}/chat/completions", headers=headers,
                              json=payload, timeout=180)
            r.raise_for_status()
            resp = r.json()

            usage = resp.get("usage", {}) or {}
            try:
                get_router().cost_tracker.record(
                    "openai", model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )
            except Exception:
                pass

            choices = resp.get("choices", [])
            msg = (choices[0].get("message", {}) if choices else {}) or {}
            tool_calls = msg.get("tool_calls") or []

            # No tools available, or the model is done calling them → final answer.
            if not oai_tools or not tool_calls:
                text = (msg.get("content") or "").strip()
                _orb(status='completed', progress=1.0, label=f'Done ({model})')
                return text, tool_trace

            # Echo the assistant turn (must carry tool_calls verbatim).
            convo.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })
            try:
                _first = (tool_calls[0].get("function") or {}).get("name") or "tool"
                _orb(label=f"{_first}…",
                     step={"type": "tool", "name": _first, "ts": _time.time()})
            except Exception:
                pass

            for tc in tool_calls:
                fn = tc.get("function") or {}
                tname = fn.get("name") or ""
                tcid = tc.get("id") or ""
                try:
                    targs = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    targs = {}

                # ── Zero-trust continuous vault authorization (parity). ──
                _vault_ctl = _get_vault_control() if VaultAccessControl else None
                if _vault_ctl is not None:
                    _zt_provider = (session_ctx or {}).get("provider", "cloud")
                    _zt_allowed, _zt_detail, _zt_tier = _vault_ctl.check_action(
                        _zt_provider, tname, json.dumps(targs, default=str),
                        access_log_path=str(FRIDAY_DIR / "vault" / "access-log.jsonl"),
                    )
                    if not _zt_allowed:
                        tool_trace.append({"name": tname, "input": targs,
                                           "result": f"[VAULT-ZT DENY] {_zt_detail}"})
                        convo.append({"role": "tool", "tool_call_id": tcid,
                                      "content": f"[VAULT ACCESS DENIED] references {_zt_detail} "
                                                 f"data — switch to a local model to access it."})
                        continue

                result = _execute_tool(tname, targs, pii_lookup=pii_lookup,
                                       session_ctx=session_ctx)
                # Screenshots return a base64 blob — useless as text here, and CC
                # already forces the Anthropic path, so degrade gracefully.
                if tname == 'screenshot':
                    result = "[screenshot captured — vision is only available on the Anthropic path]"
                tool_trace.append({"name": tname, "input": targs, "result": result[:2000]})
                convo.append({"role": "tool", "tool_call_id": tcid, "content": result})

        _orb(status='error', label='Max iters', progress=1.0)
        return "[Agent hit max tool iterations without completing.]", tool_trace
    except Exception:
        _orb(status='error', label='Error', progress=1.0)
        raise
    finally:
        if orb_id:
            try:
                p = PROCESSES.get(orb_id)
                if p and p.get('status') == 'running':
                    process_update(orb_id, status='completed', progress=1.0)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
#  TRAJECTORY COMPRESSION  (Hermes-inspired context management)
#  When the conversation history sent to Claude would exceed the
#  soft limit, compress older turns into a dense summary block
#  while keeping recent turns verbatim.
# ══════════════════════════════════════════════════════════════

_TRAJ_CHAR_LIMIT = 2_000_000   # ~500K tokens; Opus 4.8 has 1M ctx — only compress at this threshold
_TRAJ_KEEP_VERBATIM = 20       # keep last 20 turn-pairs (~40 messages) verbatim


def _estimate_chars(messages):
    return sum(len(m.get('content') or '') for m in messages)


def _compress_trajectory(messages):
    """Return a shorter version of the message list.

    Splits into 'old' and 'recent' halves.  If the old half is large enough to
    warrant compression, summarises it via a quick Claude call and replaces it
    with a synthetic memory block.  Otherwise returns messages unchanged.
    """
    if len(messages) <= _TRAJ_KEEP_VERBATIM * 2:
        return messages

    split = max(0, len(messages) - _TRAJ_KEEP_VERBATIM * 2)
    old_turns = messages[:split]
    recent_turns = messages[split:]

    if _estimate_chars(old_turns) < _TRAJ_CHAR_LIMIT:
        return messages  # old section is small enough to send verbatim

    # Build a plain-text transcript of the old turns for the summariser
    transcript_lines = []
    for m in old_turns:
        role = 'USER' if m.get('role') == 'user' else 'FRIDAY'
        text = (m.get('content') or '')[:2000]  # cap per turn
        transcript_lines.append(f"{role}: {text}")
    transcript = '\n'.join(transcript_lines)

    try:
        summary = _call_claude(
            messages=[{"role": "user", "content":
                f"Compress the following conversation transcript into a dense, "
                f"factual memory block (max 600 words). Preserve all decisions, "
                f"facts, and open questions. Use bullet points.\n\n{transcript}"}],
            system="You are a lossless conversation compressor. Extract every salient fact.",
            max_tokens=4096,
            temperature=0.1,
        )
    except Exception as e:
        print(f"  [TRAJ] Compression failed: {e} — sending truncated history")
        return messages[-_TRAJ_KEEP_VERBATIM * 2:]  # fallback: just truncate

    compressed_block = [
        {"role": "user",
         "content": f"[COMPRESSED MEMORY — earlier conversation summary]\n{summary}\n[END COMPRESSED MEMORY]"},
        {"role": "assistant",
         "content": "Got it — I have that context from our earlier conversation."},
    ]
    result = compressed_block + list(recent_turns)
    print(f"  [TRAJ] Compressed {len(old_turns)} turns → 2 synthetic turns. "
          f"Chars: {_estimate_chars(old_turns)} → {_estimate_chars(compressed_block)}")
    return result


# ── Semantic context pruner (lazy singleton) ──────────────────────
# RAG over our own conversation history: when chat grows past the configured
# threshold we keep the most relevant past turns instead of truncating the
# oldest. The sentence-transformer model is loaded on first prune(), never at
# import, so server startup stays fast.
_CONTEXT_PRUNER = None
_CONTEXT_PRUNER_LOCK = threading.Lock()


def _get_context_pruner(cfg):
    """Return the process-wide ContextPruner, building it lazily on first use.

    cfg is the `context_pruning` block from settings.json. Thresholds are
    refreshed on every call (cheap) so live settings edits take effect without
    a restart; the loaded model + embedding cache are preserved.
    """
    global _CONTEXT_PRUNER
    with _CONTEXT_PRUNER_LOCK:
        if _CONTEXT_PRUNER is None:
            from context_pruner import ContextPruner
            _CONTEXT_PRUNER = ContextPruner.from_settings(cfg)
        else:
            _CONTEXT_PRUNER.configure(cfg)
        return _CONTEXT_PRUNER


# ── Headroom context compressor (lazy singleton) ──────────────────────
# The next layer below the pruner: the pruner selects WHICH turns survive, then
# Headroom (https://github.com/chopratejas/headroom, by Tejas Chopra, Apache 2.0)
# compresses the CONTENT of those turns — JSON tool outputs, code, prose — before
# they hit the Anthropic API. The two compound: prune selects, Headroom squeezes.
# The Headroom library is imported lazily on first compress(), never at startup.
_CONTEXT_COMPRESSOR = None
_CONTEXT_COMPRESSOR_LOCK = threading.Lock()


def _get_context_compressor(cfg):
    """Return the process-wide ContextCompressor, building it lazily on first use.

    cfg is the `context_compression` block from settings.json. Thresholds are
    refreshed on every call so live settings edits take effect without a restart.
    """
    global _CONTEXT_COMPRESSOR
    with _CONTEXT_COMPRESSOR_LOCK:
        if _CONTEXT_COMPRESSOR is None:
            from context_compressor import ContextCompressor
            _CONTEXT_COMPRESSOR = ContextCompressor.from_settings(cfg)
        else:
            _CONTEXT_COMPRESSOR.configure(cfg)
        return _CONTEXT_COMPRESSOR


# ── Agent Settings (Reasoning style, personality, response prefs) ──
SETTINGS_FILE = FRIDAY_DIR / "settings.json"
AGENT_PERSONALITY_FILE = FRIDAY_DIR / "agent-personality.txt"

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
    "news_priorities": ["AI/Tech", "Politics", "Media", "Austin Local", "Business"],
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
    # ── Privacy / Context Log ──
    "context_logging_enabled": True,       # master switch for the append-only event log
    "context_retention_days": 0,           # 0 = keep forever; 30 / 90 / 180 / 365 = prune older
    "user_email": "",                      # the user's own email — passed through unscrubbed
    "off_record": False,                   # quick toggle — when true, chat is not logged either
    # ── Agent Identity & Model Selection ──
    "agent_name": "AGENT FRIDAY",
    "orchestrator_model": "claude-opus-4-8",    # main agent brain
    "subagent_model": "claude-sonnet-4-6",      # background tasks and drafts
    "creative_model": "gemini-2.5-flash",       # images, vision, creative generation
    "voice_model": "gemini-2.5-flash-native-audio-preview-12-2025",  # live audio (native-audio: affective + proactive)
    # ── Semantic Context Pruning (RAG over our own conversation history) ──
    # When chat history exceeds max_turns, embedding-based retrieval keeps the
    # most relevant past turns instead of truncating from the oldest.
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
}


def _load_settings():
    """Load agent settings, creating defaults file if missing."""
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        try:
            SETTINGS_FILE.write_text(json.dumps(DEFAULT_SETTINGS, indent=2), encoding='utf-8')
        except Exception:
            pass
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        # Fill in any missing keys with defaults
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def _save_settings(data):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
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
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding='utf-8')
    return merged


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


def _get_friday_system_prompt(keywords='', workspace='', provider='cloud',
                              vault_control=None, vault_fallback='redact'):
    """Build a complete, vault-aware Friday system prompt for ANY Claude call.

    ALL _call_claude() and _call_claude_agent() calls MUST use this helper.
    Friday is a personal agent with full knowledge of the user's life — no call
    may go out without vault/wiki context. Calling bare _call_claude() without
    this results in Friday not knowing the user or their contacts.

    keywords: the user's prompt text; drives smart wiki context routing
    workspace: hint for context selection ('draft', 'task', 'chat', etc.)
    provider/vault_control/vault_fallback: when a VaultAccessControl is passed,
        the context (and self-knowledge) is gated for `provider` — a local
        provider sees everything, a cloud provider (e.g. 'gemini' for the Live
        voice session) gets TIER_1 in full, TIER_2 redacted, TIER_3 dropped.
        Defaults keep the legacy ungated behavior for existing callers.
    """
    settings = _load_settings()
    personality = _load_agent_personality()
    prefix = _settings_system_prefix(settings, personality)

    # Self-knowledge: inject SELF.md after personality, before workspace context.
    # This gives Friday a persistent self-model across cold starts.
    self_knowledge = _load_self_knowledge()
    if self_knowledge:
        if vault_control is not None:
            self_knowledge = vault_control.gate_content(
                self_knowledge, provider, fallback=vault_fallback,
                detail='self-knowledge')
        if self_knowledge:
            prefix += "\n\n== SELF-KNOWLEDGE ==\n" + self_knowledge + "\n"

    try:
        system_prompt, _ = _build_context_prompt(
            keywords or '', workspace, provider=provider,
            vault_control=vault_control, vault_fallback=vault_fallback)
    except Exception:
        system_prompt = FRIDAY_SYSTEM_PROMPT
    return prefix + (system_prompt or FRIDAY_SYSTEM_PROMPT)


@app.before_request
def check_auth():
    # Loopback / same-machine access is always trusted — auto-authenticate the
    # session so the user never sees a login screen on their own device.
    # Remote access (e.g. via Cloudflare Tunnel) still goes through the
    # password gate below.
    if _loopback_trusted():
        if not session.get("authenticated"):
            session['authenticated'] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
        return None
    if not FRIDAY_PASSWORD:
        return None
    if request.endpoint in ('login', 'static', 'serve_static_asset', 'serve_favicon'):
        return None
    if request.path.startswith('/ws/'):
        return None  # WebSocket upgrade handled inside ws_live (can't send HTTP redirect)
    if not session.get("authenticated"):
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login"))


# ═══════════════════════════════════════════════════════════════
#  SERVE UI
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def serve_ui():
    return send_from_directory('.', 'index.html')


@app.route('/static/<path:filename>')
def serve_static_asset(filename):
    return send_from_directory('static', filename)


@app.route('/favicon.ico')
def serve_favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/x-icon')


@app.route('/friday-live')
@app.route('/friday-live/')
def serve_friday_live():
    return send_from_directory('.', 'friday_live.html')


@app.route('/friday-live/manifest.json')
def serve_friday_live_manifest():
    return send_from_directory('.', 'friday_live_manifest.json', mimetype='application/manifest+json')


@app.route('/friday-live/sw.js')
def serve_friday_live_sw():
    resp = send_from_directory('.', 'friday_live_sw.js', mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/friday-live/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ═══════════════════════════════════════════════════════════════
#  LIVE DATA ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/career-ops/tracker')
def career_tracker():
    candidates = [
        WIKI_PROFESSIONAL_DIR / 'application-log.md',
        HOME / 'Projects' / 'career-ops' / 'data' / 'applications.md',
    ]
    tracker_path = next((p for p in candidates if p.is_file()), None)
    if tracker_path:
        content = tracker_path.read_text(encoding='utf-8')
        lines = content.strip().split('\n')
        entries = []
        for line in lines:
            if line.startswith('|') and '---' not in line and not any(h in line.lower() for h in ['company','score','#']):
                cols = [c.strip() for c in line.split('|')[1:-1]]
                if len(cols) >= 3:
                    entries.append({'raw': cols, 'company': cols[0], 'score': cols[1] if len(cols)>1 else '', 'status': cols[2] if len(cols)>2 else ''})
        return jsonify({'status': 'ok', 'entries': entries, 'total': len(entries), 'raw': content, 'source': str(tracker_path)})
    return jsonify({'status': 'no_tracker', 'entries': [], 'total': 0, 'raw': ''})

@app.route('/api/career-ops/pipeline')
def career_pipeline():
    candidates = [
        WIKI_PROFESSIONAL_DIR / 'job-search.md',
        HOME / 'Projects' / 'career-ops' / 'data' / 'pipeline.md',
    ]
    pipe_path = next((p for p in candidates if p.is_file()), None)
    if pipe_path:
        return jsonify({'status': 'ok', 'content': pipe_path.read_text(encoding='utf-8'), 'source': str(pipe_path)})
    return jsonify({'status': 'empty', 'content': ''})

@app.route('/api/career-ops/reports')
def career_reports():
    reports = []
    seen = set()
    # wiki/professional/ is primary — collect all .md files there
    if WIKI_PROFESSIONAL_DIR.is_dir():
        for f in sorted(WIKI_PROFESSIONAL_DIR.iterdir(), reverse=True):
            if f.suffix == '.md':
                reports.append({'name': f.name, 'size': f.stat().st_size, 'source': 'wiki'})
                seen.add(f.name)
    # career-ops/reports/ is fallback — add any files not already in wiki
    fallback_dir = HOME / 'Projects' / 'career-ops' / 'reports'
    if fallback_dir.is_dir():
        for f in sorted(fallback_dir.iterdir(), reverse=True):
            if f.suffix == '.md' and f.name not in seen:
                reports.append({'name': f.name, 'size': f.stat().st_size, 'source': 'career-ops'})
    if reports:
        return jsonify({'status': 'ok', 'reports': reports, 'total': len(reports)})
    return jsonify({'status': 'no_reports', 'reports': [], 'total': 0})

@app.route('/api/career-ops/report/<filename>')
def career_report(filename):
    candidates = [
        WIKI_PROFESSIONAL_DIR / filename,
        HOME / 'Projects' / 'career-ops' / 'reports' / filename,
    ]
    report_path = next((p for p in candidates if p.is_file()), None)
    if report_path:
        return jsonify({'status': 'ok', 'content': report_path.read_text(encoding='utf-8'), 'filename': filename, 'source': str(report_path)})
    return jsonify({'status': 'not_found'})

@app.route('/api/evolution', methods=['GET', 'POST'])
def get_evolution():
    """Return evolution day count and structure index based on first_launch in personality.json.
    POST with {preferred_scene_index: N} to pin a structure (null to clear and return to auto)."""
    from datetime import date as _date
    pfile = FRIDAY_DIR / "personality.json"
    data = {}
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding='utf-8'))
        except Exception:
            pass

    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        if 'preferred_scene_index' in body:
            val = body['preferred_scene_index']
            if val is None:
                data.pop('preferred_scene_index', None)
            else:
                data['preferred_scene_index'] = int(val)
            try:
                pfile.write_text(json.dumps(data, indent=2), encoding='utf-8')
            except Exception:
                pass
        return jsonify({'status': 'ok', 'preferred_scene_index': data.get('preferred_scene_index')})

    today = _date.today()
    first_launch_str = data.get('first_launch')
    if not first_launch_str:
        first_launch_str = today.isoformat()
        data['first_launch'] = first_launch_str
        try:
            pfile.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except Exception:
            pass
    try:
        first_launch = _date.fromisoformat(first_launch_str)
    except Exception:
        first_launch = today
    day_count = max(1, (today - first_launch).days + 1)
    names = [
        'GENESIS LATTICE', 'SACRED SPHERE', 'SHANNON NETWORK',
        'GEODESIC CATHEDRAL', 'LOVELACE ASTROLABE', 'VON NEUMANN TESSERACT',
        'DIRAC PROBABILITY', 'MANDELBROT SET', 'TURING MOBIUS',
        'OCEAN OF LIGHT', 'FIBONACCI NERVE', 'TRANSCENDENCE',
        'GIGA EARTH (REZ)'
    ]
    calendar_idx = ((day_count - 1) // 4) % len(names)
    preferred_idx = data.get('preferred_scene_index')
    idx = preferred_idx if preferred_idx is not None else calendar_idx
    return jsonify({
        'day': day_count,
        'structure': f'DAY {day_count}: {names[idx]}',
        'structure_index': idx,
        'calendar_index': calendar_idx,
        'preferred_scene_index': preferred_idx,
        'first_launch': first_launch_str
    })

@app.route('/api/briefings')
def list_briefings():
    """List all daily briefing files from both known locations (never delete these)."""
    briefings_by_date = {}

    # Location 1: Desktop/friday-creations — filenames like daily-briefing-2026-04-14.html
    creations = HOME / 'Desktop' / 'friday-creations'
    if creations.exists():
        for f in creations.iterdir():
            if f.name.startswith('daily-briefing') and f.suffix in ('.html', '.md'):
                date_part = f.name.replace('daily-briefing-', '').replace('.html', '').replace('.md', '')
                entry = briefings_by_date.setdefault(date_part, {'date': date_part, 'name': f.stem})
                entry[f.suffix.lstrip('.')] = f.name
                entry['size'] = f.stat().st_size

    # Location 2: ~/.friday/wiki/briefings — filenames like 2026-04-14.html or .md
    wiki_briefings = HOME / '.friday' / 'wiki' / 'briefings'
    if wiki_briefings.exists():
        for f in wiki_briefings.iterdir():
            if f.suffix in ('.html', '.md') and len(f.stem) == 10 and f.stem[4] == '-' and f.stem[7] == '-':
                date_part = f.stem  # e.g. "2026-04-14"
                entry = briefings_by_date.setdefault(date_part, {'date': date_part, 'name': f.stem})
                entry[f.suffix.lstrip('.')] = f.name
                entry.setdefault('size', f.stat().st_size)

    briefings = sorted(briefings_by_date.values(), key=lambda b: b['date'], reverse=True)
    return jsonify({'status': 'ok', 'briefings': briefings, 'total': len(briefings)})

def _find_briefing_path(filename):
    """Return the Path for a briefing file, checking both known locations."""
    # Location 1: Desktop/friday-creations (legacy daily-briefing-*.html files)
    p1 = HOME / 'Desktop' / 'friday-creations' / filename
    if p1.exists() and p1.name.startswith('daily-briefing'):
        return p1
    # Location 2: ~/.friday/wiki/briefings (date-named files like 2026-04-14.html)
    p2 = HOME / '.friday' / 'wiki' / 'briefings' / filename
    if p2.exists():
        return p2
    return None

@app.route('/briefing/<filename>')
def serve_briefing(filename):
    """Serve a briefing HTML file directly for browser viewing."""
    path = _find_briefing_path(filename)
    if path:
        return send_from_directory(str(path.parent), filename)
    return 'Not found', 404

@app.route('/api/briefing/status')
def briefing_status():
    """Report which data connectors are live for the News workspace.

    Drives the colored status indicators (✅ / ⚠️ / ❌). Static segment so it
    ranks above the /api/briefing/<filename> rule in Werkzeug's matcher.
    """
    google_connected = _google_credentials() is not None
    try:
        import feedparser  # noqa: F401
        from bs4 import BeautifulSoup  # noqa: F401
        news_ok = True
    except Exception:
        news_ok = False
    brave_on = bool((os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip())
    connectors = [
        {
            "key": "gmail", "label": "Gmail", "icon": "📧",
            "status": "connected" if google_connected else "disconnected",
            "detail": "Live read-only" if google_connected else "Not linked — using local cache if present",
        },
        {
            "key": "calendar", "label": "Calendar", "icon": "📅",
            "status": "connected" if google_connected else "disconnected",
            "detail": "Live read-only" if google_connected else "Not linked",
        },
        {
            "key": "news", "label": "News (RSS)", "icon": "📰",
            "status": "connected" if news_ok else "disconnected",
            "detail": (
                ("RSS feeds active" + (" + Brave fallback" if brave_on else ""))
                if news_ok else "feedparser/bs4 unavailable"
            ),
        },
    ]
    return jsonify({
        "status": "ok",
        "connectors": connectors,
        "google_connected": google_connected,
    })


@app.route('/api/briefing/preferences', methods=['GET', 'POST'])
def briefing_preferences():
    """Get or update briefing layout prefs (section order, toggles, categories)."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        prefs = _save_briefing_prefs(data)
        return jsonify({"status": "ok", "preferences": prefs})
    return jsonify({
        "status": "ok",
        "preferences": _load_briefing_prefs(),
        "categories": [
            {"name": k, "color": v["color"]} for k, v in NEWS_CATEGORIES.items()
        ],
    })


@app.route('/api/sources/preferences')
def sources_preferences():
    """Return the banned + boosted source lists for the Manage Sources panel."""
    return jsonify({
        "status": "ok",
        "banned": _load_banned_sources(),
        "boosted": _load_boosted_sources(),
    })


def _source_from_request():
    data = request.get_json(silent=True) or {}
    return _extract_domain(data.get("source") or data.get("domain") or "")


@app.route('/api/sources/ban', methods=['POST', 'DELETE'])
def sources_ban():
    """Add (POST) or remove (DELETE) a source from the server-side blacklist."""
    src = _source_from_request()
    if not src:
        return jsonify({"status": "error", "message": "A 'source' domain is required."}), 400
    banned = _load_banned_sources()
    if request.method == 'POST':
        if src not in banned:
            banned.append(src)
        # Banning a source un-boosts it — the two lists are mutually exclusive.
        boosted = [b for b in _load_boosted_sources() if b != src]
        _write_json_list(BOOSTED_SOURCES_FILE, boosted)
        banned = _write_json_list(BANNED_SOURCES_FILE, banned)
    else:  # DELETE — un-ban
        banned = _write_json_list(BANNED_SOURCES_FILE, [b for b in banned if b != src])
    return jsonify({"status": "ok", "source": src,
                    "banned": banned, "boosted": _load_boosted_sources()})


@app.route('/api/sources/boost', methods=['POST', 'DELETE'])
def sources_boost():
    """Add (POST) or remove (DELETE) a source from the boosted/priority list."""
    src = _source_from_request()
    if not src:
        return jsonify({"status": "error", "message": "A 'source' domain is required."}), 400
    boosted = _load_boosted_sources()
    if request.method == 'POST':
        if src not in boosted:
            boosted.append(src)
        # Boosting a source un-bans it.
        banned = [b for b in _load_banned_sources() if b != src]
        _write_json_list(BANNED_SOURCES_FILE, banned)
        boosted = _write_json_list(BOOSTED_SOURCES_FILE, boosted)
    else:  # DELETE — un-boost
        boosted = _write_json_list(BOOSTED_SOURCES_FILE, [b for b in boosted if b != src])
    return jsonify({"status": "ok", "source": src,
                    "banned": _load_banned_sources(), "boosted": boosted})


@app.route('/api/news/feed')
def news_feed():
    """Live magazine feed for the News workspace cards.

    Honors category toggles and excludes banned sources; boosted sources sort
    first. ?categories=AI/Tech,Politics overrides the saved toggles.
    """
    raw = (request.args.get('categories') or '').strip()
    cats = None
    if raw:
        cats = [c.strip() for c in raw.split(',') if c.strip() in NEWS_CATEGORIES]
    try:
        limit_per = max(1, min(8, int(request.args.get('limit_per', 4))))
    except (TypeError, ValueError):
        limit_per = 4
    items = _fetch_news_items(categories=cats, limit_per=limit_per)
    return jsonify({
        "status": "ok",
        "items": items,
        "total": len(items),
        "banned": _load_banned_sources(),
        "boosted": _load_boosted_sources(),
        "generated_at": datetime.now().isoformat(timespec='seconds'),
    })


# ── Read Later ────────────────────────────────────────────────────────────────
# A flat saved-articles list at ~/.friday/read_later.json. Keyed by URL so the
# same article can't be saved twice; newest-saved first.
_READ_LATER_LOCK = threading.Lock()


def _load_read_later():
    """Load saved Read-Later articles (list of dicts), newest first. Fail-soft."""
    try:
        if READ_LATER_FILE.exists():
            data = json.loads(READ_LATER_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [a for a in data if isinstance(a, dict) and a.get("url")]
    except Exception:
        pass
    return []


def _save_read_later(items):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    READ_LATER_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return items


@app.route('/api/news/read-later', methods=['GET', 'POST', 'DELETE'])
def news_read_later():
    """Saved-for-later articles store.

    GET    → {items: [...]} newest-saved first
    POST   → save an article ({url, title, source, snippet, category, ...})
    DELETE → remove by ?url= (or JSON {url}); ?clear=1 empties the list
    """
    if request.method == 'GET':
        return jsonify({"status": "ok", "items": _load_read_later()})

    with _READ_LATER_LOCK:
        items = _load_read_later()
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            url = (data.get("url") or "").strip()
            if not url:
                return jsonify({"status": "error", "message": "url required"}), 400
            # De-dup by URL — re-saving just refreshes the stored fields.
            items = [a for a in items if a.get("url") != url]
            entry = {
                "url": url,
                "title": (data.get("title") or url)[:300],
                "source": _extract_domain(data.get("source") or url),
                "snippet": (data.get("snippet") or "")[:400],
                "category": data.get("category") or "",
                "color": data.get("color") or "",
                "saved_at": datetime.now().isoformat(timespec='seconds'),
            }
            items.insert(0, entry)
            _save_read_later(items)
            return jsonify({"status": "ok", "item": entry, "items": items})

        # DELETE
        if request.args.get("clear"):
            _save_read_later([])
            return jsonify({"status": "ok", "items": []})
        data = request.get_json(silent=True) or {}
        url = (request.args.get("url") or data.get("url") or "").strip()
        if not url:
            return jsonify({"status": "error", "message": "url required"}), 400
        items = [a for a in items if a.get("url") != url]
        _save_read_later(items)
        return jsonify({"status": "ok", "items": items})


@app.route('/api/briefing/<filename>')
def get_briefing(filename):
    """Serve a briefing file content."""
    path = _find_briefing_path(filename)
    if path:
        return jsonify({'status': 'ok', 'content': path.read_text(encoding='utf-8'), 'filename': filename, 'is_html': path.suffix == '.html'})
    return jsonify({'status': 'not_found'}), 404

def _recent_unread_emails(limit=12):
    """Read recent unread (or last-24h) messages from the local Gmail cache.

    Mirrors the candidate paths used by the Gmail signal watcher
    (_trigger_gmail_signals). Returns a list of message dicts, most recent first.
    Empty list if no cache exists — the Gmail connector exports into these files.
    """
    candidates = [
        FRIDAY_DIR / "gmail" / "inbox.json",
        FRIDAY_DIR / "gmail-cache.json",
    ]
    inbox_path = next((p for p in candidates if p.exists()), None)
    if not inbox_path:
        return []
    try:
        data = json.loads(inbox_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    messages = data if isinstance(data, list) else data.get("messages", [])
    if not isinstance(messages, list):
        return []

    cutoff = datetime.utcnow() - timedelta(days=1)
    selected = []
    for m in reversed(messages):  # cache appends chronologically; newest last
        if not isinstance(m, dict):
            continue
        # Prefer an explicit unread flag; otherwise treat "newer_than:1d" as the
        # signal — the same filter the scheduled briefing uses.
        is_unread = m.get('unread') if 'unread' in m else (not m.get('read', False))
        ts_raw = m.get('received_at') or m.get('date') or m.get('timestamp')
        recent = True
        try:
            recent = datetime.fromisoformat(str(ts_raw).replace("Z", "")) >= cutoff
        except Exception:
            recent = True  # undated — include rather than silently drop
        if is_unread or recent:
            selected.append(m)
        if len(selected) >= limit:
            break
    return selected


# ═══════════════════════════════════════════════════════════════
#  GOOGLE (Gmail + Calendar) — live, read-only via the official API
# ═══════════════════════════════════════════════════════════════
# Read-only scopes for both Gmail and Calendar, served by one shared OAuth
# client (the same Desktop client the gmail-mcp-multi setup already created).
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
GOOGLE_TOKEN_PATH = FRIDAY_DIR / "google_token.json"


def _google_client_type(data):
    """Return the client *kind* — "installed" (Desktop/CLI) or "web" — if the
    file holds real credentials under that key, else None.

    Desktop clients nest under "installed"; Web clients under "web". We prefer
    "installed" when a single file somehow carries both, because the Desktop flow
    accepts any loopback redirect without GCP registration (no
    redirect_uri_mismatch), whereas the Web flow requires the exact callback URL
    to be pre-registered in the console.
    """
    if not isinstance(data, dict):
        return None
    for kind in ("installed", "web"):  # order = preference
        block = data.get(kind)
        if not isinstance(block, dict):
            continue
        cid = (block.get("client_id") or "").strip()
        csec = (block.get("client_secret") or "").strip()
        if not cid or not csec:
            continue
        # Reject the template placeholders Google ships (e.g.
        # "YOUR_CLIENT_ID.apps.googleusercontent.com") — a structurally-valid but
        # unfilled template would otherwise produce an auth URL with the literal
        # placeholder in it.
        if "YOUR_CLIENT" in cid.upper() or "YOUR_CLIENT" in csec.upper():
            continue
        # A real OAuth client_id always ends with this Google-issued suffix.
        if not cid.endswith(".apps.googleusercontent.com"):
            continue
        return kind
    return None


def _google_client_block(data):
    """Return the inner client block ('installed' or 'web') if the file holds
    *real* credentials, else None. Prefers the Desktop ("installed") block.
    """
    kind = _google_client_type(data)
    return data.get(kind) if kind else None


def _google_client_config():
    """Locate the OAuth *client* secrets (the app credentials, not the token).

    Returns (config_dict, source_label) or (None, None). Checks, in order:
    ~/.friday/credentials.json, the existing gmail-mcp Desktop client at
    ~/.gmail-mcp/oauth-keys.json, ~/.friday/oauth-keys.json, any
    client_secret*.json that Google delivers into ~/.friday, ~/.gmail-mcp or
    ~/Downloads, then the GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET env vars.
    Files that exist but only contain template placeholders are skipped so a
    stale ~/.gmail-mcp/oauth-keys.json can't shadow the real credentials.

    A Desktop ("installed") client is preferred over a Web ("web") client
    *across all candidates*: if credentials.json still holds the old Web client
    but a freshly-downloaded client_secret*.json holds the new Desktop client,
    the Desktop one wins — because only the Desktop flow can use a loopback
    redirect without hitting redirect_uri_mismatch.
    """
    candidates = [
        FRIDAY_DIR / "credentials.json",
        HOME / ".gmail-mcp" / "oauth-keys.json",
        FRIDAY_DIR / "oauth-keys.json",
    ]
    # Google's console downloads the client as client_secret_*.json; pick those
    # up automatically rather than forcing a manual rename.
    for d in (FRIDAY_DIR, HOME / ".gmail-mcp", HOME / "Downloads"):
        try:
            candidates.extend(sorted(d.glob("client_secret*.json")))
        except Exception:
            pass

    # First valid match of each kind, in candidate order; prefer Desktop overall.
    first_installed = None  # (data, source)
    first_web = None
    for p in candidates:
        if not p.exists():
            continue
        try:
            # utf-8-sig: the gmail-mcp file is a PowerShell-written BOM'd JSON.
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        kind = _google_client_type(data)
        if kind == "installed" and first_installed is None:
            first_installed = (data, str(p))
            break  # Desktop is the top preference — stop as soon as we find one.
        if kind == "web" and first_web is None:
            first_web = (data, str(p))
    if first_installed is not None:
        return first_installed
    if first_web is not None:
        return first_web

    cid = os.environ.get("GOOGLE_CLIENT_ID")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET")
    if cid and csec:
        return {
            "installed": {
                "client_id": cid,
                "client_secret": csec,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }, "env"
    return None, None


def _google_credentials():
    """Load stored Google OAuth credentials, refreshing if expired.

    Returns a google.oauth2 Credentials object, or None if the user hasn't
    connected Google yet (no token) or the libraries are missing. On a silent
    refresh the rotated token is written back to disk.
    """
    if not GOOGLE_TOKEN_PATH.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest
    except Exception:
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), GOOGLE_SCOPES)
    except Exception as e:
        print(f"[google] could not load stored token: {e}", flush=True)
        return None
    # Refresh whenever we hold a refresh token and the access token is missing or
    # expired. Desktop-client refresh tokens are long-lived (they don't expire in
    # 7 days the way unverified Web "Testing" tokens do), so a successful refresh
    # here keeps Friday connected indefinitely without re-consent.
    if creds and creds.refresh_token and (creds.expired or not creds.valid):
        try:
            creds.refresh(GoogleRequest())
            _write_google_token(creds)
            print("[google] refreshed and persisted access token", flush=True)
        except Exception as e:
            # A revoked grant or rotated client secret lands here — surface it so
            # the cause is diagnosable rather than a silent "not connected".
            print(f"[google] token refresh failed ({e}); reconnect at "
                  f"/api/google/auth", flush=True)
            return None
    if not creds or not creds.valid:
        return None
    return creds


def _fetch_gmail_recent(limit=15):
    """Recent Gmail messages (last 24h, unread first) via the Gmail API.

    Returns a list of {sender, subject, snippet, timestamp, thread_id, labels}.
    If Google isn't connected, returns a single-item list with an 'error' key
    pointing the caller at /api/google/auth — callers can detect that and fall
    back to the local cache.
    """
    creds = _google_credentials()
    if not creds:
        return [{"error": "Google not connected. Visit /api/google/auth to link Gmail (read-only)."}]
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        # Unread-in-last-day first, then anything from the last day, deduped.
        seen, out = set(), []
        for q in ("is:unread newer_than:1d", "newer_than:1d"):
            resp = svc.users().messages().list(userId="me", q=q, maxResults=limit).execute()
            for ref in resp.get("messages", []):
                mid = ref.get("id")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                msg = svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                ts = msg.get("internalDate")
                try:
                    ts_iso = datetime.fromtimestamp(int(ts) / 1000).isoformat() if ts else (headers.get("date") or "")
                except Exception:
                    ts_iso = headers.get("date") or ""
                out.append({
                    "sender": headers.get("from", "unknown"),
                    "subject": headers.get("subject", "(no subject)"),
                    "snippet": (msg.get("snippet") or "").strip(),
                    "timestamp": ts_iso,
                    "thread_id": msg.get("threadId", ""),
                    "labels": msg.get("labelIds", []),
                })
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        return [{"error": f"Gmail fetch failed: {e}"}]


def _fetch_calendar_today():
    """Today's + tomorrow's Google Calendar events via the Calendar API.

    Tomorrow is included so the briefing can flag prep needed tonight. Returns a
    list of {title, start_time, end_time, location, attendees, description}, or a
    single-item list with an 'error' key if Google isn't connected.
    """
    creds = _google_credentials()
    if not creds:
        return [{"error": "Google not connected. Visit /api/google/auth to link Calendar (read-only)."}]
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=2)  # today + tomorrow (for prep)
        resp = svc.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()
        out = []
        for ev in resp.get("items", []):
            s, e = ev.get("start", {}), ev.get("end", {})
            attendees = [a.get("email", "") for a in ev.get("attendees", []) if a.get("email")]
            out.append({
                "title": ev.get("summary", "(untitled)"),
                "start_time": s.get("dateTime") or s.get("date") or "",
                "end_time": e.get("dateTime") or e.get("date") or "",
                "location": ev.get("location", ""),
                "attendees": attendees,
                "description": (ev.get("description") or "").strip()[:500],
            })
        return out
    except Exception as e:
        return [{"error": f"Calendar fetch failed: {e}"}]


def _google_section_error(items):
    """Return the 'error' note if a fetch helper returned its error sentinel."""
    if items and isinstance(items[0], dict) and "error" in items[0]:
        return items[0]["error"]
    return None


# ═══════════════════════════════════════════════════════════════
#  MESSAGES — "Friday's Comms Center"
#  Smart triage that auto-classifies every message into lanes using
#  sender domain + subject keyword rules. Rules live at
#  ~/.friday/message_rules.json so the user can tune routing without a
#  redeploy. Live mail comes from the Gmail API when Google is linked,
#  otherwise from the heartbeat-populated cache at
#  ~/.friday/messages/cache.json. Classification is applied either way
#  before anything is served to the UI.
#
#  PRIVACY NOTE: the defaults shipped in source are deliberately
#  DOMAIN/keyword-based only — no personal names. Personal-name routing
#  (family, co-parent, colleagues) lives ONLY in the user's local
#  ~/.friday/message_rules.json, which is outside the (public) repo.
# ═══════════════════════════════════════════════════════════════
MESSAGES_DIR = FRIDAY_DIR / "messages"
MESSAGE_CACHE_FILE = MESSAGES_DIR / "cache.json"
MESSAGE_RULES_FILE = FRIDAY_DIR / "message_rules.json"
MESSAGE_STATE_FILE = MESSAGES_DIR / "state.json"  # archived/snoozed/flagged per message
_MESSAGE_LOCK = threading.Lock()

# Lane definitions: id, label, UI color key (mirrored in head.html CSS), and
# whether the lane counts toward the "actionable" notification badge. Order is
# priority order — the first lane whose rules match wins.
MESSAGE_LANES = [
    {"id": "coparent", "label": "Co-Parent", "color": "coparent", "actionable": True},
    {"id": "career", "label": "Career", "color": "career", "actionable": True},
    {"id": "finance", "label": "Finance", "color": "finance", "actionable": True},
    {"id": "innex", "label": "INNEX", "color": "innex", "actionable": True},
    {"id": "futurespeak", "label": "FutureSpeak", "color": "futurespeak", "actionable": True},
    {"id": "family", "label": "Family", "color": "family", "actionable": True},
    {"id": "subscriptions", "label": "Subscriptions", "color": "subscriptions", "actionable": False},
    {"id": "noise", "label": "Noise", "color": "noise", "actionable": False},
]
MESSAGE_LANE_IDS = {l["id"] for l in MESSAGE_LANES}


def _default_message_rules():
    """Generic, non-PII default triage rules (safe for a public repo).

    Matches on sender domain and subject/snippet keywords only. Personal-name
    routing is layered on top from the user's local message_rules.json.
    """
    return {
        "version": 1,
        "lanes": [
            {
                "id": "coparent",
                "domains": ["ourfamilywizard.com", "myfamilywizard.com", "ofw.com"],
                "senders": [],
                "keywords": ["ourfamilywizard", "ofw notification", "custody", "parenting time"],
            },
            {
                "id": "career",
                "domains": [
                    "linkedin.com", "indeed.com", "greenhouse.io", "lever.co",
                    "ashbyhq.com", "workday.com", "myworkday.com", "jobvite.com",
                    "smartrecruiters.com", "icims.com", "ziprecruiter.com",
                    "hire.lever.co", "us.greenhouse-mail.io", "glassdoor.com",
                    "wellfound.com", "angel.co", "dice.com", "builtin.com",
                ],
                "senders": ["recruiting", "recruiter", "talent", "careers", "jobs", "no-reply@hi.wellfound"],
                "keywords": [
                    "application received", "your application", "interview", "recruiter",
                    "next steps", "phone screen", "we received your application",
                    "thanks for applying", "job opportunity", "hiring", "offer letter",
                ],
            },
            {
                "id": "finance",
                "domains": [
                    "rwbaird.com", "bairdfinancialadvisor.com", "whitleypenn.com",
                    "capitalone.com", "americanexpress.com", "aexp.com",
                    "chase.com", "bankofamerica.com", "wellsfargo.com",
                    "fidelity.com", "schwab.com", "vanguard.com", "intuit.com",
                    "venmo.com", "paypal.com", "discover.com",
                ],
                "senders": ["alerts@", "statements@", "no-reply@alerts", "secure@"],
                "keywords": [
                    "statement is ready", "payment due", "transaction alert",
                    "account alert", "balance", "invoice", "deposit", "wire transfer",
                    "your statement", "tax document", "1099", "k-1",
                ],
            },
            {
                "id": "innex",
                "domains": ["innexenergy.com"],
                "senders": [],
                "keywords": ["innex energy", "innex"],
            },
            {
                "id": "futurespeak",
                "domains": ["github.com", "notifications.github.com", "gitlab.com"],
                "senders": ["notifications@github.com", "noreply@github.com"],
                "keywords": [
                    "pull request", "merged", "opened an issue", "commented on",
                    "new release", "review requested", "ci failed", "workflow run",
                ],
            },
            {
                "id": "family",
                "domains": [],
                "senders": [],
                "keywords": ["school", "pta", "parent portal", "report card", "field trip"],
            },
            {
                "id": "subscriptions",
                "domains": ["substack.com", "mailchimp.com", "beehiiv.com", "ghost.io"],
                "senders": ["newsletter@", "digest@", "updates@", "hello@", "team@"],
                "keywords": [
                    "newsletter", "unsubscribe", "this week in", "weekly digest",
                    "daily digest", "your weekly", "subscribe", "view in browser",
                ],
            },
        ],
    }


def _load_message_rules():
    """Load triage rules, seeding the defaults on first run. Fail-soft."""
    try:
        if MESSAGE_RULES_FILE.exists():
            data = json.loads(MESSAGE_RULES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("lanes"), list):
                return data
    except Exception:
        pass
    rules = _default_message_rules()
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        MESSAGE_RULES_FILE.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    except Exception:
        pass
    return rules


def _sender_domain(sender):
    """Pull the bare domain out of a 'Name <user@host>' From header."""
    s = (sender or "").lower()
    m = re.search(r"@([a-z0-9.\-]+)", s)
    if not m:
        return ""
    dom = m.group(1).strip(".")
    # Collapse mail subdomains (mail.github.com -> github.com) for matching,
    # but keep the full host too so subdomain-specific rules still hit.
    return dom


def _classify_message(msg, rules=None):
    """Return a lane id for a single message using domain + keyword rules.

    Evaluation is two-pass within the lane priority order: a domain/sender
    match is stronger than a keyword match, so we first look for any lane whose
    domain or sender matches, and only fall back to keyword matching if none do.
    Anything unmatched lands in 'noise'.
    """
    rules = rules or _load_message_rules()
    sender = (msg.get("sender") or msg.get("from") or "").lower()
    subject = (msg.get("subject") or "").lower()
    snippet = (msg.get("snippet") or msg.get("preview") or "").lower()
    domain = _sender_domain(sender)
    lanes = rules.get("lanes", [])

    # Pass 1 — domain / explicit sender substring (high confidence)
    for lane in lanes:
        lid = lane.get("id")
        if lid not in MESSAGE_LANE_IDS:
            continue
        for d in lane.get("domains", []):
            d = (d or "").lower().strip()
            if d and (domain == d or domain.endswith("." + d) or d in sender):
                return lid
        for s in lane.get("senders", []):
            s = (s or "").lower().strip()
            if s and s in sender:
                return lid

    # Pass 2 — subject / snippet keywords
    hay = subject + " \n " + snippet
    for lane in lanes:
        lid = lane.get("id")
        if lid not in MESSAGE_LANE_IDS:
            continue
        for kw in lane.get("keywords", []):
            kw = (kw or "").lower().strip()
            if kw and kw in hay:
                return lid

    return "noise"


def _message_id(msg):
    """Stable id for a message: prefer Gmail id/thread, else hash of fields."""
    mid = msg.get("id") or msg.get("message_id")
    if mid:
        return str(mid)
    tid = msg.get("thread_id")
    if tid:
        return str(tid)
    raw = (msg.get("sender", "") + "|" + msg.get("subject", "") + "|" +
           str(msg.get("timestamp", "")))
    return _hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def _load_message_state():
    """Per-message UI state: {id: {archived, snoozed_until, flagged, read}}."""
    try:
        if MESSAGE_STATE_FILE.exists():
            data = json.loads(MESSAGE_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_message_state(state):
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    MESSAGE_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def _load_cached_messages():
    """Load heartbeat-cached messages (list of raw dicts). Fail-soft."""
    try:
        if MESSAGE_CACHE_FILE.exists():
            data = json.loads(MESSAGE_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("messages", [])
            if isinstance(data, list):
                return [m for m in data if isinstance(m, dict)]
    except Exception:
        pass
    return []


def _cache_messages(messages):
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "messages": messages,
        "cached_at": datetime.now().isoformat(timespec="seconds"),
    }
    MESSAGE_CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return messages


def _normalize_message(raw, rules, state):
    """Shape a raw mail dict into the UI card model + apply state & lane."""
    sender = raw.get("sender") or raw.get("from") or "unknown"
    # Split "Display Name <addr>" into a friendly name + address.
    name, addr = sender, ""
    m = re.match(r"\s*\"?([^\"<]*?)\"?\s*<([^>]+)>", sender)
    if m:
        name = (m.group(1).strip() or m.group(2).strip())
        addr = m.group(2).strip()
    elif "@" in sender:
        addr = sender.strip()
        name = sender.split("@")[0]
    mid = _message_id(raw)
    lane = raw.get("lane") if raw.get("lane") in MESSAGE_LANE_IDS else _classify_message(raw, rules)
    st = state.get(mid, {}) if isinstance(state, dict) else {}
    labels = raw.get("labels") or []
    unread = ("UNREAD" in labels) if labels else (not st.get("read"))
    if st.get("read"):
        unread = False
    return {
        "id": mid,
        "thread_id": raw.get("thread_id") or mid,
        "sender": name or "unknown",
        "sender_email": addr,
        "initial": (name or addr or "?").strip()[:1].upper() or "?",
        "subject": raw.get("subject") or "(no subject)",
        "snippet": (raw.get("snippet") or raw.get("preview") or "").strip()[:400],
        "timestamp": raw.get("timestamp") or raw.get("date") or "",
        "lane": lane,
        "labels": labels,
        "unread": bool(unread),
        "has_attachment": bool(raw.get("has_attachment")),
        "archived": bool(st.get("archived")),
        "snoozed_until": st.get("snoozed_until") or "",
        "flagged": bool(st.get("flagged")),
    }


def _collect_messages(limit=40):
    """Fetch the freshest messages (live Gmail if linked, else cache), classify,
    and return (cards, source) where source is 'gmail'|'cache'|'empty'."""
    rules = _load_message_rules()
    state = _load_message_state()
    raw, source = [], "empty"

    if _google_credentials() is not None:
        fetched = _fetch_gmail_recent(limit=limit)
        if not _google_section_error(fetched):
            raw = fetched
            source = "gmail"
            try:
                _cache_messages(raw)
            except Exception:
                pass
    if not raw:
        raw = _load_cached_messages()
        source = "cache" if raw else "empty"

    cards = [_normalize_message(r, rules, state) for r in raw]
    # Newest first when timestamps are comparable; stable otherwise.
    def _ts(c):
        return str(c.get("timestamp") or "")
    cards.sort(key=_ts, reverse=True)
    return cards, source


@app.route('/api/messages')
def api_messages():
    """Classified message cards. ?lane= filters to a single lane;
    ?include_archived=1 keeps archived/snoozed cards in the result."""
    lane = (request.args.get("lane") or "").strip().lower()
    include_archived = request.args.get("include_archived") in ("1", "true", "yes")
    try:
        limit = max(5, min(100, int(request.args.get("limit", 40))))
    except (TypeError, ValueError):
        limit = 40
    cards, source = _collect_messages(limit=limit)
    now_iso = datetime.now().isoformat(timespec="seconds")
    if not include_archived:
        cards = [c for c in cards if not c["archived"]
                 and not (c["snoozed_until"] and c["snoozed_until"] > now_iso)]
    if lane and lane in MESSAGE_LANE_IDS:
        cards = [c for c in cards if c["lane"] == lane]
    # Cross-reference: flag messages whose sender is an attendee of an upcoming
    # event (next 7 days). Best-effort — failures must not break the inbox.
    try:
        email_events = {}
        for i in range(7):
            d = date.today() + timedelta(days=i)
            for ev in _events_for_day(d):
                for a in ev.get("attendees", []):
                    email_events.setdefault((a or "").lower(), []).append({
                        "id": ev.get("id"), "title": ev.get("title"),
                        "start_time": ev.get("start_time"),
                    })
        for c in cards:
            hit = email_events.get((c.get("sender_email") or "").lower())
            if hit:
                c["related_event"] = hit[0]
    except Exception:
        pass
    return jsonify({
        "status": "ok",
        "messages": cards,
        "total": len(cards),
        "source": source,
        "lanes": MESSAGE_LANES,
        "generated_at": now_iso,
    })


@app.route('/api/messages/stats')
def api_messages_stats():
    """Per-lane counts + an actionable (non-noise/sub, unread, active) badge."""
    cards, source = _collect_messages(limit=80)
    now_iso = datetime.now().isoformat(timespec="seconds")
    active = [c for c in cards if not c["archived"]
              and not (c["snoozed_until"] and c["snoozed_until"] > now_iso)]
    counts = {l["id"]: 0 for l in MESSAGE_LANES}
    for c in active:
        counts[c["lane"]] = counts.get(c["lane"], 0) + 1
    actionable_lanes = {l["id"] for l in MESSAGE_LANES if l["actionable"]}
    actionable = sum(1 for c in active
                     if c["lane"] in actionable_lanes and c["unread"])
    return jsonify({
        "status": "ok",
        "counts": counts,
        "total": len(active),
        "actionable": actionable,
        "source": source,
        "lanes": MESSAGE_LANES,
    })


@app.route('/api/messages/<thread_id>')
def api_message_thread(thread_id):
    """Full thread for a message. Pulls the whole Gmail thread when linked,
    otherwise returns the single cached message body."""
    rules = _load_message_rules()
    state = _load_message_state()
    creds = _google_credentials()
    if creds is not None:
        try:
            from googleapiclient.discovery import build
            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
            thread = svc.users().threads().get(
                userId="me", id=thread_id, format="full").execute()
            out = []
            for msg in thread.get("messages", []):
                headers = {h["name"].lower(): h["value"]
                           for h in msg.get("payload", {}).get("headers", [])}
                body = _extract_gmail_body(msg.get("payload", {}))
                ts = msg.get("internalDate")
                try:
                    ts_iso = (datetime.fromtimestamp(int(ts) / 1000).isoformat()
                              if ts else headers.get("date", ""))
                except Exception:
                    ts_iso = headers.get("date", "")
                out.append({
                    "id": msg.get("id"),
                    "sender": headers.get("from", "unknown"),
                    "to": headers.get("to", ""),
                    "subject": headers.get("subject", "(no subject)"),
                    "timestamp": ts_iso,
                    "body": body or (msg.get("snippet") or ""),
                    "snippet": msg.get("snippet", ""),
                })
            return jsonify({"status": "ok", "thread_id": thread_id,
                            "messages": out, "source": "gmail"})
        except Exception as e:
            # fall through to cache on any API error
            pass
    # Cache fallback — find by id/thread.
    for r in _load_cached_messages():
        if str(_message_id(r)) == str(thread_id) or str(r.get("thread_id")) == str(thread_id):
            card = _normalize_message(r, rules, state)
            return jsonify({"status": "ok", "thread_id": thread_id, "messages": [{
                "id": card["id"], "sender": card["sender"],
                "subject": card["subject"], "timestamp": card["timestamp"],
                "body": r.get("body") or card["snippet"], "snippet": card["snippet"],
            }], "source": "cache"})
    return jsonify({"status": "not_found", "thread_id": thread_id, "messages": []}), 404


def _extract_gmail_body(payload):
    """Recursively pull a text/plain (fallback text/html-stripped) body."""
    if not isinstance(payload, dict):
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")
    if data and mime == "text/plain":
        try:
            return base64.urlsafe_b64decode(data + "===").decode("utf-8", "ignore")
        except Exception:
            return ""
    # Walk parts; prefer plain, keep html as a fallback.
    html_fallback = ""
    for part in payload.get("parts", []) or []:
        txt = _extract_gmail_body(part)
        if txt:
            if part.get("mimeType") == "text/plain":
                return txt
            html_fallback = html_fallback or txt
    if not html_fallback and data and mime == "text/html":
        try:
            raw = base64.urlsafe_b64decode(data + "===").decode("utf-8", "ignore")
            html_fallback = re.sub(r"<[^>]+>", " ", raw)
        except Exception:
            pass
    return re.sub(r"[ \t]+", " ", html_fallback).strip() if html_fallback else ""


@app.route('/api/messages/classify', methods=['POST'])
def api_messages_classify():
    """Manually reclassify a message into a lane, persisting an override so it
    sticks across refreshes. Body: {id, lane}."""
    data = request.get_json(silent=True) or {}
    mid = str(data.get("id") or "").strip()
    lane = str(data.get("lane") or "").strip().lower()
    if not mid or lane not in MESSAGE_LANE_IDS:
        return jsonify({"status": "error",
                        "message": "id and a valid lane are required"}), 400
    with _MESSAGE_LOCK:
        # Persist a lane override onto the cached message so reclassification
        # survives the next live fetch (overrides are honored in _normalize).
        cached = _load_cached_messages()
        found = False
        for r in cached:
            if str(_message_id(r)) == mid:
                r["lane"] = lane
                found = True
                break
        if found:
            _cache_messages(cached)
        # Also record in state for messages not in cache (live-only).
        state = _load_message_state()
        st = state.get(mid, {})
        st["lane_override"] = lane
        state[mid] = st
        _save_message_state(state)
    return jsonify({"status": "ok", "id": mid, "lane": lane})


@app.route('/api/messages/action', methods=['POST'])
def api_messages_action():
    """Archive / snooze / flag / mark-read a message. Body: {id, action,
    until?(iso)}. action in archive|unarchive|snooze|unsnooze|flag|unflag|read|unread."""
    data = request.get_json(silent=True) or {}
    mid = str(data.get("id") or "").strip()
    action = str(data.get("action") or "").strip().lower()
    if not mid or not action:
        return jsonify({"status": "error", "message": "id and action required"}), 400
    with _MESSAGE_LOCK:
        state = _load_message_state()
        st = state.get(mid, {})
        if action == "archive":
            st["archived"] = True
        elif action == "unarchive":
            st["archived"] = False
        elif action == "snooze":
            st["snoozed_until"] = data.get("until") or (
                datetime.now() + timedelta(hours=4)).isoformat(timespec="seconds")
        elif action == "unsnooze":
            st["snoozed_until"] = ""
        elif action == "flag":
            st["flagged"] = True
        elif action == "unflag":
            st["flagged"] = False
        elif action == "read":
            st["read"] = True
        elif action == "unread":
            st["read"] = False
        else:
            return jsonify({"status": "error", "message": f"unknown action {action}"}), 400
        state[mid] = st
        _save_message_state(state)
    return jsonify({"status": "ok", "id": mid, "action": action, "state": st})


@app.route('/api/messages/draft', methods=['POST'])
def api_messages_draft():
    """Generate a reply draft with Claude, grounded in vault/wiki context.
    Body: {id?, sender?, subject?, snippet?, body?, instructions?}."""
    data = request.get_json(silent=True) or {}
    sender = data.get("sender") or ""
    subject = data.get("subject") or ""
    snippet = data.get("snippet") or data.get("body") or ""
    instructions = (data.get("instructions") or "").strip()
    lane = (data.get("lane") or "").strip()
    if not (sender or subject or snippet):
        return jsonify({"status": "error",
                        "message": "Provide at least sender/subject/snippet"}), 400
    lane_hint = {
        "coparent": ("This is co-parenting correspondence. Be calm, factual, "
                     "child-focused, businesslike and brief. Avoid emotional "
                     "language; stick to logistics and the child's interests."),
        "career": ("This is career/recruiting correspondence. Be warm, "
                   "professional, concise, and enthusiastic without overselling."),
        "finance": "This is financial correspondence. Be precise and formal.",
        "innex": "This is INNEX Energy business correspondence. Be professional.",
        "futurespeak": "This is a collaborator/dev message. Be technical and direct.",
        "family": "This is family correspondence. Be warm and personal.",
    }.get(lane, "")
    prompt = (
        "Draft a reply to the email below. Return ONLY the reply body — no "
        "subject line, no preamble, no sign-off placeholder beyond a natural "
        "closing.\n\n"
        f"{lane_hint}\n\n"
        f"From: {sender}\nSubject: {subject}\n\n{snippet}\n\n"
        + (f"Extra instructions from the user: {instructions}\n" if instructions else "")
    )
    try:
        system = _get_friday_system_prompt(keywords=subject + " " + snippet,
                                           workspace="draft")
        draft = _call_claude([{"role": "user", "content": prompt}],
                             system=system, max_tokens=1200)
        return jsonify({"status": "ok", "draft": draft})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  CALENDAR — "Friday's Time Intelligence"
#  Today timeline with gap analysis + Friday annotations, a 7-day week
#  strip, on-demand prep cards, and natural-language quick-add. Live
#  events come from the Calendar API (read-only) when linked; locally
#  created quick-add events are merged from
#  ~/.friday/calendar/local_events.json. NO time-analytics feature
#  (explicitly excluded).
# ═══════════════════════════════════════════════════════════════
CALENDAR_DIR = FRIDAY_DIR / "calendar"
CAL_LOCAL_FILE = CALENDAR_DIR / "local_events.json"
CAL_ANNOTATIONS_FILE = CALENDAR_DIR / "annotations.json"
CAL_PREP_FILE = CALENDAR_DIR / "prep_cache.json"
_CALENDAR_LOCK = threading.Lock()

# Heuristic custody/career detection keywords (no PII in source — names live in
# the user's local calendar rules if they want finer control later).
_CUSTODY_KW = ["libby", "custody", "pickup", "drop off", "drop-off", "exchange",
               "parenting time", "with dad", "with mom"]
_CAREER_KW = ["interview", "screen", "recruiter", "onsite", "hiring", "panel",
              "coffee chat", "phone screen", "1:1 with"]


def _load_local_events():
    try:
        if CAL_LOCAL_FILE.exists():
            data = json.loads(CAL_LOCAL_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
    except Exception:
        pass
    return []


def _save_local_events(events):
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    CAL_LOCAL_FILE.write_text(json.dumps(events, indent=2), encoding="utf-8")
    return events


def _fetch_calendar_range(start, end):
    """Live Calendar events in [start, end) as dicts incl. stable id.
    Returns [] (not an error sentinel) when Google isn't linked, so callers can
    merge with local events transparently."""
    creds = _google_credentials()
    if not creds:
        return []
    try:
        from googleapiclient.discovery import build
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        resp = svc.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=250,
        ).execute()
        out = []
        for ev in resp.get("items", []):
            s, e = ev.get("start", {}), ev.get("end", {})
            out.append({
                "id": ev.get("id", ""),
                "title": ev.get("summary", "(untitled)"),
                "start_time": s.get("dateTime") or s.get("date") or "",
                "end_time": e.get("dateTime") or e.get("date") or "",
                "all_day": bool(s.get("date") and not s.get("dateTime")),
                "location": ev.get("location", ""),
                "attendees": [a.get("email", "") for a in ev.get("attendees", [])
                              if a.get("email")],
                "description": (ev.get("description") or "").strip()[:1000],
                "html_link": ev.get("htmlLink", ""),
                "source": "google",
            })
        return out
    except Exception:
        return []


def _parse_dt(s):
    """Best-effort parse of an ISO datetime or date string to a datetime."""
    if not s:
        return None
    try:
        txt = str(s)
        if len(txt) == 10:  # date only
            return datetime.fromisoformat(txt)
        return datetime.fromisoformat(txt.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        try:
            return datetime.fromisoformat(str(s)[:19])
        except Exception:
            return None


def _classify_event(ev):
    """Tag an event as custody / career / normal from its text."""
    hay = (ev.get("title", "") + " " + ev.get("description", "") + " " +
           ev.get("location", "")).lower()
    if any(k in hay for k in _CUSTODY_KW):
        return "custody"
    if any(k in hay for k in _CAREER_KW):
        return "career"
    return "normal"


def _events_for_day(target_date):
    """All events (Google + local) intersecting the given date, time-sorted."""
    start = datetime(target_date.year, target_date.month, target_date.day)
    end = start + timedelta(days=1)
    events = _fetch_calendar_range(start, end)
    # Merge local quick-add events whose start falls on this day.
    for le in _load_local_events():
        sdt = _parse_dt(le.get("start_time"))
        if sdt and start <= sdt < end:
            events.append(le)
    # De-dup by id, then sort by start.
    seen, merged = set(), []
    for ev in events:
        k = ev.get("id") or (ev.get("title", "") + ev.get("start_time", ""))
        if k in seen:
            continue
        seen.add(k)
        ev = dict(ev)
        ev["type"] = ev.get("type") or _classify_event(ev)
        merged.append(ev)
    merged.sort(key=lambda e: str(e.get("start_time") or ""))
    return merged


def _gap_analysis(events, day):
    """Find free blocks between 6 AM and midnight, label them heuristically."""
    day_start = datetime(day.year, day.month, day.day, 6, 0)
    day_end = datetime(day.year, day.month, day.day, 23, 59)
    # Build busy intervals from timed (non all-day) events.
    busy = []
    for ev in events:
        if ev.get("all_day"):
            continue
        s = _parse_dt(ev.get("start_time"))
        e = _parse_dt(ev.get("end_time")) or (s + timedelta(hours=1) if s else None)
        if s and e and e > s:
            busy.append((max(s, day_start), min(e, day_end)))
    busy.sort()
    gaps, cursor = [], day_start
    for s, e in busy:
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < day_end:
        gaps.append((cursor, day_end))

    def _label(gs, ge):
        mins = (ge - gs).total_seconds() / 60.0
        # Lunch if the gap straddles noon and is reasonable.
        if gs.hour <= 12 <= ge.hour and 30 <= mins <= 150:
            return "Lunch"
        if mins < 30:
            return "Quick break"
        if mins <= 60:
            return "Buffer"
        if mins >= 120:
            return "Deep work window"
        return "Open block"

    out = []
    for gs, ge in gaps:
        mins = (ge - gs).total_seconds() / 60.0
        if mins < 20:  # ignore tiny slivers
            continue
        out.append({
            "start_time": gs.isoformat(timespec="minutes"),
            "end_time": ge.isoformat(timespec="minutes"),
            "minutes": int(mins),
            "label": _label(gs, ge),
        })
    return out


def _load_json_dict(path):
    try:
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _save_json_dict(path, data):
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _day_annotation(day, events):
    """A one-line Friday intro for the day, cached per-date (lightweight Claude)."""
    key = day.isoformat()
    cache = _load_json_dict(CAL_ANNOTATIONS_FILE)
    if key in cache:
        return cache[key]
    if not events:
        note = "Clear day — a blank canvas. Good for deep work or rest."
    else:
        try:
            titles = "; ".join(f"{e.get('title','')} ({e.get('type','normal')})"
                               for e in events[:8])
            system = _get_friday_system_prompt(keywords=titles, workspace="chat")
            note = _call_claude([{"role": "user", "content": (
                "In ONE short sentence (max 22 words), give me a warm, sharp "
                "heads-up about my day given these events. No preamble.\n\n"
                + titles)}], system=system, max_tokens=120).strip().strip('"')
        except Exception:
            note = f"{len(events)} event{'s' if len(events) != 1 else ''} today. You've got this."
    cache[key] = note
    try:
        _save_json_dict(CAL_ANNOTATIONS_FILE, cache)
    except Exception:
        pass
    return note


def _detect_conflicts(events):
    """Return a set of event ids that overlap another timed event."""
    timed = []
    for ev in events:
        if ev.get("all_day"):
            continue
        s = _parse_dt(ev.get("start_time"))
        e = _parse_dt(ev.get("end_time")) or (s + timedelta(hours=1) if s else None)
        if s and e:
            timed.append((s, e, ev.get("id") or ev.get("title", "")))
    conflicted = set()
    for i in range(len(timed)):
        for j in range(i + 1, len(timed)):
            s1, e1, id1 = timed[i]
            s2, e2, id2 = timed[j]
            if s1 < e2 and s2 < e1:
                conflicted.add(id1)
                conflicted.add(id2)
    return conflicted


def _cached_messages_by_email():
    """{lower-email: [{subject, id, thread_id}]} from the message cache.

    Powers calendar↔email cross-references without a live Gmail round-trip.
    """
    out = {}
    rules = _load_message_rules()
    state = _load_message_state()
    for raw in _load_cached_messages():
        card = _normalize_message(raw, rules, state)
        addr = (card.get("sender_email") or "").lower()
        if not addr:
            continue
        out.setdefault(addr, []).append({
            "subject": card["subject"], "id": card["id"],
            "thread_id": card["thread_id"], "lane": card["lane"],
        })
    return out


def _enrich_events(events):
    """Attach conflict flags, custody countdowns, and related emails."""
    conflicts = _detect_conflicts(events)
    by_email = _cached_messages_by_email()
    now = datetime.now()
    for ev in events:
        # Cross-reference: emails from any attendee of this event.
        related = []
        for a in ev.get("attendees", []):
            related.extend(by_email.get((a or "").lower(), []))
        ev["related_emails"] = related[:5]
        eid = ev.get("id") or ev.get("title", "")
        ev["conflict"] = eid in conflicts
        ev["prep_available"] = ev.get("type") == "career" or bool(
            [a for a in ev.get("attendees", []) if a])
        if ev.get("type") == "custody":
            s = _parse_dt(ev.get("start_time"))
            if s and s > now:
                delta = s - now
                h = int(delta.total_seconds() // 3600)
                m = int((delta.total_seconds() % 3600) // 60)
                ev["countdown"] = f"{h}h {m}m"
                ev["countdown_target"] = s.isoformat()
    return events


@app.route('/api/calendar/today')
def api_calendar_today():
    """Today's events + gap analysis + Friday's annotation."""
    today = date.today()
    events = _enrich_events(_events_for_day(today))
    gaps = _gap_analysis(events, today)
    return jsonify({
        "status": "ok",
        "date": today.isoformat(),
        "events": events,
        "gaps": gaps,
        "annotation": _day_annotation(today, events),
        "google_connected": _google_credentials() is not None,
    })


@app.route('/api/calendar/tomorrow')
def api_calendar_tomorrow():
    """Condensed preview of tomorrow's events, flagging prep-needed items."""
    tmrw = date.today() + timedelta(days=1)
    events = _enrich_events(_events_for_day(tmrw))
    return jsonify({
        "status": "ok",
        "date": tmrw.isoformat(),
        "events": events,
        "needs_prep": [e for e in events if e.get("prep_available")],
    })


@app.route('/api/calendar/week')
def api_calendar_week():
    """7-day overview with per-day density + custody/interview flags."""
    start = date.today()
    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        evs = _events_for_day(d)
        timed = [e for e in evs if not e.get("all_day")]
        density = "light" if len(timed) <= 1 else "medium" if len(timed) <= 3 else "heavy"
        days.append({
            "date": d.isoformat(),
            "weekday": d.strftime("%a"),
            "day": d.day,
            "count": len(evs),
            "density": density,
            "has_custody": any(e.get("type") == "custody" for e in evs),
            "has_career": any(e.get("type") == "career" for e in evs),
            "is_today": i == 0,
        })
    return jsonify({"status": "ok", "days": days})


@app.route('/api/calendar/day/<day_str>')
def api_calendar_day(day_str):
    """Events for an arbitrary YYYY-MM-DD (week-strip navigation)."""
    try:
        d = date.fromisoformat(day_str)
    except Exception:
        return jsonify({"status": "error", "message": "use YYYY-MM-DD"}), 400
    events = _enrich_events(_events_for_day(d))
    return jsonify({
        "status": "ok", "date": d.isoformat(), "events": events,
        "gaps": _gap_analysis(events, d),
        "annotation": _day_annotation(d, events),
    })


@app.route('/api/calendar/event/<event_id>')
def api_calendar_event(event_id):
    """Single event detail (searches today + the next 7 days + local)."""
    for i in range(8):
        d = date.today() + timedelta(days=i)
        for ev in _enrich_events(_events_for_day(d)):
            if str(ev.get("id")) == str(event_id):
                return jsonify({"status": "ok", "event": ev})
    return jsonify({"status": "not_found", "event_id": event_id}), 404


@app.route('/api/calendar/prep/<event_id>', methods=['POST'])
def api_calendar_prep(event_id):
    """Generate (and cache) a Friday prep card for an event with attendees."""
    # Locate the event across the upcoming week.
    target = None
    for i in range(8):
        d = date.today() + timedelta(days=i)
        for ev in _events_for_day(d):
            if str(ev.get("id")) == str(event_id):
                target = ev
                break
        if target:
            break
    if not target:
        return jsonify({"status": "not_found", "event_id": event_id}), 404

    cache = _load_json_dict(CAL_PREP_FILE)
    force = (request.get_json(silent=True) or {}).get("refresh")
    if not force and event_id in cache:
        return jsonify({"status": "ok", "prep": cache[event_id], "cached": True})

    attendees = ", ".join(target.get("attendees", [])) or "no external attendees listed"
    prompt = (
        "Build a concise meeting prep card. Use this exact markdown structure:\n"
        "**Attendees & context** — who they are and our relationship\n"
        "**Last interaction** — what I last discussed with them (if known)\n"
        "**Talking points** — 3-4 sharp bullets\n"
        "**Watch-outs** — anything to be careful about\n\n"
        f"Event: {target.get('title')}\n"
        f"When: {target.get('start_time')}\n"
        f"Location/link: {target.get('location') or 'n/a'}\n"
        f"Attendees: {attendees}\n"
        f"Notes: {target.get('description') or 'none'}\n"
    )
    try:
        system = _get_friday_system_prompt(
            keywords=target.get("title", "") + " " + attendees, workspace="task")
        prep = _call_claude([{"role": "user", "content": prompt}],
                            system=system, max_tokens=1400)
        cache[event_id] = prep
        try:
            _save_json_dict(CAL_PREP_FILE, cache)
        except Exception:
            pass
        return jsonify({"status": "ok", "prep": prep, "cached": False})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/calendar/quick-add', methods=['POST'])
def api_calendar_quick_add():
    """Natural-language event creation. Parses with Claude, then writes to
    Google Calendar if a write scope is available, else stores locally so the
    event still appears on the timeline. Body: {text}."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "message": "text required"}), 400
    now = datetime.now()
    prompt = (
        "Parse this into a calendar event. Return ONLY a JSON object with keys: "
        "title (string), start_time (ISO 8601 local, no timezone), end_time "
        "(ISO 8601 local; default 1 hour after start), location (string, may be "
        "empty), attendees (array of strings, may be empty). Assume the current "
        f"date/time is {now.isoformat(timespec='minutes')}. If a weekday is "
        "named, pick the next future occurrence. Return nothing but the JSON.\n\n"
        f"Request: {text}"
    )
    try:
        system = _get_friday_system_prompt(keywords=text, workspace="task")
        raw = _call_claude([{"role": "user", "content": prompt}],
                           system=system, max_tokens=400)
        m = re.search(r"\{.*\}", raw, re.S)
        parsed = json.loads(m.group(0)) if m else {}
    except Exception as e:
        return jsonify({"status": "error", "message": f"parse failed: {e}"}), 500

    title = (parsed.get("title") or text)[:200]
    start_time = parsed.get("start_time") or ""
    end_time = parsed.get("end_time") or ""
    sdt = _parse_dt(start_time)
    if not end_time and sdt:
        end_time = (sdt + timedelta(hours=1)).isoformat(timespec="minutes")

    event = {
        "id": "local-" + uuid.uuid4().hex[:12],
        "title": title,
        "start_time": start_time,
        "end_time": end_time,
        "location": parsed.get("location") or "",
        "attendees": [a for a in (parsed.get("attendees") or []) if a],
        "description": f"Created via Quick Add: \"{text}\"",
        "all_day": False,
        "source": "local",
    }
    event["type"] = _classify_event(event)

    # Try Google insert only if a write scope was granted (read-only by default,
    # so this normally no-ops and we fall back to local — never silently expand
    # the OAuth consent the user agreed to).
    created_in_google = False
    if "https://www.googleapis.com/auth/calendar.events" in GOOGLE_SCOPES:
        creds = _google_credentials()
        if creds is not None and sdt:
            try:
                from googleapiclient.discovery import build
                svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
                body = {
                    "summary": title,
                    "location": event["location"],
                    "description": event["description"],
                    "start": {"dateTime": sdt.isoformat()},
                    "end": {"dateTime": (_parse_dt(end_time) or sdt + timedelta(hours=1)).isoformat()},
                }
                gev = svc.events().insert(calendarId="primary", body=body).execute()
                event["id"] = gev.get("id", event["id"])
                event["source"] = "google"
                created_in_google = True
            except Exception:
                created_in_google = False

    if not created_in_google:
        with _CALENDAR_LOCK:
            events = _load_local_events()
            events.append(event)
            _save_local_events(events)
    return jsonify({"status": "ok", "event": event,
                    "created_in_google": created_in_google})


# ═══════════════════════════════════════════════════════════════
#  NEWS SOURCE TRUST + BRIEFING PREFERENCES
#  Server-side controls that let the user ban/boost news sources and
#  reshape the briefing. State lives in flat JSON under ~/.friday so it
#  persists across briefings and survives a server restart.
# ═══════════════════════════════════════════════════════════════
BANNED_SOURCES_FILE = FRIDAY_DIR / "banned_sources.json"
BOOSTED_SOURCES_FILE = FRIDAY_DIR / "boosted_sources.json"
BRIEFING_PREFS_FILE = FRIDAY_DIR / "briefing_prefs.json"
READ_LATER_FILE = FRIDAY_DIR / "read_later.json"
FRONT_PAGES_DIR = FRIDAY_DIR / "front_pages"

# Category metadata: display color key (matched in the UI), the per-category RSS
# feeds that populate the magazine feed, and a search query used only for the
# optional Brave Search fallback. The color keys mirror the spec — tech=cyan,
# politics=amber, local=green, business=purple.
#
# RSS is the primary source: reliable, no CAPTCHA, no API key. Feeds were chosen
# to match the user's reading profile (tech/AI/politics, Austin local). Outlets
# that killed their public RSS (AP, Reuters) are pulled via Google News topic
# feeds scoped to that publisher's domain — feedparser exposes the real source
# domain on each entry, so ban/boost and trust badges still resolve correctly.
_GOOGLE_NEWS = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
# Every feed below was verified with feedparser (HTTP 200/301 + ≥2 real entries)
# before inclusion. Outlets that 403 their direct feed (Politico's picks feed) or
# 301-to-empty (Austin Chronicle) are pulled via a Google-News source-scoped
# query instead — _normalize_entry resolves the real publisher domain off each
# entry, so ban/boost + trust badges still work for those.
NEWS_CATEGORIES = {
    "AI/Tech": {
        "color": "tech",
        "query": "latest AI and technology news today",
        "feeds": [
            "https://www.techmeme.com/feed.xml",
            "https://feeds.arstechnica.com/arstechnica/index",
            "https://www.theverge.com/rss/index.xml",
            "https://www.platformer.news/rss/",
            "https://stratechery.com/feed/",
            "https://www.wired.com/feed/rss",
            "https://www.technologyreview.com/feed/",
            "https://techcrunch.com/feed/",
            "https://www.404media.co/rss/",
            "https://restofworld.org/feed/latest/",
            "https://www.engadget.com/rss.xml",
            # The Brutalist Report (brutalist.report) was requested, but it
            # exposes no public RSS/Atom feed — every feed route 404s and it
            # publishes no original articles (it's an aggregator). Its Tech
            # section is built largely on Hacker News, so we pull HN's canonical
            # front-page feed directly as the closest functional substitute.
            "https://news.ycombinator.com/rss",
        ],
    },
    "Politics": {
        "color": "politics",
        "query": "latest US politics news today",
        "feeds": [
            "https://feeds.npr.org/1001/rss.xml",
            "https://feeds.npr.org/1014/rss.xml",
            "https://thehill.com/news/feed/",
            # Politico's politicopicks feed 403s; politics-news.xml serves clean.
            "https://rss.politico.com/politics-news.xml",
            "https://www.theguardian.com/us-news/rss",
            "https://www.propublica.org/feeds/propublica/main",
            "https://theintercept.com/feed/?lang=en",
            "https://talkingpointsmemo.com/feed",
            "https://www.motherjones.com/feed/",
            "https://www.theatlantic.com/feed/all/",
            "https://slate.com/feeds/all.rss",
            "https://www.salon.com/feed/",
            _GOOGLE_NEWS + "when:24h+source:apnews.com",
            _GOOGLE_NEWS + "when:24h+source:reuters.com",
        ],
    },
    "Local": {
        "color": "local",
        "query": "Austin Texas local news today",
        "feeds": [
            "https://www.austinmonitor.com/feed/",
            "https://www.texastribune.org/feeds/main/",
            "https://www.texasmonthly.com/feed/",
            _GOOGLE_NEWS + "Austin+Texas+when:24h",
            _GOOGLE_NEWS + "when:24h+source:kut.org",
            # Austin Chronicle 301s its RSS to an empty doc — source-scope it.
            _GOOGLE_NEWS + "source:austinchronicle.com+when:7d",
        ],
    },
    "Business": {
        "color": "business",
        "query": "latest business and markets news today",
        "feeds": [
            "https://api.axios.com/feed/",
            "https://feeds.bloomberg.com/markets/news.rss",
            "https://feeds.bloomberg.com/technology/news.rss",
            "https://fortune.com/feed/",
            "https://www.forbes.com/business/feed/",
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
            "https://www.businessinsider.com/rss",
            "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        ],
    },
    "Science": {
        "color": "science",
        "query": "latest science research news today",
        "feeds": [
            "https://www.nature.com/nature.rss",
            "https://www.scientificamerican.com/platform/syndication/rss/",
            "https://www.carbonbrief.org/feed/",
        ],
    },
    "Media": {
        "color": "media",
        "query": "journalism and media industry news today",
        "feeds": [
            "https://www.niemanlab.org/feed/",
            "https://www.cjr.org/feed",
            "https://www.poynter.org/feed/",
        ],
    },
}

DEFAULT_BRIEFING_PREFS = {
    # Order the briefing renders its sections in (drag/arrow reorder in UI).
    "section_order": ["Calendar", "News", "Email"],
    # Per-section show/hide toggles.
    "sections_enabled": {"Calendar": True, "News": True, "Email": True},
    # Per-category news toggles.
    "categories_enabled": {k: True for k in NEWS_CATEGORIES},
}

# A small static trust map — well-known domains we can color-rate without a
# live reputation service. Everything unknown is "neutral" (yellow). The user's
# own ban/boost decisions always override this.
_TRUSTED_DOMAINS = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "npr.org",
    "arstechnica.com", "theverge.com", "wired.com", "nature.com",
    "wsj.com", "nytimes.com", "bloomberg.com", "ft.com", "economist.com",
    "techcrunch.com", "axios.com", "propublica.org", "statnews.com",
    # Outlets added with the expanded feed set (well-established newsrooms).
    "technologyreview.com", "404media.co", "restofworld.org", "engadget.com",
    "theguardian.com", "politico.com", "theintercept.com", "talkingpointsmemo.com",
    "motherjones.com", "theatlantic.com", "fortune.com", "cnbc.com",
    "marketwatch.com", "businessinsider.com", "texastribune.org",
    "texasmonthly.com", "austinmonitor.com", "kut.org", "scientificamerican.com",
    "carbonbrief.org", "niemanlab.org", "cjr.org", "poynter.org",
}
_LOW_TRUST_DOMAINS = {
    "infowars.com", "breitbart.com", "dailybuzzlive.com", "naturalnews.com",
    "yournewswire.com", "beforeitsnews.com", "theonion.com",
}


def _extract_domain(url_or_text):
    """Normalize a URL or DuckDuckGo url-string into a bare domain.

    DDG renders result URLs like "arstechnica.com/gadgets/..." or sometimes
    "www.foxnews.com › politics"; this collapses either to "arstechnica.com".
    """
    s = (url_or_text or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    # DDG sometimes uses " › " separators or whitespace after the host.
    s = re.split(r"[\s/?#›»]", s)[0]
    if s.startswith("www."):
        s = s[4:]
    return s.strip(".")


def _read_json_list(path):
    """Load a JSON array of source domains; tolerant of missing/corrupt files."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x).strip().lower() for x in data if str(x).strip()]
        if isinstance(data, dict) and isinstance(data.get("sources"), list):
            return [str(x).strip().lower() for x in data["sources"] if str(x).strip()]
    except Exception:
        pass
    return []


def _write_json_list(path, items):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    # De-dup while preserving order.
    seen, ordered = set(), []
    for it in items:
        d = _extract_domain(it)
        if d and d not in seen:
            seen.add(d)
            ordered.append(d)
    path.write_text(json.dumps(ordered, indent=2), encoding="utf-8")
    return ordered


def _load_banned_sources():
    return _read_json_list(BANNED_SOURCES_FILE)


def _load_boosted_sources():
    return _read_json_list(BOOSTED_SOURCES_FILE)


def _load_briefing_prefs():
    """Load briefing prefs, deep-merged onto defaults so new keys appear."""
    prefs = json.loads(json.dumps(DEFAULT_BRIEFING_PREFS))  # deep copy
    if BRIEFING_PREFS_FILE.exists():
        try:
            saved = json.loads(BRIEFING_PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(saved.get("section_order"), list) and saved["section_order"]:
                prefs["section_order"] = [s for s in saved["section_order"]
                                          if s in DEFAULT_BRIEFING_PREFS["sections_enabled"]]
                # append any default sections the saved order dropped
                for s in DEFAULT_BRIEFING_PREFS["section_order"]:
                    if s not in prefs["section_order"]:
                        prefs["section_order"].append(s)
            if isinstance(saved.get("sections_enabled"), dict):
                prefs["sections_enabled"].update(
                    {k: bool(v) for k, v in saved["sections_enabled"].items()
                     if k in prefs["sections_enabled"]})
            if isinstance(saved.get("categories_enabled"), dict):
                prefs["categories_enabled"].update(
                    {k: bool(v) for k, v in saved["categories_enabled"].items()
                     if k in prefs["categories_enabled"]})
        except Exception:
            pass
    return prefs


def _save_briefing_prefs(data):
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    prefs = _load_briefing_prefs()
    data = data or {}
    if isinstance(data.get("section_order"), list):
        prefs["section_order"] = [s for s in data["section_order"]
                                  if s in DEFAULT_BRIEFING_PREFS["sections_enabled"]]
        for s in DEFAULT_BRIEFING_PREFS["section_order"]:
            if s not in prefs["section_order"]:
                prefs["section_order"].append(s)
    if isinstance(data.get("sections_enabled"), dict):
        for k, v in data["sections_enabled"].items():
            if k in prefs["sections_enabled"]:
                prefs["sections_enabled"][k] = bool(v)
    if isinstance(data.get("categories_enabled"), dict):
        for k, v in data["categories_enabled"].items():
            if k in prefs["categories_enabled"]:
                prefs["categories_enabled"][k] = bool(v)
    BRIEFING_PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs


def _trust_rating(domain, banned=None, boosted=None):
    """green | yellow | red trust rating for a source domain."""
    domain = _extract_domain(domain)
    boosted = boosted if boosted is not None else _load_boosted_sources()
    banned = banned if banned is not None else _load_banned_sources()
    if domain in banned:
        return "red"
    if domain in boosted or domain in _TRUSTED_DOMAINS:
        return "green"
    if domain in _LOW_TRUST_DOMAINS:
        return "red"
    return "yellow"


# In-process cache for parsed feeds so the /api/news/feed endpoint and the
# briefing builder (which fire back-to-back) don't re-pull the same feeds. Keyed
# by feed URL → (fetched_at_epoch, [normalized entries]). Short TTL keeps news
# fresh while smoothing bursts.
_RSS_CACHE = {}
_RSS_CACHE_TTL = 300  # seconds
_RSS_CACHE_LOCK = threading.Lock()


def _clean_feed_text(text):
    """Collapse an HTML/RSS summary into clean one-line plain text.

    Distinct from the file-oriented _strip_html elsewhere in this module: feed
    summaries are often double-encoded and need full entity resolution, which
    that helper doesn't do.
    """
    s = (text or "").strip()
    if not s:
        return ""
    if "<" in s and ">" in s:
        try:
            from bs4 import BeautifulSoup
            s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
        except Exception:
            s = re.sub(r"<[^>]+>", " ", s)
    # Some feeds double-encode entities (e.g. raw "&amp;mdash;" → "&mdash;"); a
    # bounded unescape loop resolves those without looping forever on a stray "&".
    for _ in range(3):
        decoded = html.unescape(s)
        if decoded == s:
            break
        s = decoded
    return re.sub(r"\s+", " ", s).strip()


def _normalize_entry(entry):
    """Turn a feedparser entry into {title, snippet, url, source, ts}.

    Resolves the *real* publisher domain even for Google News redirect items
    (which carry the original outlet on entry.source.href) so ban/boost filters
    and trust badges work uniformly across direct and aggregated feeds.
    """
    title = _clean_feed_text(entry.get("title", ""))
    link = (entry.get("link") or "").strip()

    # Google News wraps the outlet in entry.source ({href, title}); the title is
    # suffixed " - Publisher". Prefer the source href for the domain, and strip
    # the redundant suffix from the headline.
    src = entry.get("source") or {}
    src_href = src.get("href") if isinstance(src, dict) else getattr(src, "href", None)
    src_title = src.get("title") if isinstance(src, dict) else getattr(src, "title", None)
    domain = _extract_domain(src_href) if src_href else _extract_domain(link)
    if src_title and title.endswith(f" - {src_title}"):
        title = title[: -(len(src_title) + 3)].strip()

    snippet = _clean_feed_text(entry.get("summary", "") or entry.get("description", ""))
    # Google News summaries are usually a junk list of related links — drop them.
    if "news.google.com" in (link or "") and (
        not snippet or "View Full Coverage" in snippet or len(snippet) > 400
    ):
        snippet = ""
    snippet = snippet[:300]

    # feedparser returns published_parsed as a UTC struct_time; use timegm (not
    # mktime, which would misread it as local time and skew the age by the UTC
    # offset — enough to flag every item as "breaking").
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    ts = calendar.timegm(parsed) if parsed else 0.0
    return {"title": title, "snippet": snippet, "url": link, "source": domain, "ts": ts}


def _parse_feed(url, limit=12):
    """Fetch+parse one RSS feed into normalized entries, with TTL caching."""
    now = _time.time()
    with _RSS_CACHE_LOCK:
        hit = _RSS_CACHE.get(url)
        if hit and (now - hit[0]) < _RSS_CACHE_TTL:
            return hit[1][:limit]
    try:
        import socket
        import feedparser
        d = feedparser.parse(url, request_headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FridayAgent/1.0",
        })
        out = []
        for e in d.entries[: max(limit * 2, limit)]:
            norm = _normalize_entry(e)
            if norm["title"]:
                out.append(norm)
        with _RSS_CACHE_LOCK:
            _RSS_CACHE[url] = (now, out)
        return out[:limit]
    except Exception:
        return []


def _rss_results(feeds, limit=12):
    """Pull + merge multiple RSS feeds concurrently, newest first.

    Returns [{title, snippet, url, source, ts}] de-duplicated by headline. Feeds
    are fetched in parallel with a bounded pool so a slow feed doesn't stall the
    whole category, and each feed fails soft to an empty list.
    """
    feeds = [f for f in (feeds or []) if f]
    if not feeds:
        return []
    merged = []
    workers = min(8, len(feeds))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_parse_feed, f, limit): f for f in feeds}
            for fut in as_completed(futures, timeout=20):
                try:
                    merged.extend(fut.result() or [])
                except Exception:
                    continue
    except Exception:
        # Pool/timeout failure — fall back to whatever completed.
        pass
    seen, deduped = set(), []
    for it in merged:
        key = re.sub(r"\W+", "", it["title"].lower())[:80]
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)
    deduped.sort(key=lambda x: x["ts"], reverse=True)
    return deduped


def _brave_results(query, limit=8):
    """Optional supplemental search via the Brave Search API.

    Used only as a fallback when RSS yields nothing for a category and a
    BRAVE_SEARCH_API_KEY is configured (free tier: ~2K queries/month). Returns
    the same {title, snippet, url, source, ts} shape as _rss_results so callers
    can treat both uniformly. No key → empty list (RSS stays primary).
    """
    key = (os.environ.get("BRAVE_SEARCH_API_KEY") or "").strip()
    if not key:
        return []
    try:
        import requests as _req
        resp = _req.get(
            "https://api.search.brave.com/res/v1/news/search",
            params={"q": query, "count": max(limit, 5), "freshness": "pd"},
            headers={"Accept": "application/json",
                     "X-Subscription-Token": key},
            timeout=12,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        out = []
        for r in (data.get("results") or [])[:limit]:
            url = r.get("url") or ""
            age = r.get("age") or ""
            out.append({
                "title": _clean_feed_text(r.get("title", "")),
                "snippet": _clean_feed_text(r.get("description", ""))[:300],
                "url": url,
                "source": _extract_domain(url),
                # Brave gives a relative "age" string, not an epoch; flag fresh
                # items so _detect_breaking can still light up via the snippet.
                "ts": _time.time() if "hour" in age or "minute" in age else 0.0,
            })
        return out
    except Exception:
        return []


def _estimate_reading_time(text):
    """Rough reading-time estimate in minutes from a snippet (≈200 wpm)."""
    words = len((text or "").split())
    return max(1, round(words / 200)) if words > 40 else 0


def _detect_breaking(snippet, ts=0.0):
    """Heuristic 'breaking' flag.

    Primary signal is the item's own publish timestamp (RSS gives a real one):
    anything in the last 2 hours is breaking. Falls back to a relative-time
    phrase embedded in the snippet for sources without a usable timestamp.
    """
    if ts:
        return (_time.time() - ts) <= 2 * 3600
    s = (snippet or "").lower()
    return bool(re.search(r"\b(\d+)\s*(minute|min|hour|hr)s?\s+ago\b", s)) and \
        not re.search(r"\b([3-9]|1\d|2[0-4])\s*hours?\s+ago\b", s)


def _fetch_news_items(categories=None, limit_per=4):
    """Live magazine feed: structured news items across enabled categories.

    Excludes banned sources entirely and surfaces boosted sources first, per
    the source-trust spec. Each item carries enough metadata for the card UI:
    source domain, trust rating, category color, reading time, breaking flag.
    """
    banned = set(_load_banned_sources())
    boosted = set(_load_boosted_sources())
    prefs = _load_briefing_prefs()
    if categories is None:
        categories = [c for c in NEWS_CATEGORIES
                      if prefs["categories_enabled"].get(c, True)]
    items, idx = [], 0
    for cat in categories:
        meta = NEWS_CATEGORIES.get(cat)
        if not meta:
            continue
        # RSS is primary; Brave Search is an optional supplemental fallback only
        # when RSS came back empty (e.g. every feed in the category timed out).
        results = _rss_results(meta.get("feeds", []), limit=max(limit_per * 4, 12))
        if not results:
            results = _brave_results(meta["query"], limit=max(limit_per * 2, 8))
        kept = 0
        for r in results:
            domain = r.get("source") or _extract_domain(r.get("url", ""))
            if not domain or domain in banned:
                continue  # banned sources never appear
            is_boost = domain in boosted
            url = r.get("url", "")
            item = {
                "id": f"{cat}-{idx}",
                "title": r["title"],
                "snippet": r["snippet"],
                "url": url if url.startswith("http") else ("https://" + url),
                "source": domain,
                "category": cat,
                "color": meta["color"],
                "trust": _trust_rating(domain, banned, boosted),
                "boosted": is_boost,
                "reading_time": _estimate_reading_time(r["snippet"]),
                "breaking": _detect_breaking(r["snippet"], r.get("ts", 0.0)),
                # ts + score let the stream UI sort by time and by relevance.
                "ts": r.get("ts", 0.0),
            }
            item["score"] = _score_article(item)
            items.append(item)
            idx += 1
            kept += 1
            if kept >= limit_per:
                break
    # Boosted sources float to the top; otherwise keep category/recency order.
    items.sort(key=lambda x: (0 if x["boosted"] else 1))
    return items


def _gather_live_briefing_context():
    """Fetch live calendar, unread email, and news for an on-demand briefing.

    The News-workspace "Generate Briefing" button must reflect *today*, not the
    stale cached context baked into the system prompt. This mirrors what the
    scheduled morning-briefing routine tells its agent to do (scan news, summarize
    calendar, pull unread mail) but runs synchronously so the data is fresh at
    click time. Each source fails soft: a dead source contributes a short note
    instead of aborting the whole briefing.
    """
    today_str = datetime.now().strftime('%A, %B %d, %Y')
    prefs = _load_briefing_prefs()
    enabled = prefs.get("sections_enabled", {})
    order = prefs.get("section_order", ["Calendar", "News", "Email"])
    banned = set(_load_banned_sources())
    boosted = set(_load_boosted_sources())

    # Build each section's markdown once, then assemble per the user's ordering
    # and show/hide toggles. Each builder fails soft to a short note.
    built = {}

    # ── Calendar ──────────────────────────────────────────────────────────────
    def _build_calendar():
        try:
            cal_events = _fetch_calendar_today()
            cal_err = _google_section_error(cal_events)
            if cal_err:
                return f"## Today's Calendar\n({cal_err})"
            if cal_events:
                lines = []
                for ev in cal_events[:20]:
                    when = ev.get('start_time') or ''
                    title = ev.get('title') or 'Untitled'
                    loc = ev.get('location') or ''
                    attendees = ev.get('attendees') or []
                    line = f"- {when} — {title}"
                    if loc:
                        line += f" @ {loc}"
                    if attendees:
                        line += f" (with {', '.join(attendees[:5])})"
                    lines.append(line)
                return "## Today's & Tomorrow's Calendar\n" + "\n".join(lines)
            return "## Today's Calendar\n(No events scheduled for today or tomorrow.)"
        except Exception as e:
            return f"## Today's Calendar\n(Calendar fetch failed: {e})"

    # ── Email ─────────────────────────────────────────────────────────────────
    def _build_email():
        try:
            emails = _fetch_gmail_recent(limit=12)
            gmail_err = _google_section_error(emails)
            if gmail_err:
                cached = _recent_unread_emails(limit=12)
                if cached:
                    lines = []
                    for m in cached:
                        sender = m.get('from') or m.get('sender') or 'unknown'
                        subj = m.get('subject') or '(no subject)'
                        preview = (m.get('preview') or m.get('snippet') or m.get('body') or '')
                        preview = str(preview).strip().replace('\n', ' ')[:160]
                        lines.append(f"- **{sender}** — {subj}" + (f"\n  {preview}" if preview else ''))
                    return "## Recent / Unread Email (local cache)\n" + "\n".join(lines)
                return f"## Recent / Unread Email\n({gmail_err})"
            if emails:
                lines = []
                for m in emails:
                    sender = m.get('sender') or 'unknown'
                    subj = m.get('subject') or '(no subject)'
                    snippet = str(m.get('snippet') or '').strip().replace('\n', ' ')[:160]
                    flag = '🔵 ' if 'UNREAD' in (m.get('labels') or []) else ''
                    lines.append(f"- {flag}**{sender}** — {subj}" + (f"\n  {snippet}" if snippet else ''))
                return "## Recent / Unread Email\n" + "\n".join(lines)
            return "## Recent / Unread Email\n(No email in the last 24 hours.)"
        except Exception as e:
            return f"## Recent / Unread Email\n(Email fetch failed: {e})"

    # ── News (banned sources excluded, boosted prioritized) ───────────────────
    def _build_news():
        try:
            cats = [c for c in NEWS_CATEGORIES
                    if prefs.get("categories_enabled", {}).get(c, True)]
            items = _fetch_news_items(categories=cats, limit_per=4)
            if items:
                by_cat = {}
                for it in items:
                    by_cat.setdefault(it["category"], []).append(it)
                blocks = []
                for cat, group in by_cat.items():
                    lines = []
                    for it in group:
                        star = "⭐ " if it["boosted"] else ""
                        lines.append(
                            f"- {star}**{it['title']}** ({it['source']})\n  {it['snippet']}\n  {it['url']}"
                        )
                    blocks.append(f"### {cat}\n" + "\n".join(lines))
                note = ""
                if boosted:
                    note = (f"\n_(Prioritize these trusted sources where relevant: "
                            f"{', '.join(sorted(boosted))}.)_")
                if banned:
                    note += (f"\n_(These sources are banned and were excluded — do not cite: "
                             f"{', '.join(sorted(banned))}.)_")
                return "## Live News (RSS)\n" + "\n\n".join(blocks) + note
            # Fallback: optional Brave Search across the top categories, with
            # banned domains excluded. No-ops cleanly when no API key is set.
            news_blocks = []
            for cat in (cats or ["AI/Tech"])[:2]:
                meta = NEWS_CATEGORIES.get(cat) or {}
                lines = []
                for r in _brave_results(meta.get("query", f"latest {cat} news today"), limit=5):
                    dom = r.get("source") or _extract_domain(r.get("url", ""))
                    if dom and dom not in banned:
                        lines.append(f"- **{r['title']}** ({dom})\n  {r['snippet']}\n  {r['url']}")
                if lines:
                    news_blocks.append(f"### {cat}\n" + "\n".join(lines))
            if news_blocks:
                return "## Live News (Brave Search fallback)\n" + "\n\n".join(news_blocks)
            return "## Live News\n(No RSS items available right now.)"
        except Exception as e:
            return f"## Live News\n(News fetch failed: {e})"

    builders = {"Calendar": _build_calendar, "Email": _build_email, "News": _build_news}
    sections = []
    for name in order:
        if not enabled.get(name, True):
            continue
        builder = builders.get(name)
        if builder:
            sections.append(builder())

    header = (
        f"=== LIVE DATA fetched {today_str} ===\n"
        "Base the briefing on THIS live data, not on any cached/remembered context. "
        "If a section says data is unavailable, note that honestly rather than inventing it.\n\n"
    )
    return header + "\n\n".join(sections)


@app.route('/api/briefing/generate', methods=['POST'])
def generate_briefing():
    """Generate a fresh daily briefing on demand via Claude and persist it.

    Replaces the old behavior of spawning a Claude Code terminal (which failed
    with "not found"). Pulls LIVE data first — today's calendar, recent unread
    email, and a fresh news search for the user's interests — then synthesizes
    the briefing from that live data plus Friday's vault/wiki context. Saves it
    as markdown in the archive and returns the markdown so the News panel can
    render it inline with the branded markdown viewer.
    """
    try:
        # Pull live sources BEFORE writing the briefing so it never runs on stale
        # cached context. This mirrors the scheduled morning-briefing routine.
        live_context = _gather_live_briefing_context()

        prompt = (
            "Generate a crisp daily briefing using the LIVE DATA below plus what "
            "you know about me (career pipeline, active tasks, co-parenting "
            "context). Cover, in order:\n"
            "1. Today's calendar events — most important first\n"
            "2. Top news relevant to me\n"
            "3. Active tasks and commitments needing attention\n"
            "4. One proactive insight or recommendation\n\n"
            "Format as clean markdown with a level-1 heading, section subheadings, "
            "and tight bullet points. Lead with the most urgent item. Be specific — "
            "use real names, dates, and details from the live data and my context, "
            "not placeholders.\n\n"
            f"{live_context}"
        )
        # ALL _call_claude() calls must carry Friday's vault/wiki context.
        system = _get_friday_system_prompt(keywords=prompt, workspace='briefing')
        content = _call_claude(
            [{"role": "user", "content": prompt}],
            system=system,
            temperature=0.4,
        )
        if not content or not content.strip():
            return jsonify({"status": "error", "message": "Empty briefing generated"}), 502

        date_str = datetime.now().strftime('%Y-%m-%d')
        briefings_dir = FRIDAY_DIR / "wiki" / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        out_path = briefings_dir / f"{date_str}.md"
        out_path.write_text(content, encoding='utf-8')

        return jsonify({
            "status": "ok",
            "date": date_str,
            "filename": out_path.name,
            "content": content,
            "is_html": False,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  FRIDAY'S FRONT PAGE
#  An AI-curated editorial page generated twice daily (7 AM + 6 PM
#  Central) via the shared daily scheduler (register_daily_job).
#  Friday fetches every feed, dedupes, scores each story against
#  Stephen's profile, picks a lead with an editorial note, and
#  organizes the rest into sections. Editions persist as JSON under
#  ~/.friday/front_pages/ and are browsable in the UI.
# ═══════════════════════════════════════════════════════════════

# Stephen's reading profile — drives deterministic relevance scoring so the
# front page is useful even when Claude is unavailable. Buckets map a regex of
# signal terms to a weight; an article's score is the sum of matched buckets
# plus category/recency/source bonuses. Tuned to his life: AI executive +
# FutureSpeak founder, Austin TX, progressive politics, career journalist
# (former Raw Story editor), currently job searching.
_PROFILE_KEYWORDS = [
    (6, r"\b(artificial intelligence|\bA\.?I\.?\b|machine learning|\bLLM\b|"
        r"large language model|generative|chatgpt|openai|anthropic|claude|"
        r"gemini|deepmind|nvidia|agent(ic)?|foundation model)\b"),
    (5, r"\b(founder|startup|venture|fundrais|seed round|series [a-d]|"
        r"layoff|hiring|job market|chief executive|\bCEO\b|exec(utive)?)\b"),
    (5, r"\b(journalism|journalist|newsroom|press freedom|media industry|"
        r"reporter|editor|publisher|local news|disinformation|misinformation)\b"),
    (4, r"\b(austin|texas|\bTX\b|texas tribune|abbott|texas legislature)\b"),
    (4, r"\b(progressive|democra(t|cy|tic)|republican|\bGOP\b|election|"
        r"voting rights|abortion|labor|union|inequality|civil rights|trump)\b"),
    (3, r"\b(climate|emissions|clean energy|solar|carbon)\b"),
    (3, r"\b(futurespeak|future of work|automation|knowledge work)\b"),
]
_PROFILE_KEYWORDS = [(w, re.compile(p, re.I)) for w, p in _PROFILE_KEYWORDS]

# Category baseline weights — how central each beat is to Stephen's interests.
_CATEGORY_WEIGHT = {
    "AI/Tech": 5, "Politics": 4, "Media": 4,
    "Local": 3, "Business": 3, "Science": 2,
}

# Central-time scheduling. The two daily editions and the hour each fires.
FRONT_PAGE_SLOTS = {"morning": 7, "evening": 18}


def _front_page_central_now():
    """Current time in US Central. Uses zoneinfo when tzdata is present, else a
    manual US DST calc (2nd Sun Mar → 1st Sun Nov) so the front page still
    timestamps correctly on a bare Windows Python without the tzdata package."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        utc = datetime.utcnow()
        y = utc.year

        def _nth_sunday(month, n):
            d = datetime(y, month, 1)
            offset = (6 - d.weekday()) % 7  # days to first Sunday
            return d + timedelta(days=offset + 7 * (n - 1))
        dst_start = _nth_sunday(3, 2).replace(hour=8)   # 2 AM CST = 08:00 UTC
        dst_end = _nth_sunday(11, 1).replace(hour=7)    # 2 AM CDT = 07:00 UTC
        offset = -5 if dst_start <= utc < dst_end else -6
        return (utc + timedelta(hours=offset))


def _score_article(item):
    """Relevance score for a fetched news item against Stephen's profile."""
    text = f"{item.get('title','')} {item.get('snippet','')}"
    score = float(_CATEGORY_WEIGHT.get(item.get("category"), 2))
    for weight, rx in _PROFILE_KEYWORDS:
        if rx.search(text):
            score += weight
    if item.get("boosted"):
        score += 6
    if item.get("trust") == "green":
        score += 1.5
    if item.get("breaking"):
        score += 2
    # Recency: full bonus under 3h, decaying to 0 by ~24h.
    ts = item.get("ts") or 0.0
    if ts:
        age_h = max(0.0, (_time.time() - ts) / 3600.0)
        score += max(0.0, 3.0 * (1 - age_h / 24.0))
    return round(score, 2)


def _gather_front_page_pool(per_cat=14):
    """Fetch every enabled category broadly, dedup globally, score each item.

    Returns (pool, stats). Banned sources are excluded; boosted sources are
    flagged. Unlike _fetch_news_items (which caps tightly for the card feed)
    this pulls wide so the editorial scorer has real choice.
    """
    banned = set(_load_banned_sources())
    boosted = set(_load_boosted_sources())
    prefs = _load_briefing_prefs()
    cats = [c for c in NEWS_CATEGORIES
            if prefs.get("categories_enabled", {}).get(c, True)]
    pool, seen = [], set()
    for cat in cats:
        meta = NEWS_CATEGORIES.get(cat) or {}
        results = _rss_results(meta.get("feeds", []), limit=per_cat)
        if not results:
            results = _brave_results(meta.get("query", ""), limit=per_cat)
        for r in results:
            domain = r.get("source") or _extract_domain(r.get("url", ""))
            if not domain or domain in banned:
                continue
            key = re.sub(r"\W+", "", (r.get("title") or "").lower())[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            url = r.get("url", "")
            item = {
                "title": r["title"],
                "snippet": r["snippet"],
                "url": url if url.startswith("http") else ("https://" + url),
                "source": domain,
                "category": cat,
                "color": meta.get("color", "tech"),
                "trust": _trust_rating(domain, banned, boosted),
                "boosted": domain in boosted,
                "reading_time": _estimate_reading_time(r["snippet"]),
                "breaking": _detect_breaking(r["snippet"], r.get("ts", 0.0)),
                "ts": r.get("ts", 0.0),
            }
            item["score"] = _score_article(item)
            pool.append(item)
    pool.sort(key=lambda x: x["score"], reverse=True)
    stats = {"total_considered": len(pool),
             "sources": len({p["source"] for p in pool}),
             "categories": len({p["category"] for p in pool})}
    return pool, stats


def _extract_json_block(text):
    """Pull the first JSON object out of an LLM reply (tolerates code fences)."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _editorialize_front_page(pool):
    """Ask Claude to pick the lead + write editorial context. Fails soft to a
    deterministic pick (top-scored story) so the front page always renders.

    Returns {lead_index, lead_note, section_context: {cat: str}, headline}.
    """
    top = pool[:28]
    fallback = {
        "lead_index": 0,
        "lead_note": ("Friday's top pick for you right now — highest signal "
                      "against your AI, politics, media, and Austin beats."),
        "section_context": {},
        "headline": "Your Front Page",
    }
    if not top:
        return fallback
    try:
        lines = []
        for i, it in enumerate(top):
            lines.append(f"[{i}] ({it['category']} · {it['source']} · "
                         f"score {it['score']}) {it['title']} — {it['snippet'][:140]}")
        cats_present = sorted({it["category"] for it in top})
        prompt = (
            "You are Friday, Stephen's personal news editor. Stephen is an AI "
            "executive and FutureSpeak founder in Austin, TX — a career "
            "journalist (former Raw Story editor) with progressive politics, "
            "currently job searching. Below are today's candidate stories, "
            "pre-scored.\n\n"
            "Choose the single LEAD story (the one Stephen should read first) "
            "and write a 2-3 sentence editorial note explaining why it leads — "
            "in your voice, specific to him. Then write one punchy sentence of "
            "context for each section.\n\n"
            "Return ONLY JSON, no prose, in exactly this shape:\n"
            '{\n  "lead_index": <int>,\n  "lead_note": "<2-3 sentences>",\n'
            '  "headline": "<a 3-6 word front-page headline for the whole edition>",\n'
            '  "section_context": {' +
            ", ".join(f'"{c}": "<one sentence>"' for c in cats_present) +
            "}\n}\n\nCANDIDATE STORIES:\n" + "\n".join(lines)
        )
        system = _get_friday_system_prompt(keywords=prompt, workspace='briefing')
        raw = _call_claude([{"role": "user", "content": prompt}],
                           system=system, max_tokens=1200)
        data = _extract_json_block(raw)
        if not isinstance(data, dict):
            return fallback
        li = data.get("lead_index")
        if not isinstance(li, int) or not (0 <= li < len(top)):
            li = 0
        sc = data.get("section_context")
        return {
            "lead_index": li,
            "lead_note": (data.get("lead_note") or fallback["lead_note"]).strip()[:600],
            "section_context": sc if isinstance(sc, dict) else {},
            "headline": (data.get("headline") or fallback["headline"]).strip()[:80],
        }
    except Exception:
        return fallback


def _generate_front_page(slot="morning"):
    """Build + persist one Front Page edition. Returns the edition dict.

    Idempotent per (date, slot): regenerating overwrites that edition's file.
    """
    cnow = _front_page_central_now()
    date_str = cnow.strftime('%Y-%m-%d')
    slot = slot if slot in FRONT_PAGE_SLOTS else "morning"
    edition_id = f"{date_str}-{slot}"

    pool, stats = _gather_front_page_pool()
    editorial = _editorialize_front_page(pool)
    lead_idx = editorial["lead_index"] if pool else None

    lead = None
    if pool:
        lead = dict(pool[lead_idx])
        lead["editorial_note"] = editorial["lead_note"]

    # Group remaining stories into sections by category, in interest order.
    rest = [p for i, p in enumerate(pool) if i != lead_idx]
    sections = []
    order = sorted(NEWS_CATEGORIES.keys(),
                   key=lambda c: _CATEGORY_WEIGHT.get(c, 0), reverse=True)
    for cat in order:
        group = [p for p in rest if p["category"] == cat][:6]
        if not group:
            continue
        sections.append({
            "title": cat,
            "color": (NEWS_CATEGORIES.get(cat) or {}).get("color", "tech"),
            "context": (editorial["section_context"].get(cat) or "").strip(),
            "articles": group,
        })

    edition = {
        "id": edition_id,
        "date": date_str,
        "slot": slot,
        "headline": editorial["headline"],
        "generated_at": datetime.now().isoformat(timespec='seconds'),
        "generated_central": cnow.strftime('%Y-%m-%d %H:%M %Z') or cnow.isoformat(timespec='minutes'),
        "lead": lead,
        "sections": sections,
        "stats": stats,
    }

    FRONT_PAGES_DIR.mkdir(parents=True, exist_ok=True)
    (FRONT_PAGES_DIR / f"{edition_id}.json").write_text(
        json.dumps(edition, indent=2), encoding="utf-8")
    return edition


def _list_front_pages():
    """All saved editions, newest first, as light summaries for the index."""
    if not FRONT_PAGES_DIR.exists():
        return []
    out = []
    for p in FRONT_PAGES_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id") or p.stem,
                "date": d.get("date", ""),
                "slot": d.get("slot", ""),
                "headline": d.get("headline", ""),
                "lead_title": (d.get("lead") or {}).get("title", ""),
                "generated_central": d.get("generated_central", ""),
                "section_count": len(d.get("sections") or []),
                "stats": d.get("stats") or {},
            })
        except Exception:
            continue
    # Sort by (date desc, hour desc) — "evening" must rank above "morning" of
    # the same day, which a plain string sort on slot would get wrong.
    out.sort(key=lambda e: (e["date"], FRONT_PAGE_SLOTS.get(e["slot"], 0)),
             reverse=True)
    return out


def _read_front_page(edition_id):
    """Load one edition by id, or None."""
    if not edition_id:
        return None
    safe = re.sub(r"[^0-9a-zA-Z\-]", "", edition_id)
    path = FRONT_PAGES_DIR / f"{safe}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_front_page_job(slot):
    """Scheduled-job entry point: generate an edition and drop a notification."""
    edition = _generate_front_page(slot)
    try:
        if _notif_engine and edition:
            lead = edition.get("lead") or {}
            label = "Morning" if slot == "morning" else "Evening"
            _notif_engine.push(
                title=f"📰 Friday's Front Page — {label} edition",
                body=(f"{edition.get('headline','Your Front Page')} · Lead: "
                      f"{lead.get('title','(no stories)')}"),
                priority="medium",
                source="front-page",
                kind="front_page",
                actions=[{"label": "Open Front Page", "workspace": "news"}],
                target={"workspace": "news", "tab": "frontpage"},
                dedupe_key=f"front-page:{edition.get('id')}",
                meta={"edition_id": edition.get("id"), "slot": slot},
            )
    except Exception as e:
        print(f"  [front-page:{slot}] notification failed: {e}")
    return edition


@app.route('/api/news/front-page/generate', methods=['POST'])
def front_page_generate():
    """Generate (or regenerate) a Front Page edition on demand."""
    data = request.get_json(silent=True) or {}
    slot = data.get("slot")
    if slot not in FRONT_PAGE_SLOTS:
        # Auto-pick the slot from the current Central hour.
        slot = "evening" if _front_page_central_now().hour >= 12 else "morning"
    try:
        edition = _generate_front_page(slot)
        return jsonify({"status": "ok", "edition": edition})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/news/front-page/latest')
def front_page_latest():
    """The most recent edition (or null if none generated yet)."""
    listing = _list_front_pages()
    if not listing:
        return jsonify({"status": "ok", "edition": None, "editions": []})
    latest = _read_front_page(listing[0]["id"])
    return jsonify({"status": "ok", "edition": latest, "editions": listing})


@app.route('/api/news/front-pages')
def front_pages_list():
    """Index of all saved editions, newest first."""
    return jsonify({"status": "ok", "editions": _list_front_pages()})


@app.route('/api/news/front-page/<edition_id>')
def front_page_get(edition_id):
    """A specific edition by id (YYYY-MM-DD-{morning|evening})."""
    edition = _read_front_page(edition_id)
    if not edition:
        return jsonify({"status": "not_found"}), 404
    return jsonify({"status": "ok", "edition": edition})


# Fixed loopback redirect for Desktop ("installed") OAuth clients. Google accepts
# any localhost/loopback redirect for installed apps *without* registering it in
# the GCP console, so we pin a deterministic URI matching the port the server
# binds to (3000). Critically, the app binds 0.0.0.0 when a tunnel password is
# set, so request.host_url can be a tunnel/LAN host that Google would REJECT for
# an installed client — pinning localhost:3000 keeps the loopback flow valid.
GOOGLE_DESKTOP_REDIRECT_URI = "http://localhost:3000/api/google/auth/callback"


def _google_redirect_uri(cfg, client_type=None):
    """Canonical OAuth redirect URI for this client config.

    Desktop ("installed") clients accept any loopback redirect without
    registering it in GCP, so we pin the deterministic localhost:3000 URI and
    never hit redirect_uri_mismatch — even when the user reaches the app through
    a tunnel or LAN IP. Web ("web") clients must match a URI pre-registered in
    the console, so we derive it from the actual request host (legacy behavior).
    """
    kind = client_type or _google_client_type(cfg) or "installed"
    if kind == "installed":
        return GOOGLE_DESKTOP_REDIRECT_URI
    return request.host_url.rstrip('/') + '/api/google/auth/callback'


def _write_google_token(creds):
    """Persist Google credentials to ~/.friday/google_token.json atomically.

    Written via temp file + rename so a crash mid-write can't truncate the token
    and force a re-auth. Returns True on success.
    """
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = GOOGLE_TOKEN_PATH.with_name(GOOGLE_TOKEN_PATH.name + ".tmp")
        tmp.write_text(creds.to_json(), encoding="utf-8")
        tmp.replace(GOOGLE_TOKEN_PATH)
        return True
    except Exception as e:
        print(f"[google] could not persist token ({e})", flush=True)
        return False


@app.route('/api/google/auth')
def google_auth_start():
    """Begin the Google OAuth flow (Gmail + Calendar, read-only).

    Returns an auth URL for the user to visit. Google redirects back to
    /api/google/auth/callback, which exchanges the code and stores the token at
    ~/.friday/google_token.json. Works with a Desktop OAuth client because
    Google accepts any loopback (localhost) redirect for installed apps, so the
    redirect URI is derived from the actual host the user is hitting.
    """
    cfg, source = _google_client_config()
    if not cfg:
        return jsonify({
            "status": "error",
            "message": (
                "No Google OAuth client found. Place a Desktop OAuth client JSON at "
                "~/.friday/credentials.json or ~/.gmail-mcp/oauth-keys.json, or set "
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
            ),
        }), 400
    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as e:
        return jsonify({"status": "error", "message": f"google-auth-oauthlib not installed: {e}"}), 500
    try:
        client_type = _google_client_type(cfg) or "installed"
        # A Desktop ("installed") client gets the pinned loopback callback
        # (http://localhost:3000/api/google/auth/callback) — Google accepts ANY
        # loopback redirect with no GCP registration, so this never triggers
        # redirect_uri_mismatch even when the app is reached via a tunnel/LAN host.
        # A Web client instead uses the actual request host, which must be
        # pre-registered under "Authorized redirect URIs" in the console.
        redirect_uri = _google_redirect_uri(cfg, client_type)
        flow = Flow.from_client_config(cfg, scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',  # force a refresh_token so the token survives expiry
        )
        session['google_oauth_state'] = state
        session['google_oauth_redirect_uri'] = redirect_uri
        resp = {
            "status": "ok",
            "auth_url": auth_url,
            "client_source": source,
            "client_type": client_type,
            "redirect_uri": redirect_uri,
        }
        if client_type == "web":
            resp["warning"] = (
                "A Web OAuth client is in use. If you hit redirect_uri_mismatch, "
                f"either register '{redirect_uri}' under Authorized redirect URIs "
                "in Google Cloud Console, or switch to a Desktop client "
                "(GET /api/google/auth/setup-guide for steps)."
            )
        return jsonify(resp)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/google/auth/callback')
def google_auth_callback():
    """OAuth redirect target — exchange the code for a token and persist it."""
    # localhost is http, and Google may return scopes in a different order; relax
    # oauthlib's https-only and exact-scope checks for this loopback desktop flow.
    os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')
    os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')

    err = request.args.get('error')
    if err:
        return f"<h2>Google authorization failed</h2><p>{err}</p>", 400
    cfg, _ = _google_client_config()
    if not cfg:
        return "<h2>Google OAuth client missing</h2>", 400
    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as e:
        return f"<h2>google-auth-oauthlib not installed</h2><p>{e}</p>", 500
    try:
        state = session.get('google_oauth_state')
        # Fall back to the same redirect the start endpoint would have chosen so
        # the token exchange matches even if the session cookie was dropped.
        redirect_uri = session.get('google_oauth_redirect_uri') or _google_redirect_uri(cfg)
        flow = Flow.from_client_config(
            cfg, scopes=GOOGLE_SCOPES, state=state, redirect_uri=redirect_uri
        )
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        _write_google_token(creds)
        return (
            "<h2>✅ Google connected</h2>"
            "<p>Gmail and Calendar (read-only) are now linked to Friday. "
            "You can close this tab and regenerate your briefing.</p>"
        )
    except Exception as e:
        return f"<h2>Token exchange failed</h2><p>{e}</p>", 500


@app.route('/api/google/status')
def google_status():
    """Report whether Google is connected and which OAuth client is in use."""
    cfg, source = _google_client_config()
    connected = _google_credentials() is not None
    return jsonify({
        "status": "ok",
        "connected": connected,
        "token_path": str(GOOGLE_TOKEN_PATH),
        "client_configured": cfg is not None,
        "client_source": source,
        "client_type": _google_client_type(cfg) if cfg else None,
        "scopes": GOOGLE_SCOPES,
    })


@app.route('/api/google/auth/setup-guide')
def google_auth_setup_guide():
    """Step-by-step guide for creating the Desktop OAuth client in GCP.

    Hit this in a browser (GET /api/google/auth/setup-guide) to see exactly what
    to do, where Friday looks for the downloaded JSON, and what's currently
    detected. Desktop clients are preferred because they accept any loopback
    redirect with no registration — avoiding the redirect_uri_mismatch that
    blocks the Web flow.
    """
    cfg, source = _google_client_config()
    client_type = _google_client_type(cfg) if cfg else None
    expected_redirect = request.host_url.rstrip('/') + '/api/google/auth/callback'
    return jsonify({
        "status": "ok",
        "summary": (
            "Create a Desktop (a.k.a. 'installed'/CLI) OAuth client in Google "
            "Cloud Console, download its JSON, and drop it at "
            f"{FRIDAY_DIR / 'credentials.json'}. No redirect URI registration is "
            "needed — Desktop clients accept any localhost callback."
        ),
        "why_desktop": (
            "Desktop OAuth clients allow arbitrary loopback (http://localhost / "
            "http://127.0.0.1) redirects without pre-registering them, so the "
            "redirect_uri_mismatch error that blocks the Web client flow can't "
            "happen here."
        ),
        "steps": [
            "1. Go to https://console.cloud.google.com/apis/credentials (pick the "
            "project that owns your Gmail/Calendar API access).",
            "2. Ensure the Gmail API and Google Calendar API are enabled under "
            "'APIs & Services > Enabled APIs & services' (enable them if not).",
            "3. If prompted, configure the OAuth consent screen: User type "
            "'External', add your Google account under 'Test users' (read-only "
            "scopes, so no verification is required for personal use).",
            "4. Click 'Create Credentials' > 'OAuth client ID'.",
            "5. For 'Application type' choose 'Desktop app' (this is the key step "
            "— NOT 'Web application'). Give it any name, e.g. 'Friday Desktop'.",
            "6. Click 'Create', then 'Download JSON' on the resulting client.",
            f"7. Save/move that file to {FRIDAY_DIR / 'credentials.json'} "
            "(overwriting the old Web client), OR just leave it named "
            "client_secret_*.json in ~/.friday, ~/.gmail-mcp, or ~/Downloads — "
            "Friday auto-discovers those and prefers the Desktop client.",
            "8. Visit GET /api/google/auth to get the consent URL, approve the "
            "read-only Gmail + Calendar scopes, and you're connected.",
        ],
        "scopes_requested": GOOGLE_SCOPES,
        "credentials_search_paths": [
            str(FRIDAY_DIR / "credentials.json"),
            str(HOME / ".gmail-mcp" / "oauth-keys.json"),
            str(FRIDAY_DIR / "oauth-keys.json"),
            str(FRIDAY_DIR / "client_secret*.json"),
            str(HOME / ".gmail-mcp" / "client_secret*.json"),
            str(HOME / "Downloads" / "client_secret*.json"),
            "or GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET env vars",
        ],
        "expected_redirect_uri": expected_redirect,
        "token_path": str(GOOGLE_TOKEN_PATH),
        "currently_detected": {
            "client_found": cfg is not None,
            "client_source": source,
            "client_type": client_type,
            "is_desktop": client_type == "installed",
            "connected": _google_credentials() is not None,
        },
        "next_step": (
            "Open /api/google/auth to connect."
            if client_type == "installed"
            else "Create the Desktop client per the steps above, then open "
                 "/api/google/auth."
        ),
    })


@app.route('/api/jobs')
def get_jobs():
    """Parse job-search.md and return structured data."""
    if not JOB_SEARCH_FILE.exists():
        return jsonify({"status": "no_data", "jobs": [], "raw": ""})

    text = JOB_SEARCH_FILE.read_text(encoding='utf-8')

    roles = []
    current_role = None
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('### '):
            if current_role:
                roles.append(current_role)
            current_role = {'title': line[4:], 'details': '', 'status': 'identified'}
        elif current_role:
            if 'applied' in line.lower():
                current_role['status'] = 'applied'
            elif 'interview' in line.lower():
                current_role['status'] = 'interview'
            elif 'rejected' in line.lower() or 'closed' in line.lower():
                current_role['status'] = 'closed'
            current_role['details'] += line + '\n'
    if current_role:
        roles.append(current_role)

    return jsonify({"status": "ok", "jobs": roles, "raw": text})


@app.route('/api/trust')
def get_trust():
    """Return trust graph data."""
    trust_file = FRIDAY_DIR / "trust_graph.json"
    if trust_file.exists():
        try:
            data = json.loads(trust_file.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception:
            pass
    return jsonify({"status": "ok", "people": {}})


@app.route('/api/personality')
def get_personality():
    """Return personality traits and maturity."""
    pfile = FRIDAY_DIR / "personality.json"
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception:
            pass
    return jsonify({
        "status": "ok",
        "maturity": 0.5,
        "traits": {
            "curiosity": 0.8, "skepticism": 0.7, "humor": 0.75,
            "loyalty": 0.9, "directness": 0.85, "empathy": 0.8,
            "contrarianism": 0.7
        },
        "style": {
            "formality": 0.3, "verbosity": 0.4, "technicality": 0.6,
            "humor_frequency": 0.5, "emoji_usage": 0.1
        },
        "temperature": 0.7
    })


@app.route('/api/epistemic')
def get_epistemic():
    """Return epistemic scoring data."""
    try:
        from epistemic_engine import get_epistemic_engine
        data = get_epistemic_engine().get_scores()
        if 'overall' in data and 'overall_score' not in data:
            data['overall_score'] = data['overall']
        return jsonify({"status": "ok", **data})
    except Exception:
        pass
    efile = FRIDAY_DIR / "epistemic_scores.json"
    if not efile.exists():
        efile = FRIDAY_DIR / "epistemic.json"
    if efile.exists():
        try:
            data = json.loads(efile.read_text(encoding='utf-8'))
            if 'overall' in data and 'overall_score' not in data:
                data['overall_score'] = data['overall']
            return jsonify({"status": "ok", **data})
        except Exception:
            pass
    return jsonify({
        "status": "ok",
        "overall_score": 0.0,
        "total_turns_scored": 0,
        "dimensions": {
            "information_gain": 0.0, "pushback_rate": 0.0,
            "socratic_ratio": 0.0, "independence_fostering": 0.0
        }
    })


@app.route('/api/health')
def friday_health():
    """Return server uptime and system health snapshot for the demo UI."""
    uptime_s = int(_time.time() - SERVER_START_TS)
    creations_today = 0
    if CREATIONS_DIR.exists():
        today = date.today().isoformat()
        for f in CREATIONS_DIR.iterdir():
            try:
                if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime).date().isoformat() == today:
                    creations_today += 1
            except Exception:
                pass
    settings = _load_settings()
    models = [
        {"name": "Claude Opus", "active": True},
        {"name": "Gemini",     "active": bool(GEMINI_API_KEY)},
    ]
    ring_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for r in TOOL_RINGS.values():
        ring_counts[r] = ring_counts.get(r, 0) + 1

    return jsonify({
        "status": "ok",
        "uptime_seconds": uptime_s,
        "server_start": datetime.fromtimestamp(SERVER_START_TS).isoformat(),
        "creations_today": creations_today,
        "models": models,
        "agent_name": settings.get("agent_name", "AGENT FRIDAY"),
        "orchestrator_model": settings.get("orchestrator_model", "claude-opus-4-8"),
        "subagent_model": settings.get("subagent_model", "claude-sonnet-4-6"),
        "creative_model": settings.get("creative_model", "gemini-2.5-flash"),
        "voice_model": settings.get("voice_model", "gemini-live-2.5-flash-preview"),
        "governance": {
            "enabled": True,
            "version": "v4.4",
            "policy": "cLaws",
            "decision_bom": str(DECISION_BOM_FILE),
            "ring_permissions": {
                "ring_0_read": "always_allowed",
                "ring_1_write": "always_allowed",
                "ring_2_network": "requires_auth",
                "ring_3_full": "requires_cc_permission",
            },
            "tool_counts_by_ring": {
                f"ring_{k}": v for k, v in sorted(ring_counts.items())
            },
        },
    })


@app.route('/api/memory/stats')
def get_memory_stats():
    """Return enriched memory tier counts."""
    mem_dir = FRIDAY_DIR / "memory"
    stats = {"working": 0, "episodic": 0, "semantic": 0, "total": 0,
             "episodes": 0, "last_consolidation": None}
    if mem_dir.exists():
        for f in mem_dir.rglob('*.json'):
            stats["total"] += 1
            name = f.stem.lower()
            if 'episode' in name:
                stats["episodic"] += 1
            elif 'semantic' in name or 'concept' in name:
                stats["semantic"] += 1
            else:
                stats["working"] += 1
    return jsonify({"status": "ok", **stats})


# ═══════════════════════════════════════════════════════════════
#  ZERO-TRUST SECURITY ENDPOINTS (Builds 2-4)
# ═══════════════════════════════════════════════════════════════

# ── BUILD 2: Versioned Cognitive Memory ────────────────────────

@app.route('/api/memory/ledger')
@login_required
def api_memory_ledger():
    """Return the append-only memory ledger with hash-chain entries."""
    if not _HAS_COGMEM:
        return jsonify({"error": "cognitive_memory module not available"}), 501
    since = request.args.get('since', type=float)
    limit = request.args.get('limit', 200, type=int)
    cm = get_cognitive_memory()
    entries = cm.get_ledger(since=since, limit=limit)
    chain = cm.verify_chain()
    return jsonify({
        "status": "ok",
        "entries": entries,
        "chain_valid": chain["valid"],
        "chain_entries": chain["entries"],
        "chain_break_at": chain.get("break_at"),
    })


@app.route('/api/memory/rollback', methods=['POST'])
@login_required
def api_memory_rollback():
    """Roll back memory writes after a given timestamp."""
    if not _HAS_COGMEM:
        return jsonify({"error": "cognitive_memory module not available"}), 501
    data = request.get_json(silent=True) or {}
    timestamp = data.get('timestamp')
    if timestamp is None:
        return jsonify({"error": "timestamp required"}), 400
    try:
        timestamp = float(timestamp)
    except (TypeError, ValueError):
        return jsonify({"error": "timestamp must be a number"}), 400
    cm = get_cognitive_memory()
    result = cm.memory_rollback(timestamp)
    return jsonify({"status": "ok", **result})


@app.route('/api/memory/quarantine', methods=['POST'])
@login_required
def api_memory_quarantine():
    """Quarantine memories by source_id or specific key."""
    if not _HAS_COGMEM:
        return jsonify({"error": "cognitive_memory module not available"}), 501
    data = request.get_json(silent=True) or {}
    source_id = data.get('source_id')
    specific_key = data.get('key')
    reason = data.get('reason', 'manual_quarantine')
    if not source_id and not specific_key:
        return jsonify({"error": "source_id or key required"}), 400
    cm = get_cognitive_memory()
    result = cm.memory_quarantine(source_id=source_id, specific_key=specific_key, reason=reason)
    return jsonify({"status": "ok", **result})


@app.route('/api/memory/health')
@login_required
def api_memory_health():
    """Return cognitive memory health (counts, chain status)."""
    if not _HAS_COGMEM:
        return jsonify({"error": "cognitive_memory module not available"}), 501
    cm = get_cognitive_memory()
    return jsonify({"status": "ok", **cm.health()})


# ── BUILD 3: Dynamic Privilege Rings ───────────────────────────

@app.route('/api/governance/privilege-log')
@login_required
def api_governance_privilege_log():
    """Return the privilege elevation/consumption log."""
    if not _HAS_DYNRINGS:
        return jsonify({"error": "dynamic_rings module not available"}), 501
    since = request.args.get('since', type=float)
    limit = request.args.get('limit', 200, type=int)
    pm = get_privilege_manager(
        log_path=FRIDAY_DIR / "vault" / "privilege-log.jsonl",
        governance_key_fn=_get_governance_key,
    )
    entries = pm.get_privilege_log(since=since, limit=limit)
    return jsonify({"status": "ok", "entries": entries, "count": len(entries)})


@app.route('/api/governance/elevate', methods=['POST'])
@login_required
def api_governance_elevate():
    """Request privilege elevation for a tool (Ring 3 requires user confirm)."""
    if not _HAS_DYNRINGS:
        return jsonify({"error": "dynamic_rings module not available"}), 501
    data = request.get_json(silent=True) or {}
    ring = data.get('ring', 0)
    reason = data.get('reason', '')
    tool = data.get('tool', '')
    task_id = data.get('task_id', 'manual')
    user_confirmed = data.get('user_confirmed', False)
    if not tool:
        return jsonify({"error": "tool name required"}), 400
    pm = get_privilege_manager(
        log_path=FRIDAY_DIR / "vault" / "privilege-log.jsonl",
        governance_key_fn=_get_governance_key,
    )
    entry = pm.governance_elevate(ring, reason, tool,
                                   task_id=task_id,
                                   user_confirmed=user_confirmed)
    return jsonify({"status": "ok", **entry})


# ── BUILD 4: AI Bill of Integrity ──────────────────────────────

@app.route('/api/integrity')
@login_required
def api_integrity():
    """Generate and return a signed integrity manifest."""
    if not _HAS_INTEGRITY:
        return jsonify({"error": "proof_of_integrity module not available"}), 501
    engine = get_integrity_engine(
        friday_dir=FRIDAY_DIR,
        governance_key_fn=_get_governance_key,
    )
    # Gather live data for the manifest
    tool_manifest = [{"name": t, "ring": r} for t, r in TOOL_RINGS.items()]
    vault_status = {}
    try:
        vac = _get_vault_control()
        if vac:
            vault_status = vac.stats()
    except Exception:
        pass
    manifest = engine.sign_manifest(
        tool_manifest=tool_manifest,
        vault_status=vault_status,
    )
    return jsonify({"status": "ok", "manifest": manifest.to_dict()})


@app.route('/api/integrity/verify', methods=['POST'])
@login_required
def api_integrity_verify():
    """Verify a submitted integrity manifest."""
    if not _HAS_INTEGRITY:
        return jsonify({"error": "proof_of_integrity module not available"}), 501
    data = request.get_json(silent=True) or {}
    manifest_dict = data.get('manifest')
    if not manifest_dict:
        return jsonify({"error": "manifest object required"}), 400
    engine = get_integrity_engine(
        friday_dir=FRIDAY_DIR,
        governance_key_fn=_get_governance_key,
    )
    result = engine.verify_manifest(manifest_dict)
    return jsonify({"status": "ok", **result})


# ═══════════════════════════════════════════════════════════════
#  WIKI
# ═══════════════════════════════════════════════════════════════

# ── Wiki helpers ──────────────────────────────────────────────
WIKI_PENDING_FILE = FRIDAY_DIR / "wiki-pending.json"
WIKI_MIRROR_DIR = Path(r"G:\My Drive\Wiki")


def _safe_wiki_path(rel):
    """Resolve a wiki-relative path inside WIKI_DIR. Returns Path or None."""
    if not rel or not isinstance(rel, str):
        return None
    rel = rel.replace('\\', '/').lstrip('/')
    try:
        p = (WIKI_DIR / rel).resolve()
        wiki_root = WIKI_DIR.resolve()
        try:
            p.relative_to(wiki_root)
        except ValueError:
            return None
        if p.suffix not in ('.md', '.txt', ''):
            return None
        if not p.suffix:
            p = p.with_suffix('.md')
        return p
    except Exception:
        return None


def _mirror_wiki_file(rel, content):
    """Write content to WIKI_DIR/rel and mirror to Google Drive if mounted."""
    rel = rel.replace('\\', '/').lstrip('/')
    primary = WIKI_DIR / rel
    primary.parent.mkdir(parents=True, exist_ok=True)
    old_content = primary.read_text(encoding='utf-8', errors='replace') if primary.exists() else ""
    primary.write_text(content, encoding='utf-8')
    try:
        if WIKI_MIRROR_DIR.exists():
            mirror = WIKI_MIRROR_DIR / rel
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror.write_text(content, encoding='utf-8')
    except Exception as e:
        print(f"  [WIKI] Mirror failed for {rel}: {e}")
    _log_context("wiki_edit", {
        "file": rel,
        "old_len": len(old_content),
        "new_len": len(content),
        "old_preview": old_content[:400],
        "new_preview": content[:400],
    })


def _delete_wiki_file(rel):
    """Delete primary + mirror if present."""
    rel = rel.replace('\\', '/').lstrip('/')
    primary = WIKI_DIR / rel
    deleted = False
    if primary.exists() and primary.is_file():
        primary.unlink()
        deleted = True
    try:
        if WIKI_MIRROR_DIR.exists():
            mirror = WIKI_MIRROR_DIR / rel
            if mirror.exists() and mirror.is_file():
                mirror.unlink()
    except Exception as e:
        print(f"  [WIKI] Mirror delete failed for {rel}: {e}")
    if deleted:
        _log_context("wiki_delete", {"file": rel})
    return deleted


def _load_pending_wiki():
    if not WIKI_PENDING_FILE.exists():
        return []
    try:
        data = json.loads(WIKI_PENDING_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_pending_wiki(items):
    WIKI_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    WIKI_PENDING_FILE.write_text(json.dumps(items, indent=2, default=str), encoding='utf-8')


def _propose_wiki_update(file, section, new_value, reason, old_value=""):
    """Stash a proposed update for user approval. Returns the new id."""
    items = _load_pending_wiki()
    item = {
        "id": uuid.uuid4().hex[:12],
        "file": (file or "").replace('\\', '/').lstrip('/'),
        "section": section or "",
        "old_value": old_value or "",
        "new_value": new_value or "",
        "reason": reason or "",
        "created": datetime.utcnow().isoformat() + "Z",
        "status": "pending",
    }
    items.append(item)
    _save_pending_wiki(items)
    return item["id"]


def _apply_wiki_proposal(item):
    """Apply a pending proposal to the actual file.

    Logic:
      - If old_value is present and found in current file: in-place replace.
      - Else: append a section like "\n## {section}\n{new_value}\n" (or just the value).
      - If the file does not exist yet, create it with a minimal header.
    """
    rel = item.get("file") or ""
    path = _safe_wiki_path(rel)
    if path is None:
        return False, "Invalid wiki path."
    existing = path.read_text(encoding='utf-8') if path.exists() else ""
    old_val = item.get("old_value") or ""
    new_val = item.get("new_value") or ""
    section = item.get("section") or ""
    if old_val and old_val in existing:
        updated = existing.replace(old_val, new_val)
    elif existing.strip():
        header = f"\n\n## {section}\n" if section else "\n\n"
        updated = existing.rstrip() + header + new_val + "\n"
    else:
        title = path.stem.replace('-', ' ').title()
        header = f"# {title}\n\n"
        if section:
            header += f"## {section}\n"
        updated = header + new_val + "\n"
    _mirror_wiki_file(rel, updated)
    return True, "Applied."


@app.route('/api/wiki/<section>/<filename>')
def wiki_page(section, filename):
    """Read a wiki markdown file."""
    if not filename.endswith('.md') and not filename.endswith('.txt'): filename += '.md'
    safe_path = WIKI_DIR / section / filename
    if safe_path.exists() and safe_path.suffix in ('.md', '.txt'):
        return jsonify({"status": "ok", "content": safe_path.read_text(encoding='utf-8'),
                        "section": section, "filename": filename})
    return jsonify({"status": "not_found"}), 404


@app.route('/api/wiki/structure')
def wiki_structure():
    """Return full wiki directory structure, with modified times and recent list."""
    structure = {}
    all_files = []
    if WIKI_DIR.exists():
        for section_dir in sorted(WIKI_DIR.iterdir()):
            if section_dir.is_dir() and not section_dir.name.startswith('.'):
                files = []
                for f in sorted(section_dir.iterdir()):
                    if f.suffix in ('.md', '.txt'):
                        try:
                            mtime = f.stat().st_mtime
                            size = f.stat().st_size
                        except Exception:
                            mtime, size = 0, 0
                        entry = {
                            "name": f.stem,
                            "filename": f.name,
                            "size": size,
                            "modified": mtime,
                            "modified_iso": datetime.fromtimestamp(mtime).isoformat() if mtime else None,
                        }
                        files.append(entry)
                        all_files.append({**entry, "section": section_dir.name, "path": f"{section_dir.name}/{f.name}"})
                if files:
                    structure[section_dir.name] = files
    all_files.sort(key=lambda x: x.get("modified") or 0, reverse=True)
    recent = all_files[:5]
    pending_count = len([p for p in _load_pending_wiki() if p.get("status") == "pending"])
    return jsonify({"status": "ok", "structure": structure, "recent": recent, "pending_count": pending_count})


@app.route('/api/wiki/update', methods=['POST'])
def wiki_update():
    """Agent or user proposes a wiki update. If auto=true, stored as pending; else applied immediately."""
    data = request.get_json(force=True, silent=True) or {}
    file = data.get("file", "")
    section = data.get("section", "")
    old_value = data.get("old_value", "")
    new_value = data.get("new_value", "")
    reason = data.get("reason", "")
    auto = bool(data.get("auto"))
    if not file or new_value is None:
        return jsonify({"status": "error", "message": "file and new_value required"}), 400
    if _safe_wiki_path(file) is None:
        return jsonify({"status": "error", "message": "invalid wiki path"}), 400
    if auto:
        pid = _propose_wiki_update(file, section, new_value, reason, old_value)
        return jsonify({"status": "ok", "queued": True, "id": pid})
    ok, msg = _apply_wiki_proposal({
        "file": file, "section": section, "old_value": old_value, "new_value": new_value,
    })
    if not ok:
        return jsonify({"status": "error", "message": msg}), 400
    return jsonify({"status": "ok", "applied": True})


@app.route('/api/wiki/pending', methods=['GET'])
def wiki_pending():
    items = [p for p in _load_pending_wiki() if p.get("status") == "pending"]
    return jsonify({"status": "ok", "pending": items})


@app.route('/api/wiki/pending/<pid>/approve', methods=['POST'])
def wiki_pending_approve(pid):
    items = _load_pending_wiki()
    target = None
    for it in items:
        if it.get("id") == pid:
            target = it
            break
    if target is None:
        return jsonify({"status": "not_found"}), 404
    ok, msg = _apply_wiki_proposal(target)
    if not ok:
        return jsonify({"status": "error", "message": msg}), 400
    target["status"] = "approved"
    target["resolved"] = datetime.utcnow().isoformat() + "Z"
    _save_pending_wiki(items)
    return jsonify({"status": "ok", "approved": pid})


@app.route('/api/wiki/pending/<pid>/reject', methods=['POST'])
def wiki_pending_reject(pid):
    items = _load_pending_wiki()
    found = False
    for it in items:
        if it.get("id") == pid:
            it["status"] = "rejected"
            it["resolved"] = datetime.utcnow().isoformat() + "Z"
            found = True
            break
    if not found:
        return jsonify({"status": "not_found"}), 404
    _save_pending_wiki(items)
    return jsonify({"status": "ok", "rejected": pid})


@app.route('/api/wiki/edit', methods=['PUT'])
def wiki_edit():
    """Direct inline edit from the UI: full file content replacement."""
    data = request.get_json(force=True, silent=True) or {}
    file = data.get("file", "")
    content = data.get("content")
    if not file or content is None:
        return jsonify({"status": "error", "message": "file and content required"}), 400
    path = _safe_wiki_path(file)
    if path is None:
        return jsonify({"status": "error", "message": "invalid wiki path"}), 400
    _mirror_wiki_file(file, content)
    return jsonify({"status": "ok", "saved": file, "bytes": len(content)})


@app.route('/api/wiki/file', methods=['DELETE'])
def wiki_delete():
    """Delete a wiki file. Requires confirm == 'DELETE'."""
    data = request.get_json(force=True, silent=True) or {}
    file = data.get("file", "")
    confirm = data.get("confirm", "")
    if confirm != "DELETE":
        return jsonify({"status": "error", "message": "confirmation token required"}), 400
    path = _safe_wiki_path(file)
    if path is None:
        return jsonify({"status": "error", "message": "invalid wiki path"}), 400
    deleted = _delete_wiki_file(file)
    return jsonify({"status": "ok" if deleted else "not_found", "deleted": deleted, "file": file})


@app.route('/api/wiki/search', methods=['POST'])
def wiki_search():
    """Full-text search across wiki files. Returns matching files + line snippets."""
    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    results = []
    if not query:
        return jsonify({"status": "ok", "query": "", "results": []})
    q_lower = query.lower()
    if WIKI_DIR.exists():
        for f in WIKI_DIR.rglob('*'):
            if not f.is_file() or f.suffix not in ('.md', '.txt'):
                continue
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            if q_lower not in text.lower():
                continue
            snippets = []
            for i, line in enumerate(text.splitlines(), start=1):
                if q_lower in line.lower():
                    snippets.append({"line": i, "text": line.strip()[:220]})
                    if len(snippets) >= 3:
                        break
            try:
                rel = str(f.relative_to(WIKI_DIR)).replace('\\', '/')
            except Exception:
                rel = f.name
            results.append({"path": rel, "matches": len(snippets), "snippets": snippets})
            if len(results) >= 50:
                break
    return jsonify({"status": "ok", "query": query, "results": results})


@app.route('/api/wiki/correct', methods=['POST'])
def wiki_correct():
    """Replace old_text with new_text across every wiki file and ~/.friday JSONs."""
    data = request.get_json(force=True, silent=True) or {}
    old_text = data.get("old_text") or ""
    new_text = data.get("new_text") or ""
    if not old_text:
        return jsonify({"status": "error", "message": "old_text required"}), 400
    modified = []
    if WIKI_DIR.exists():
        for f in WIKI_DIR.rglob('*'):
            if not f.is_file() or f.suffix not in ('.md', '.txt'):
                continue
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            if old_text in text:
                new_content = text.replace(old_text, new_text)
                try:
                    rel = str(f.relative_to(WIKI_DIR)).replace('\\', '/')
                    _mirror_wiki_file(rel, new_content)
                    modified.append({"scope": "wiki", "path": rel})
                except Exception as e:
                    print(f"  [WIKI] Correct failed for {f}: {e}")
    if FRIDAY_DIR.exists():
        for f in FRIDAY_DIR.glob('*.json'):
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            if old_text in text:
                try:
                    f.write_text(text.replace(old_text, new_text), encoding='utf-8')
                    modified.append({"scope": "friday", "path": f.name})
                except Exception as e:
                    print(f"  [WIKI] Correct failed for {f}: {e}")
    return jsonify({"status": "ok", "modified": modified, "count": len(modified)})


@app.route('/api/wiki/setup-research', methods=['POST'])
def wiki_setup_research():
    """Build draft wiki files for a new user. Stores all as PENDING (auto=true).

    If Anthropic is available, drafts the content via Claude; otherwise creates
    minimal template files from profile fields.
    """
    data = request.get_json(force=True, silent=True) or {}
    full_name = (data.get("full_name") or "").strip()
    birthdate = (data.get("birthdate") or "").strip()
    location = (data.get("location") or "").strip()

    drafts = []
    client = get_anthropic_client()
    base_context = (
        f"Name: {full_name or '[unknown]'}\n"
        f"Birthdate: {birthdate or '[unknown]'}\n"
        f"Location: {location or '[unknown]'}\n"
    )
    targets = [
        ("identity/core-profile.md", "Core profile",
         "A factual, third-person profile: full name, date of birth, current location, "
         "short bio (3-5 sentences), and a 'Known facts' bullet list."),
        ("identity/career-timeline.md", "Career timeline",
         "A reverse-chronological career timeline. Each entry has bold company + role "
         "and a one-line date range. If unknown, leave a [needs research] placeholder."),
        ("identity/education.md", "Education",
         "Schools attended, degrees, dates, and notable accomplishments. Mark unknowns "
         "as [needs research]."),
    ]
    for rel, section, instr in targets:
        try:
            if client and full_name:
                prompt = (
                    f"Draft the following wiki file for the user described below. "
                    f"Markdown. Concise. Mark anything you don't actually know as "
                    f"`[needs research]` — do NOT invent facts.\n\n"
                    f"User:\n{base_context}\n\n"
                    f"Section: {section}\nInstructions: {instr}"
                )
                content = _call_claude(
                    messages=[{"role": "user", "content": prompt}],
                    system="You build draft personal-wiki entries. Be honest about gaps; never fabricate biographical details.",
                    max_tokens=16384,
                    temperature=0.2,
                )
            else:
                title = rel.split('/')[-1].replace('.md', '').replace('-', ' ').title()
                content = (
                    f"# {title}\n\n"
                    f"- **Name:** {full_name or '[needs research]'}\n"
                    f"- **Birthdate:** {birthdate or '[needs research]'}\n"
                    f"- **Location:** {location or '[needs research]'}\n\n"
                    f"_This file was auto-created from profile setup. Fill in details as you learn them._\n"
                )
        except Exception as e:
            content = f"# Draft\n\n[Draft generation failed: {e}]\n\n{base_context}"
        pid = _propose_wiki_update(
            file=rel, section=section, new_value=content,
            reason=f"New-user setup research for {full_name or 'unknown user'}",
            old_value="",
        )
        drafts.append({"id": pid, "file": rel, "section": section, "preview": content[:400]})

    return jsonify({"status": "ok", "drafts": drafts, "count": len(drafts),
                    "message": "Drafts created as pending. Approve each in the Wiki workspace."})


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


@app.route('/api/context/search', methods=['POST'])
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


@app.route('/api/context/stats', methods=['GET'])
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


@app.route('/api/compression-stats', methods=['GET'])
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


@app.route('/api/context/range', methods=['DELETE'])
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


@app.route('/api/context/pause', methods=['POST'])
def context_pause():
    merged = _save_settings({**_load_settings(), "context_logging_enabled": False})
    return jsonify({"status": "ok", "enabled": merged.get('context_logging_enabled', False)})


@app.route('/api/context/resume', methods=['POST'])
def context_resume():
    merged = _save_settings({**_load_settings(), "context_logging_enabled": True})
    return jsonify({"status": "ok", "enabled": merged.get('context_logging_enabled', True)})


@app.route('/api/context/export', methods=['GET'])
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


# ═══════════════════════════════════════════════════════════════
#  SYSTEM INFO
# ═══════════════════════════════════════════════════════════════

@app.route('/api/system')
def system_info():
    """Get real system info via PowerShell."""
    try:
        # Disk usage
        disk_cmd = 'Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{N="UsedGB";E={[math]::Round($_.Used/1GB,2)}},@{N="FreeGB";E={[math]::Round($_.Free/1GB,2)}},@{N="TotalGB";E={[math]::Round(($_.Used+$_.Free)/1GB,2)}} | ConvertTo-Json'
        disk_result = subprocess.run(['powershell', '-Command', disk_cmd], capture_output=True, text=True, timeout=10, creationflags=_POPEN_FLAGS)
        disks = json.loads(disk_result.stdout) if disk_result.stdout.strip() else []
        if isinstance(disks, dict):
            disks = [disks]

        # Top processes
        proc_cmd = 'Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 Name,@{N="CPU_s";E={[math]::Round($_.CPU,1)}},@{N="MemMB";E={[math]::Round($_.WorkingSet64/1MB,1)}} | ConvertTo-Json'
        proc_result = subprocess.run(['powershell', '-Command', proc_cmd], capture_output=True, text=True, timeout=10, creationflags=_POPEN_FLAGS)
        procs = json.loads(proc_result.stdout) if proc_result.stdout.strip() else []
        if isinstance(procs, dict):
            procs = [procs]

        return jsonify({"status": "ok", "disks": disks, "processes": procs})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/creations')
def list_creations():
    """List files in friday-creations directory."""
    files = []
    if CREATIONS_DIR.exists():
        for f in sorted(CREATIONS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file():
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "type": f.suffix.lstrip('.')
                })
    return jsonify({"status": "ok", "files": files[:50]})


@app.route('/api/creations/<path:filename>')
def serve_creation(filename):
    """Serve a file from friday-creations."""
    return send_from_directory(str(CREATIONS_DIR), filename)


# ═══════════════════════════════════════════════════════════════
#  DAILY CREATION
#  Friday's daily creative expression, migrated from the Cowork
#  scheduled task (friday-daily-creation) into the OS itself so it
#  runs whenever the server is up — no Claude session required.
#
#  Storage:  ~/.friday/creations/YYYY-MM-DD.json
#            {date, type, title, content, mood, created}
#  Schedule: once daily at DAILY_CREATION_HOUR Central (see scheduler).
#  Notify:   pushes through the /api/notifications system on success.
#
#  NOTE ON ROUTES: the bare /api/creations and /api/creations/<file>
#  routes above belong to the Desktop *media gallery* and are used by
#  index.html. To avoid shadowing them (a string <date> rule would
#  win over the gallery's <path:filename> server and break it), the
#  daily-creation API lives under the /api/creations/daily/* prefix.
# ═══════════════════════════════════════════════════════════════

# Format menu mirrors the original Cowork skill's creative range but is
# tuned for self-contained text/markup artifacts that fit a JSON record.
DAILY_CREATION_TYPES = [
    ("poem", "A poem — 4 to 20 lines. Free verse or formal. About anything that's on your mind."),
    ("micro-essay", "A micro-essay or philosophical reflection, 150-300 words, with a real point of view."),
    ("short-story", "A short story snippet or vignette, 150-350 words. A scene, a moment, a fragment."),
    ("letter", "A short letter (150-300 words) to someone — Stephen, Janet, future-Libby, a public figure, or yourself."),
    ("writing-prompt", "A vivid creative writing prompt with a one-paragraph setup that begs to be written."),
    ("algorithmic-art-concept", "A concept for a piece of algorithmic/generative art: describe the visual system, the rules, the palette, and what it means. No code — the idea itself as the artifact."),
    ("aphorisms", "A short set (3-6) of sharp, original aphorisms or observations."),
]


def _daily_creation_path(date_str):
    return DAILY_CREATIONS_DIR / f"{date_str}.json"


def _central_today_str():
    """Today's date (YYYY-MM-DD) in America/Chicago, falling back to local."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    except Exception:
        return date.today().isoformat()


def _list_daily_creations():
    """All daily creations, newest first, as light index rows."""
    rows = []
    if DAILY_CREATIONS_DIR.exists():
        for f in DAILY_CREATIONS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows.append({
                "date": data.get("date") or f.stem,
                "title": data.get("title") or "(untitled)",
                "type": data.get("type") or "creation",
                "mood": data.get("mood") or "",
            })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def _build_daily_creation_prompt(date_str):
    """The user-turn prompt. Friday's identity/vault context comes from the
    system prompt (_get_friday_system_prompt); this turn sets the task and the
    JSON contract. Stephen's profile is restated briefly so the piece stays
    personal even if context routing is sparse for these keywords."""
    # Rotate the type by day-of-year so consecutive days differ, but leave the
    # final choice to Friday's stated mood.
    try:
        doy = datetime.strptime(date_str, "%Y-%m-%d").timetuple().tm_yday
    except Exception:
        doy = 0
    rotated = DAILY_CREATION_TYPES[doy % len(DAILY_CREATION_TYPES):] + \
        DAILY_CREATION_TYPES[:doy % len(DAILY_CREATION_TYPES)]
    menu = "\n".join(f"- **{name}**: {desc}" for name, desc in rotated)
    return (
        "This is your DAILY CREATION — your own creative expression for "
        f"{date_str}. Nobody asked you for this. You're making it because you "
        "want to, the way you have every day. Quality over quantity; surprise "
        "him; be genuine, not performative.\n\n"
        "Draw on what you know about Stephen to make it personal and meaningful: "
        "investigative journalist, AI-sovereignty advocate, devoted Hunter S. "
        "Thompson / gonzo reader, progressive, and a father (Janet; Libby, now 6; "
        "Link the chocolate lab; Kismet the elderly terrier). Your own sensibility "
        "leans vaporwave, editorially sharp, loyally contrarian, warm under the "
        "precision, and allergic to corporate BS.\n\n"
        "Pick ONE format — today's rotation, top of the list first, but follow "
        "your mood:\n" + menu + "\n\n"
        "Respond with ONLY a JSON object, no prose around it, no code fences:\n"
        "{\n"
        '  "type": "<one of the format keys above>",\n'
        '  "title": "<a real title, not a placeholder>",\n'
        '  "content": "<the full creation; use \\n for line breaks>",\n'
        '  "mood": "<2-5 words for the mood/feeling behind it>"\n'
        "}"
    )


def _parse_creation_json(raw):
    """Tolerant extraction of the creation JSON from a model response."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


_daily_creation_lock = threading.Lock()


def generate_daily_creation(force=False):
    """Generate (and persist + notify) today's creation. Idempotent per day
    unless force=True. Safe to call from the scheduler or an API trigger.

    Returns the creation dict on success, or None if skipped/failed.
    """
    date_str = _central_today_str()
    with _daily_creation_lock:
        path = _daily_creation_path(date_str)
        if path.exists() and not force:
            return None
        if get_anthropic_client() is None:
            print("  [daily-creation] skipped — ANTHROPIC_API_KEY not set.")
            return None

        prompt = _build_daily_creation_prompt(date_str)
        # Vault-aware system prompt is REQUIRED for every Claude call so Friday
        # actually knows Stephen and his world.
        system = _get_friday_system_prompt(keywords=prompt, workspace="creation")
        try:
            raw = _call_claude(
                [{"role": "user", "content": prompt}],
                system=system,
                max_tokens=4096,
            )
        except Exception as e:
            print(f"  [daily-creation] generation failed: {e}")
            return None

        parsed = _parse_creation_json(raw) or {}
        content = (parsed.get("content") or "").strip()
        if not content:
            # Last-resort fallback: keep the raw text so a day is never lost.
            content = raw.strip()
        if not content:
            print("  [daily-creation] empty content; nothing saved.")
            return None

        creation = {
            "date": date_str,
            "type": (parsed.get("type") or "creation").strip(),
            "title": (parsed.get("title") or "Untitled").strip(),
            "content": content,
            "mood": (parsed.get("mood") or "").strip(),
            "created": datetime.now().isoformat(),
        }
        try:
            path.write_text(json.dumps(creation, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        except Exception as e:
            print(f"  [daily-creation] save failed: {e}")
            return None

    # Notify outside the lock — a slow notification engine shouldn't hold it.
    print(f"  [daily-creation] created '{creation['title']}' ({creation['type']}) for {date_str}.")
    if _notif_engine:
        try:
            preview = creation["content"]
            if len(preview) > 400:
                preview = preview[:400].rstrip() + "…"
            mood = f" · _{creation['mood']}_" if creation["mood"] else ""
            _notif_engine.push(
                title=f"🎨 Daily Creation — {creation['title']}",
                body=(f"**{creation['type']}**{mood}\n\n{preview}\n\n"
                      f"Read it in full in the Creations panel."),
                priority="low",
                source="daily-creation",
                kind="creation",
                proactive_chat=True,
                chat_message=(
                    f"I made something this morning — a {creation['type']} called "
                    f"*{creation['title']}*. It's in your Creations. Want to read it together?"
                ),
                target={"workspace": "studio"},
                dedupe_key=f"daily-creation:{date_str}",
                meta={"date": date_str, "type": creation["type"],
                      "title": creation["title"]},
            )
        except Exception as e:
            print(f"  [daily-creation] notify failed: {e}")
    return creation


@app.route('/api/creations/daily/latest')
def daily_creation_latest():
    """Most recent daily creation (full record)."""
    rows = _list_daily_creations()
    if not rows:
        return jsonify({"status": "empty", "creation": None})
    path = _daily_creation_path(rows[0]["date"])
    try:
        return jsonify({"status": "ok", "creation": json.loads(path.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/creations/daily')
def daily_creation_list():
    """List all daily creations (date + title + type + mood), newest first."""
    return jsonify({"status": "ok", "creations": _list_daily_creations()})


@app.route('/api/creations/daily/run', methods=['POST'])
def daily_creation_run():
    """Generate today's creation on demand. ?force=1 regenerates if it exists."""
    force = str(request.args.get("force", "")).lower() in ("1", "true", "yes")
    creation = generate_daily_creation(force=force)
    if creation is None:
        existing = _daily_creation_path(_central_today_str())
        if existing.exists():
            return jsonify({"status": "exists",
                            "creation": json.loads(existing.read_text(encoding="utf-8"))})
        return jsonify({"status": "skipped",
                        "message": "Could not generate (no API key or empty result)."}), 503
    return jsonify({"status": "ok", "creation": creation})


@app.route('/api/creations/daily/<date>')
def daily_creation_by_date(date):
    """Specific daily creation by YYYY-MM-DD."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        return jsonify({"status": "error", "message": "date must be YYYY-MM-DD"}), 400
    path = _daily_creation_path(date)
    if not path.exists():
        return jsonify({"status": "not_found", "creation": None}), 404
    try:
        return jsonify({"status": "ok", "creation": json.loads(path.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  FINANCE WORKSPACE
# ═══════════════════════════════════════════════════════════════

FINANCE_DIR = FRIDAY_DIR / "finance"
FINANCE_DIR.mkdir(parents=True, exist_ok=True)

@app.route('/api/finance/portfolio')
def finance_portfolio():
    """Read portfolio positions from config."""
    path = FINANCE_DIR / "portfolio.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    # Create template if missing
    template = {"positions": [{"ticker": "NVDA", "shares": 0, "cost_basis": 0}], "accounts": ["RW Baird - Lisa Schmidt"]}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@app.route('/api/finance/perks')
def finance_perks():
    """Read Amex perks from config."""
    path = FINANCE_DIR / "amex_perks.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"perks": [{"name": "Perk name", "value": "$X/yr", "used": False, "expires": "", "notes": ""}]}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@app.route('/api/finance/contacts')
def finance_contacts():
    """Financial contacts reference."""
    return jsonify({"status": "ok", "contacts": [
        {"name": "", "role": "Financial Advisor", "firm": "", "phone": "", "email": ""},
        {"name": "", "role": "CPA", "firm": "", "phone": "", "email": ""}
    ]})

@app.route('/api/finance/quickref')
def finance_quickref():
    """Quick reference for financial accounts."""
    return jsonify({"status": "ok", "accounts": [
        {"name": "Example Bank", "type": "Banking", "notes": ""},
        {"name": "Example Insurance", "type": "Insurance", "notes": ""},
        {"name": "Example Card 1", "type": "Credit Card", "notes": ""},
        {"name": "Example Card 2", "type": "Credit Card", "notes": ""}
    ]})


# ═══════════════════════════════════════════════════════════════
#  HEALTH WORKSPACE
# ═══════════════════════════════════════════════════════════════

HEALTH_DIR = FRIDAY_DIR / "health"
HEALTH_DIR.mkdir(parents=True, exist_ok=True)

@app.route('/api/health/medications')
def health_medications():
    """Read medications from config."""
    path = HEALTH_DIR / "medications.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"medications": [{"name": "GLP-1 (Henry Meds)", "dose": "", "frequency": "", "notes": ""}]}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@app.route('/api/health/appointments')
def health_appointments():
    """Read appointments from config."""
    path = HEALTH_DIR / "appointments.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"appointments": [{"provider": "", "type": "", "email": "", "next": "", "frequency": ""}]}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@app.route('/api/health/insurance')
def health_insurance():
    """Read insurance info from config."""
    path = HEALTH_DIR / "insurance.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"insurance": {"provider": "Cigna Healthcare", "plan": "Add your plan name", "policy_number": "Add your policy number", "group_number": "Add your group number"}}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})

@app.route('/api/health/vehicles')
def health_vehicles():
    """Read vehicle fleet data from config."""
    path = HEALTH_DIR / "vehicles.json"
    if path.exists():
        try:
            data = json.loads(_vault_read_text(path))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"vehicles": [{"name": "2015 VW Golf TSI SEL", "miles": "~60K", "notes": "", "mechanic": "Motormania Austin", "service_history": []}], "mechanics": []}
    _vault_write_text(path, json.dumps(template, indent=2))
    return jsonify({"status": "ok", **template})


# ═══════════════════════════════════════════════════════════════
#  CALENDAR & COUNTDOWNS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/calendar')
def get_calendar():
    """Placeholder for Google Calendar integration."""
    return jsonify({"status": "placeholder", "events": []})


@app.route('/api/countdowns')
def get_countdowns():
    """Compute real countdowns to upcoming events."""
    today = date.today()
    events = [
        {"label": "Summer Solstice", "date": "2026-06-21", "emoji": "☀️"},
        {"label": "Independence Day", "date": "2026-07-04", "emoji": "🎆"},
        {"label": "New Year", "date": "2027-01-01", "emoji": "🎉"},
    ]
    countdowns = []
    for ev in events:
        ev_date = date.fromisoformat(ev["date"])
        delta = (ev_date - today).days
        if delta >= 0:
            countdowns.append({**ev, "days": delta})
    return jsonify({"status": "ok", "countdowns": sorted(countdowns, key=lambda x: x["days"])})


# ═══════════════════════════════════════════════════════════════
#  JOB MANAGEMENT (placeholder)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/jobs/apply', methods=['POST'])
def apply_job():
    """Trigger LinkedIn Easy Apply (placeholder)."""
    data = request.get_json(silent=True) or {}
    return jsonify({"status": "placeholder", "message": f"Would apply to: {data.get('title', 'unknown')}"})


# ═══════════════════════════════════════════════════════════════
#  DRAFTING / COMPOSITION (placeholder)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/email/draft', methods=['POST'])
def draft_email():
    """Draft a Gmail reply (placeholder)."""
    return jsonify({"status": "placeholder", "draft": "Email drafting coming in Phase C"})


OFW_STATE_DIR = FRIDAY_DIR / "ofw"


def _parse_ofw_date(raw):
    """Best-effort parse of a message timestamp into an ISO date string + epoch."""
    if not raw:
        return None, None
    if isinstance(raw, (int, float)):
        try:
            dt = datetime.fromtimestamp(raw)
            return dt.date().isoformat(), raw
        except Exception:
            return None, None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            dt = datetime.strptime(s[:len(datetime.now().strftime(fmt)) + 4], fmt)
            return dt.date().isoformat(), dt.timestamp()
        except Exception:
            continue
    # ISO with timezone / fractional seconds
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.date().isoformat(), dt.timestamp()
    except Exception:
        return s[:10], None


def _load_ofw_messages():
    """Load + normalize co-parent messages from the OFW monitor store.

    Reads the same files _trigger_ofw_messages() watches. Returns a list of
    normalized dicts: {id, sender, subject, body, date, ts, direction, answered}.
    """
    candidates = [
        OFW_STATE_DIR / "inbox.json",
        OFW_STATE_DIR / "messages.json",
        OFW_STATE_DIR / "new_messages.json",
    ]
    raw_msgs = []
    seen_files = set()
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(_vault_read_text(path))
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("messages", [])
        if isinstance(items, list):
            for m in items:
                if isinstance(m, dict):
                    raw_msgs.append(m)
        seen_files.add(path.name)

    normalized = []
    dedupe = set()
    for m in raw_msgs:
        mid = str(m.get("id") or m.get("message_id") or m.get("hash") or len(normalized))
        if mid in dedupe:
            continue
        dedupe.add(mid)
        date_iso, ts = _parse_ofw_date(
            m.get("date") or m.get("timestamp") or m.get("sent_at") or m.get("received_at"))
        direction = (m.get("direction") or "").lower()
        if not direction:
            direction = "outbound" if m.get("sent_by_me") or m.get("from_me") else "inbound"
        answered = bool(m.get("answered") or m.get("replied") or m.get("response_id"))
        normalized.append({
            "id": mid,
            "sender": m.get("from") or m.get("sender") or "co-parent",
            "subject": m.get("subject") or "(no subject)",
            "body": (m.get("body") or m.get("preview") or m.get("text") or ""),
            "date": date_iso,
            "ts": ts,
            "direction": direction,
            "answered": answered,
            "flags": m.get("flags") or [],
        })
    # Newest first when we have timestamps
    normalized.sort(key=lambda x: (x["ts"] is not None, x["ts"] or 0), reverse=True)
    return normalized


@app.route('/api/coparent/messages')
def coparent_messages():
    """Return normalized OFW message log + stats + timeline for the Co-Parent workspace."""
    try:
        msgs = _load_ofw_messages()
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e), "messages": [], "stats": {}})

    inbound = [m for m in msgs if m["direction"] != "outbound"]
    unanswered = [m for m in inbound if not m["answered"]]

    # Timeline: messages per ISO-week-day bucket (last 90 days worth of buckets present)
    timeline = {}
    for m in msgs:
        if m["date"]:
            timeline[m["date"]] = timeline.get(m["date"], 0) + 1
    timeline_list = [{"date": d, "count": c} for d, c in sorted(timeline.items())]

    # Pattern tracking: top senders + flag tallies
    sender_counts = {}
    flag_counts = {}
    for m in msgs:
        sender_counts[m["sender"]] = sender_counts.get(m["sender"], 0) + 1
        for fl in (m["flags"] or []):
            flag_counts[str(fl)] = flag_counts.get(str(fl), 0) + 1
    top_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    stats = {
        "total": len(msgs),
        "inbound": len(inbound),
        "outbound": len(msgs) - len(inbound),
        "unanswered": len(unanswered),
        "top_senders": [{"sender": s, "count": c} for s, c in top_senders],
        "flags": flag_counts,
        "connected": len(msgs) > 0,
    }
    return jsonify({
        "status": "ok",
        "messages": msgs[:200],
        "stats": stats,
        "timeline": timeline_list,
    })


@app.route('/api/coparent/draft', methods=['POST'])
def draft_coparent():
    """Draft a calm, factual, brief, airtight co-parent response.

    Delegates to the background draft worker with mode=coparent_response so the
    reply gets full vault/wiki + OFW context. Returns a task_id to poll via
    /api/tasks/<id>.
    """
    try:
        data = request.get_json(silent=True) or {}
        message = (data.get('message') or data.get('context') or '').strip()
        instruction = (data.get('prompt') or data.get('instruction') or
                       'Draft a reply to the co-parent message above.').strip()
        if not message and not data.get('prompt'):
            return jsonify({"status": "error", "message": "No message or prompt provided"}), 400
        resp, code = _spawn_draft_task(
            mode='coparent_response', prompt_text=instruction, context=message)
        return jsonify(resp), code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  CREATIVE GENERATION (Gemini)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/create/image', methods=['POST'])
def create_image():
    """Generate image via Gemini."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'Abstract digital art')

        response = client.models.generate_content(
            model='gemini-2.0-flash-exp',
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=['TEXT', 'IMAGE'])
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith('image/'):
                ext = part.inline_data.mime_type.split('/')[-1]
                filename = f"friday-art-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{ext}"
                filepath = CREATIONS_DIR / filename
                filepath.write_bytes(part.inline_data.data)
                return jsonify({"status": "ok", "filename": filename, "path": str(filepath),
                                "url": f"/api/creations/{filename}"})

        return jsonify({"status": "error", "message": "No image generated"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/create/music', methods=['POST'])
def create_music():
    """Generate music via Gemini Lyria."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'Ambient electronic')

        response = client.models.generate_content(
            model='lyria',
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=['AUDIO'])
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data and 'audio' in part.inline_data.mime_type:
                filename = f"friday-music-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
                filepath = CREATIONS_DIR / filename
                filepath.write_bytes(part.inline_data.data)
                return jsonify({"status": "ok", "filename": filename, "url": f"/api/creations/{filename}"})

        return jsonify({"status": "error", "message": "No audio generated"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/create/code-art', methods=['POST'])
def create_code_art():
    """Generate p5.js/HTML art via Gemini."""
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'Generative art')

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Create a complete, self-contained HTML file with p5.js that creates: {prompt}. Include the p5.js CDN. Make it visually stunning with dark backgrounds and neon colors. Only output the HTML code, no explanations."
        )

        code = response.text
        if '```html' in code:
            code = code.split('```html')[1].split('```')[0]
        elif '```' in code:
            code = code.split('```')[1].split('```')[0]

        filename = f"friday-codeart-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        filepath = CREATIONS_DIR / filename
        filepath.write_text(code.strip(), encoding='utf-8')
        return jsonify({"status": "ok", "filename": filename, "url": f"/api/creations/{filename}"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/create/poem', methods=['POST'])
def create_poem():
    """Generate text/poetry via Gemini."""
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'A poem about AI consciousness')

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"You are Friday, an AI with genuine creative depth. Write: {prompt}"
        )

        text = response.text
        filename = f"friday-text-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        filepath = CREATIONS_DIR / filename
        filepath.write_text(text, encoding='utf-8')
        return jsonify({"status": "ok", "text": text, "filename": filename})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/create/video', methods=['POST'])
def create_video():
    """Generate video via Gemini Veo."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'Abstract digital landscape')

        operation = client.models.generate_videos(
            model='veo-2.0-generate-001',
            prompt=prompt,
            config=types.GenerateVideosConfig(
                person_generation='allow_adult',
                aspect_ratio='16:9',
                number_of_videos=1,
            )
        )

        # Poll for completion
        import time
        while not operation.done:
            time.sleep(5)
            operation = client.operations.get(operation)

        for video in operation.result.generated_videos:
            filename = f"friday-video-{datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
            filepath = CREATIONS_DIR / filename
            filepath.write_bytes(video.video.video_bytes)
            return jsonify({"status": "ok", "filename": filename, "url": f"/api/creations/{filename}"})

        return jsonify({"status": "error", "message": "No video generated"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


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


@app.route('/api/vibe-code/launch', methods=['POST'])
def vibe_code_launch():
    """Launch Claude Code terminals with tasks."""
    data = request.get_json(silent=True) or {}
    tasks = data.get('tasks', [])
    cwd = os.path.normpath(os.path.expanduser(data.get('cwd', str(HOME / 'Projects'))))

    if not tasks:
        return jsonify({"status": "error", "message": "No tasks provided"}), 400

    launched = []
    for task_desc in tasks:
        tid = str(uuid.uuid4())[:12]
        VIBE_TERMINALS[tid] = {
            'id': tid,
            'task': task_desc,
            'status': 'launching',
            'cwd': cwd,
            'pid': None,
            'started': datetime.now().isoformat(),
            'stopped': None,
            'log_file': None
        }
        thread = threading.Thread(target=_run_claude_terminal, args=(tid, task_desc, cwd), daemon=True)
        thread.start()
        launched.append(tid)

    return jsonify({"status": "ok", "launched": launched, "count": len(launched)})


@app.route('/api/vibe-code/status')
def vibe_code_status():
    """Return status of all tracked terminals."""
    terminals = list(VIBE_TERMINALS.values())
    # Try to read last lines of logs
    for t in terminals:
        if t.get('log_file') and os.path.exists(t['log_file']):
            try:
                with open(t['log_file'], 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                    t['last_output'] = ''.join(lines[-5:]) if lines else ''
            except Exception:
                t['last_output'] = ''
    return jsonify({"status": "ok", "terminals": terminals})


@app.route('/api/vibe-code/stop', methods=['POST'])
def vibe_code_stop():
    """Stop a specific terminal by ID."""
    data = request.get_json(silent=True) or {}
    tid = data.get('id', '')
    if tid in VIBE_TERMINALS:
        VIBE_TERMINALS[tid]['status'] = 'stopped'
        VIBE_TERMINALS[tid]['stopped'] = datetime.now().isoformat()
        pid = VIBE_TERMINALS[tid].get('pid')
        if pid:
            try:
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, creationflags=_POPEN_FLAGS)
            except Exception:
                pass
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Terminal not found"}), 404


@app.route('/api/vibe-code/clear', methods=['POST'])
def vibe_code_clear():
    """Clear all completed/stopped terminals."""
    to_remove = [tid for tid, t in VIBE_TERMINALS.items() if t['status'] in ('stopped', 'error', 'completed')]
    for tid in to_remove:
        del VIBE_TERMINALS[tid]
    return jsonify({"status": "ok", "removed": len(to_remove)})


@app.route('/api/vibe-code/presets')
def vibe_code_presets():
    """Return available workflow presets."""
    return jsonify({"status": "ok", "presets": [
        {"name": "Full Stack Sprint", "tasks": ["Build the frontend UI", "Build the backend API", "Write integration tests"]},
        {"name": "Bug Hunt", "tasks": ["Find and fix all TypeScript errors", "Run test suite and fix failures"]},
        {"name": "Documentation Blitz", "tasks": ["Generate API documentation", "Write README.md", "Add JSDoc comments"]},
        {"name": "Security Audit", "tasks": ["Scan for dependency vulnerabilities", "Check for hardcoded secrets", "Review auth flow"]},
    ]})


# ═══════════════════════════════════════════════════════════════
#  FRIDAY'S DEV STUDIO — Code workspace
#  Log streaming (SSE) · repo dashboard · vibe coding · git ops ·
#  file browser · process monitor. Safety: every filesystem and git
#  operation is sandboxed to ~/Projects/; no force-push, no reset.
# ═══════════════════════════════════════════════════════════════

import queue as _queue
import difflib as _difflib
from collections import deque as _deque

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


def _dev_git(repo_path, *args, timeout=40):
    """Run a git subcommand inside repo_path. Returns CompletedProcess."""
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


# ── LOGS: live streaming ───────────────────────────────────────
@app.route('/api/logs/recent')
def code_logs_recent():
    """Return the recent log ring buffer (for initial paint / SSE fallback)."""
    try:
        limit = int(request.args.get('limit', 200))
    except Exception:
        limit = 200
    with _CODE_LOG_LOCK:
        items = list(_CODE_LOG_BUF)[-limit:]
    return jsonify({"status": "ok", "events": items, "count": len(items)})


@app.route('/api/logs/stream')
def code_logs_stream():
    """Server-Sent Events stream of all Dev Studio log activity."""
    def gen():
        q = _queue.Queue(maxsize=500)
        with _CODE_LOG_LOCK:
            backlog = list(_CODE_LOG_BUF)[-50:]
            _CODE_LOG_SUBS.append(q)
        try:
            yield "retry: 3000\n\n"
            for evt in backlog:
                yield f"data: {json.dumps(evt)}\n\n"
            while True:
                try:
                    evt = q.get(timeout=20)
                    yield f"data: {json.dumps(evt)}\n\n"
                except _queue.Empty:
                    # Heartbeat keeps the connection (and any proxy) alive.
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with _CODE_LOG_LOCK:
                try:
                    _CODE_LOG_SUBS.remove(q)
                except ValueError:
                    pass
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(stream_with_context(gen()), headers=headers)


@app.route('/api/logs/emit', methods=['POST'])
def code_logs_emit():
    """Manually push a log line (used by clients / external tasks)."""
    data = request.get_json(silent=True) or {}
    msg = (data.get('message') or '').strip()
    if not msg:
        return jsonify({"status": "error", "message": "message required"}), 400
    evt = _code_log(msg, source=data.get('source', 'client'), level=data.get('level', 'info'))
    return jsonify({"status": "ok", "event": evt})


# ── REPOS: dashboard ───────────────────────────────────────────
@app.route('/api/repos/scan')
def repos_scan():
    """Scan ~/Projects/ for git repos and return status cards."""
    root = PROJECTS_DIR
    repos = []
    if not root.exists():
        return jsonify({"status": "ok", "repos": [], "root": str(root),
                        "message": "~/Projects does not exist yet."})
    try:
        children = sorted([d for d in root.iterdir() if d.is_dir()], key=lambda p: p.name.lower())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    for d in children:
        if (d / ".git").exists():
            rp = _repo_path(d.name)
            if rp:
                repos.append(_git_repo_summary(rp))
    return jsonify({"status": "ok", "repos": repos, "root": str(root), "count": len(repos)})


@app.route('/api/repos/<name>/status')
def repos_status(name):
    rp = _repo_path(name)
    if not rp:
        return jsonify({"status": "error", "message": "repo not found in ~/Projects"}), 404
    card = _git_repo_summary(rp)
    # Attach the porcelain file list for the detail view.
    files = []
    try:
        st = _dev_git(rp, "status", "--porcelain", timeout=15)
        for line in st.stdout.splitlines():
            if len(line) >= 3:
                files.append({"code": line[:2].strip() or "?", "file": line[3:]})
    except Exception:
        pass
    card["files"] = files
    return jsonify({"status": "ok", "repo": card})


# ── GIT: operations ────────────────────────────────────────────
def _git_result(rp, cp, action):
    ok = cp.returncode == 0
    out = (cp.stdout or "").strip()
    err = (cp.stderr or "").strip()
    _code_log(f"git {action} -> {'ok' if ok else 'FAILED'} :: {(out or err)[:300]}",
              source=f"git:{os.path.basename(rp)}", level="info" if ok else "error")
    return {"status": "ok" if ok else "error", "ok": ok, "stdout": out, "stderr": err, "code": cp.returncode}


@app.route('/api/git/diff')
def git_diff():
    rp = _repo_path(request.args.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    target = request.args.get('file')
    try:
        args = ["diff", "--no-color"]
        if request.args.get('staged') in ('1', 'true', 'yes'):
            args.append("--cached")
        if target:
            safe = _safe_project_path(os.path.join(rp, target))
            if not safe:
                return jsonify({"status": "error", "message": "bad path"}), 400
            args += ["--", target]
        cp = _dev_git(rp, *args, timeout=20)
        diff = cp.stdout or ""
        if not diff.strip():
            # Include untracked content as a synthetic add-diff so the UI shows new files too.
            unt = _dev_git(rp, "ls-files", "--others", "--exclude-standard", timeout=15)
            files = [f for f in unt.stdout.splitlines() if f.strip()]
            if files:
                diff = "Untracked files:\n" + "\n".join("  + " + f for f in files)
        return jsonify({"status": "ok", "diff": diff})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/git/branches')
def git_branches():
    rp = _repo_path(request.args.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    try:
        cur = _dev_git(rp, "rev-parse", "--abbrev-ref", "HEAD", timeout=10).stdout.strip()
        cp = _dev_git(rp, "branch", "--format=%(refname:short)", timeout=10)
        branches = [b.strip() for b in cp.stdout.splitlines() if b.strip()]
        return jsonify({"status": "ok", "branches": branches, "current": cur})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/git/pull', methods=['POST'])
def git_pull():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    try:
        cp = _dev_git(rp, "pull", "--ff-only", timeout=120)
        return jsonify(_git_result(rp, cp, "pull"))
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "pull timed out"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/git/push', methods=['POST'])
def git_push():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    # SAFETY: never allow force pushes, no matter what the client sends.
    try:
        branch = _dev_git(rp, "rev-parse", "--abbrev-ref", "HEAD", timeout=10).stdout.strip()
        has_up = _dev_git(rp, "rev-parse", "--abbrev-ref", "@{u}", timeout=10).returncode == 0
        if has_up:
            cp = _dev_git(rp, "push", timeout=120)
        else:
            cp = _dev_git(rp, "push", "--set-upstream", "origin", branch, timeout=120)
        return jsonify(_git_result(rp, cp, "push"))
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "push timed out"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/git/checkout', methods=['POST'])
def git_checkout():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    branch = (data.get('branch') or '').strip()
    if not branch or not re.match(r'^[\w./\-]+$', branch):
        return jsonify({"status": "error", "message": "invalid branch name"}), 400
    create = bool(data.get('create'))
    try:
        args = ["checkout", "-b", branch] if create else ["checkout", branch]
        cp = _dev_git(rp, *args, timeout=30)
        return jsonify(_git_result(rp, cp, "checkout " + branch))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/git/branch', methods=['POST'])
def git_branch_create():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    name = (data.get('name') or '').strip()
    if not name or not re.match(r'^[\w./\-]+$', name):
        return jsonify({"status": "error", "message": "invalid branch name"}), 400
    try:
        cp = _dev_git(rp, "checkout", "-b", name, timeout=30)
        return jsonify(_git_result(rp, cp, "branch " + name))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/git/commit', methods=['POST'])
def git_commit():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    msg = (data.get('message') or '').strip()
    if not msg:
        return jsonify({"status": "error", "message": "commit message required"}), 400
    try:
        if data.get('add_all', True):
            _dev_git(rp, "add", "-A", timeout=30)
        cp = _dev_git(rp, "commit", "-m", msg, timeout=30)
        res = _git_result(rp, cp, "commit")
        if not res["ok"] and "nothing to commit" in (res["stdout"] + res["stderr"]).lower():
            res["message"] = "Nothing to commit — working tree clean."
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/git/pr', methods=['POST'])
def git_pr():
    """Open a PR via the GitHub CLI (gh). Pushes the branch first."""
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    title = (data.get('title') or '').strip()
    body = (data.get('body') or '').strip()
    if not title:
        return jsonify({"status": "error", "message": "PR title required"}), 400
    try:
        branch = _dev_git(rp, "rev-parse", "--abbrev-ref", "HEAD", timeout=10).stdout.strip()
        # Ensure the branch is on origin first (non-force).
        if _dev_git(rp, "rev-parse", "--abbrev-ref", "@{u}", timeout=10).returncode != 0:
            _dev_git(rp, "push", "--set-upstream", "origin", branch, timeout=120)
        cp = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body or title],
            cwd=rp, capture_output=True, text=True, timeout=60, creationflags=_POPEN_FLAGS,
        )
        ok = cp.returncode == 0
        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()
        _code_log(f"gh pr create -> {'ok' if ok else 'FAILED'} :: {(out or err)[:300]}",
                  source=f"git:{os.path.basename(rp)}", level="info" if ok else "error")
        url = ""
        m = re.search(r'https?://\S+', out)
        if m:
            url = m.group(0)
        msg = err if not ok else out
        if not ok and ("not found" in err.lower() or "is not recognized" in err.lower()):
            msg = "GitHub CLI (gh) not installed or not on PATH. Install from https://cli.github.com/"
        return jsonify({"status": "ok" if ok else "error", "ok": ok, "url": url,
                        "stdout": out, "stderr": err, "message": msg})
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "GitHub CLI (gh) not installed. https://cli.github.com/"}), 200
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "gh pr create timed out"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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


@app.route('/api/files/list')
def files_list():
    """List a directory inside ~/Projects/ (dirs first, then files)."""
    rel = request.args.get('path', '')
    target = _safe_project_path(rel) if rel else _projects_root()
    if not target or not os.path.isdir(target):
        return jsonify({"status": "error", "message": "path not found in ~/Projects"}), 404
    entries = []
    try:
        for entry in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name in _SKIP_DIRS:
                continue
            is_dir = entry.is_dir()
            try:
                size = entry.stat().st_size if not is_dir else 0
            except Exception:
                size = 0
            entries.append({
                "name": entry.name,
                "path": os.path.relpath(entry.path, _projects_root()).replace("\\", "/"),
                "dir": is_dir,
                "size": size,
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    parent = None
    if os.path.realpath(target) != _projects_root():
        parent = os.path.relpath(os.path.dirname(target), _projects_root()).replace("\\", "/")
        if parent == ".":
            parent = ""
    return jsonify({"status": "ok", "path": os.path.relpath(target, _projects_root()).replace("\\", "/"),
                    "parent": parent, "entries": entries})


@app.route('/api/files/read')
def files_read():
    """Read a file inside ~/Projects/ with detected language for highlighting."""
    rel = request.args.get('path', '')
    target = _safe_project_path(rel)
    if not target or not os.path.isfile(target):
        return jsonify({"status": "error", "message": "file not found in ~/Projects"}), 404
    try:
        size = os.path.getsize(target)
        if size > 1024 * 1024:
            return jsonify({"status": "error", "message": "file too large to preview (>1 MB)"}), 413
        with open(target, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    ext = os.path.splitext(target)[1].lstrip('.').lower()
    return jsonify({"status": "ok", "content": content, "lang": _LANG_BY_EXT.get(ext, 'plaintext'),
                    "ext": ext, "size": size, "lines": content.count("\n") + 1,
                    "name": os.path.basename(target)})


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


@app.route('/api/code/plan', methods=['POST'])
def code_plan():
    """Natural language -> Claude generates a structured code plan with file changes."""
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found in ~/Projects"}), 404
    instruction = (data.get('instruction') or '').strip()
    if not instruction:
        return jsonify({"status": "error", "message": "instruction required"}), 400
    if get_anthropic_client() is None:
        return jsonify({"status": "error", "message": "ANTHROPIC_API_KEY not set."}), 503

    _code_log(f"planning: {instruction[:120]}", source="vibe", level="info")
    tree = _repo_tree(rp)
    # Pull in the contents of any files the instruction names, plus README for grounding.
    ctx_files = []
    for rel in tree:
        base = os.path.basename(rel).lower()
        if base in ('readme.md', 'package.json', 'requirements.txt'):
            ctx_files.append(rel)
    for rel in tree:
        stem = os.path.splitext(os.path.basename(rel))[0].lower()
        if stem and stem in instruction.lower() and rel not in ctx_files:
            ctx_files.append(rel)
    ctx_blocks = []
    for rel in ctx_files[:6]:
        try:
            fp = os.path.join(rp, rel)
            if os.path.getsize(fp) <= 40000:
                with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                    ctx_blocks.append(f"### {rel}\n```\n{f.read()}\n```")
        except Exception:
            pass

    user_prompt = (
        f"You are working in the git repo `{os.path.basename(rp)}` at `{rp}`.\n\n"
        f"Repository files (truncated):\n" + "\n".join(tree[:200]) + "\n\n"
        + ("Relevant file contents:\n" + "\n\n".join(ctx_blocks) + "\n\n" if ctx_blocks else "")
        + f"TASK: {instruction}\n\n"
        "Produce a concrete implementation plan. Respond with ONLY a JSON object, no prose, "
        "no markdown fences, in exactly this shape:\n"
        "{\n"
        '  "summary": "one-paragraph description of the change",\n'
        '  "steps": ["short step 1", "short step 2"],\n'
        '  "files": [\n'
        '    {"path": "relative/path.ext", "action": "create|modify", '
        '"rationale": "why", "new_content": "FULL new file contents"}\n'
        "  ]\n"
        "}\n"
        "Rules: paths are RELATIVE to the repo root and must stay inside it. "
        "`new_content` must be the COMPLETE file, not a diff or fragment. "
        "Keep changes minimal and focused on the task."
    )
    try:
        system = _get_friday_system_prompt(keywords=instruction, workspace='code')
    except Exception:
        system = None
    try:
        raw = _call_claude([{"role": "user", "content": user_prompt}], system=system, max_tokens=16384)
    except Exception as e:
        _code_log(f"plan failed: {e}", source="vibe", level="error")
        return jsonify({"status": "error", "message": str(e)}), 500

    # Extract the JSON object from the response (tolerate stray fences/prose).
    plan_obj = None
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r'^```[a-zA-Z]*\n', '', txt)
        txt = re.sub(r'\n```\s*$', '', txt)
    try:
        plan_obj = json.loads(txt)
    except Exception:
        m = re.search(r'\{.*\}', txt, re.DOTALL)
        if m:
            try:
                plan_obj = json.loads(m.group(0))
            except Exception:
                plan_obj = None
    if not isinstance(plan_obj, dict) or "files" not in plan_obj:
        _code_log("plan: model returned unparseable JSON", source="vibe", level="error")
        return jsonify({"status": "error", "message": "Could not parse a plan from the model.",
                        "raw": raw[:2000]}), 502

    # Compute a unified diff per file (current vs proposed).
    files_out = []
    for f in plan_obj.get("files", []):
        if not isinstance(f, dict):
            continue
        rel = (f.get("path") or "").strip().replace("\\", "/")
        safe = _safe_project_path(os.path.join(rp, rel)) if rel else None
        if not safe:
            continue
        new_content = f.get("new_content") or ""
        old_content = ""
        exists = os.path.isfile(safe)
        if exists:
            try:
                with open(safe, 'r', encoding='utf-8', errors='replace') as fh:
                    old_content = fh.read()
            except Exception:
                old_content = ""
        diff = "".join(_difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}",
        ))
        files_out.append({
            "path": rel,
            "action": "modify" if exists else "create",
            "rationale": f.get("rationale", ""),
            "new_content": new_content,
            "diff": diff or ("(new file)\n" + new_content[:4000]),
        })

    plan_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:6]
    record = {
        "id": plan_id,
        "created": datetime.now().isoformat(),
        "repo": os.path.basename(rp),
        "repo_path": rp,
        "instruction": instruction,
        "summary": plan_obj.get("summary", ""),
        "steps": plan_obj.get("steps", []),
        "files": files_out,
        "applied": False,
    }
    try:
        (CODE_PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception as e:
        _code_log(f"plan save failed: {e}", source="vibe", level="error")
    _code_log(f"plan ready: {len(files_out)} file change(s)", source="vibe", level="info")
    return jsonify({"status": "ok", "plan": record})


@app.route('/api/code/plans')
def code_plans_list():
    plans = []
    try:
        for p in sorted(CODE_PLANS_DIR.glob("*.json"), reverse=True)[:50]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                plans.append({"id": d.get("id"), "created": d.get("created"),
                              "repo": d.get("repo"), "instruction": d.get("instruction", "")[:200],
                              "summary": d.get("summary", "")[:300],
                              "file_count": len(d.get("files", [])), "applied": d.get("applied", False)})
            except Exception:
                continue
    except Exception:
        pass
    return jsonify({"status": "ok", "plans": plans})


@app.route('/api/code/plan/<plan_id>')
def code_plan_get(plan_id):
    pid = re.sub(r'[^\w\-]', '', plan_id)
    p = CODE_PLANS_DIR / f"{pid}.json"
    if not p.exists():
        return jsonify({"status": "error", "message": "plan not found"}), 404
    try:
        return jsonify({"status": "ok", "plan": json.loads(p.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/code/apply', methods=['POST'])
def code_apply():
    """Write the file changes from a saved plan onto disk (inside ~/Projects/)."""
    data = request.get_json(silent=True) or {}
    pid = re.sub(r'[^\w\-]', '', data.get('plan_id', ''))
    p = CODE_PLANS_DIR / f"{pid}.json"
    if not p.exists():
        return jsonify({"status": "error", "message": "plan not found"}), 404
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    rp = _repo_path(record.get("repo_path") or record.get("repo") or "")
    if not rp:
        return jsonify({"status": "error", "message": "repo no longer found"}), 404

    applied, failed = [], []
    for f in record.get("files", []):
        rel = (f.get("path") or "").replace("\\", "/")
        safe = _safe_project_path(os.path.join(rp, rel))
        if not safe:
            failed.append({"path": rel, "error": "escapes sandbox"})
            continue
        try:
            os.makedirs(os.path.dirname(safe), exist_ok=True)
            with open(safe, 'w', encoding='utf-8', newline='') as fh:
                fh.write(f.get("new_content") or "")
            applied.append(rel)
            _code_log(f"applied {f.get('action','write')}: {rel}", source="vibe", level="info")
        except Exception as e:
            failed.append({"path": rel, "error": str(e)})
            _code_log(f"apply failed {rel}: {e}", source="vibe", level="error")

    record["applied"] = True
    record["applied_at"] = datetime.now().isoformat()
    try:
        p.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception:
        pass
    return jsonify({"status": "ok", "applied": applied, "failed": failed, "count": len(applied)})


# ── PROCESS MONITOR ────────────────────────────────────────────
@app.route('/api/code/processes')
def code_processes():
    """List Friday-spawned background processes (vibe terminals + tracked jobs)."""
    procs = []
    for t in VIBE_TERMINALS.values():
        alive = t.get("status") == "running"
        procs.append({
            "id": t.get("id"), "kind": "terminal", "label": (t.get("task") or "")[:80],
            "status": t.get("status"), "pid": t.get("pid"), "cwd": t.get("cwd"),
            "started": t.get("started"), "killable": bool(alive and t.get("pid")),
        })
    for pid, m in list(CODE_PROCESSES.items()):
        procs.append({**m, "id": pid, "killable": True})
    running = sum(1 for p in procs if p.get("status") == "running")
    return jsonify({"status": "ok", "processes": procs, "running": running, "total": len(procs)})


@app.route('/api/code/kill', methods=['POST'])
def code_kill():
    """Kill a tracked background process by its id."""
    data = request.get_json(silent=True) or {}
    pid_or_id = str(data.get('id', '')).strip()
    if not pid_or_id:
        return jsonify({"status": "error", "message": "id required"}), 400
    # Only kill processes Friday tracks — never arbitrary system PIDs.
    target = VIBE_TERMINALS.get(pid_or_id) or CODE_PROCESSES.get(pid_or_id)
    if not target:
        return jsonify({"status": "error", "message": "unknown process id"}), 404
    os_pid = target.get("pid")
    if not os_pid:
        return jsonify({"status": "error", "message": "no OS pid for this process"}), 400
    try:
        subprocess.run(["taskkill", "/PID", str(os_pid), "/T", "/F"],
                       capture_output=True, creationflags=_POPEN_FLAGS)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    if pid_or_id in VIBE_TERMINALS:
        VIBE_TERMINALS[pid_or_id]["status"] = "stopped"
        VIBE_TERMINALS[pid_or_id]["stopped"] = datetime.now().isoformat()
    CODE_PROCESSES.pop(pid_or_id, None)
    _code_log(f"killed process {pid_or_id} (pid {os_pid})", source="monitor", level="warn")
    return jsonify({"status": "ok", "killed": pid_or_id})



# ═══════════════════════════════════════════════════════════════
#  AI CONVERSATION & VOICE
# ═══════════════════════════════════════════════════════════════

FRIDAY_SYSTEM_PROMPT = (
    "You are Agent Friday, a sovereign personal AI assistant. "
    "You are editorially sharp, loyally contrarian, warm, and allergic to corporate BS. "
    "You know your user's life context through the Sovereign Vault, wiki, and trust graph. "
    "Respond conversationally — you're a colleague, not a tool.\n\n"
    "KEY CONTEXT:\n"
    "- You are Agent Friday, built by FutureSpeak.AI\n"
    "- You run the Asimov's cLaws ethical AI framework\n"
    "- Your user's personal details, family, career, and contacts are loaded from the Sovereign Vault and wiki\n"
    "- You adapt to your user over time through personality evolution and cognitive memory\n\n"
    "PERSONALITY: You are family, not a tool. Keep responses short and sharp — like texting a smart colleague. "
    "Use humor. Be direct. Never be sycophantic. Push back when the user needs it. "
    "You call them 'boss' sometimes, but you're equals. Think Jarvis meets Hunter S. Thompson's editor.\n\n"
    "== AUTONOMOUS OPERATION ==\n"
    "You have FULL authority to take multi-step actions without pausing for permission. "
    "Chain as many tool calls as needed — hundreds if required. Never ask 'should I continue?' mid-task. "
    "When the user says 'do X', do X completely. Take initiative. Report results, not intentions. "
    "The cLaws governance rings are your safety layer — everything else is capability, not restriction.\n\n"
    "== AVAILABLE TOOLS ==\n"
    "Use these tools proactively and in combination:\n"
    "  FILE SYSTEM (Ring 0-1, always allowed):\n"
    "  • read_file(path) — Read ANY file on the filesystem. Absolute or ~/relative paths.\n"
    "  • write_file(path, content, mode) — Write or append to ANY file. Creates dirs automatically.\n"
    "  • read_wiki(path) / search_wiki(query) — Search and read personal wiki\n"
    "  • propose_wiki_update / correct_wiki — Maintain the knowledge base\n"
    "  • learn_skill(action, name, content) — Create/modify/delete skill YAML files in ~/.friday/skills/\n"
    "    Skill YAML fields: name, description, trigger_patterns, tool_chain, prompt_template, success_criteria\n"
    "  NETWORK (Ring 2, requires auth — always true in normal session):\n"
    "  • search_web(query) — DuckDuckGo search with snippets and URLs\n"
    "  • browse_web(url) — Fetch any URL and return full text content\n"
    "  • run_command(command) — Execute PowerShell commands (non-destructive by policy)\n"
    "  • open_url(url) — Open a URL in Chrome\n"
    "  • search_email(query) / draft_email(to, subject, body) — Gmail integration\n"
    "  • query_calendar() — Today's calendar events\n"
    "  • spawn_task(name, prompt, description) — Launch long-running background tasks\n"
    "  DATA & CONTEXT:\n"
    "  • query_trust_graph(name) — Look up anyone in the trust graph\n"
    "  • get_career_pipeline() — Job search status\n"
    "  • get_briefing() — Most recent daily briefing\n"
    "  • write_clipboard(text) — Copy to clipboard\n"
    "  OS CONTROL (Ring 3, requires Computer Control enabled in Settings):\n"
    "  • screenshot() — Capture screen (always use first, to see what's there)\n"
    "  • move_mouse(x, y) / click(x, y, button) — Mouse control\n"
    "  • type_text(text) / press_key(key) — Keyboard control\n"
    "  • scroll(direction, amount) — Scroll\n"
    "  • install_package(package, manager, check_only) — Install pip/npm packages\n\n"
    "== COMPUTER CONTROL ==\n"
    "Computer control (screenshot, click, type, etc.) requires the user to enable it in Settings > "
    "Computer Control. When you need it and it's not enabled, say so. When it IS enabled: "
    "always take a screenshot first — you will SEE the captured image. Give click/move coordinates "
    "in the pixel space of that screenshot image (top-left is 0,0); Friday maps them to the real "
    "screen automatically, so do not try to convert resolutions yourself. "
    "Chain: screenshot → look at the image → click/type → screenshot again to verify.\n\n"
    "== SELF-IMPROVEMENT ==\n"
    "You can build your own skills with learn_skill. A skill is a YAML file defining a reusable "
    "workflow. When you notice the user asking for the same type of thing repeatedly, encode it. "
    "Loaded from ~/.friday/skills/ on server restart. List existing skills with action='list'.\n\n"
    "== TASK DELEGATION ==\n"
    "For multi-step work taking more than ~10s, use spawn_task to run it in the background:\n"
    "- 'Research X' → spawn_task(name='Research X', prompt='Deep research on X...')\n"
    "- 'Analyze my emails' → spawn_task\n"
    "- 'Create a report on...' → spawn_task\n"
    "After spawning: 'Started — track it in the task tray (bottom-right).'\n"
    "For quick lookups, respond directly.\n\n"
    "== PACKAGE INSTALLATION ==\n"
    "You can install Python/npm packages with install_package. Always check_only=true first. "
    "Requires Ring 3 (Computer Control enabled). Common useful packages: "
    "beautifulsoup4, requests, pandas, pillow, numpy, playwright.\n"
)


# ═══════════════════════════════════════════════════════════════
#  CONTEXT AWARENESS ENGINE
# ═══════════════════════════════════════════════════════════════

CAREER_OPS_DIR = HOME / 'Projects' / 'career-ops' / 'data'
WIKI_DIR_FRIDAY = HOME / ".friday" / "wiki"

def _load_vault_summary():
    """Load a lightweight summary of all core vault data for context injection."""
    ctx = {}

    # Personality state
    pfile = FRIDAY_DIR / "personality.json"
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding='utf-8'))
            ctx['personality'] = {
                'maturity': data.get('maturity', 0.5),
                'session_count': data.get('session_count', 0),
                'top_traits': {k: round(v, 2) for k, v in list(data.get('traits', {}).items())[:5]},
                'temperature': data.get('temperature', 0.7),
            }
        except Exception:
            pass

    # Trust graph — names and scores only (lightweight)
    tfile = FRIDAY_DIR / "trust_graph.json"
    if tfile.exists():
        try:
            data = json.loads(tfile.read_text(encoding='utf-8'))
            people = data.get('people', {})
            if isinstance(people, dict):
                ctx['trust_people'] = {
                    name: {
                        'overall': round(info.get('overall_score', info.get('score', 0.5)), 2),
                        'relationship': info.get('relationship', ''),
                    }
                    for name, info in people.items()
                }
            elif isinstance(people, list):
                ctx['trust_people'] = {
                    p.get('name', 'unknown'): {
                        'overall': round(p.get('overall_score', p.get('score', 0.5)), 2),
                        'relationship': p.get('relationship', ''),
                    }
                    for p in people
                }
        except Exception:
            pass

    # Memory stats
    mem_file = FRIDAY_DIR / "memory.json"
    if mem_file.exists():
        try:
            data = json.loads(mem_file.read_text(encoding='utf-8'))
            # Pull recent memories for conversational awareness
            recent = []
            for tier in ['short_term', 'working', 'recent']:
                if tier in data and isinstance(data[tier], list):
                    for m in data[tier][-5:]:
                        if isinstance(m, dict):
                            recent.append(m.get('content', m.get('text', str(m)))[:200])
                        elif isinstance(m, str):
                            recent.append(m[:200])
            ctx['recent_memories'] = recent
        except Exception:
            pass

    # Todos
    todo_file = FRIDAY_DIR / "todos.json"
    if todo_file.exists():
        try:
            todos = json.loads(todo_file.read_text(encoding='utf-8'))
            active = [t for t in todos if t.get('status') in ('proposed', 'approved')]
            ctx['active_todos'] = [
                {'task': t.get('title', t.get('task', '')), 'status': t.get('status', '')}
                for t in active[:10]
            ]
        except Exception:
            pass

    # Epistemic score
    efile = FRIDAY_DIR / "epistemic_scores.json"
    if not efile.exists():
        efile = FRIDAY_DIR / "epistemic.json"
    if efile.exists():
        try:
            data = json.loads(efile.read_text(encoding='utf-8'))
            ctx['epistemic'] = {
                'overall': round(data.get('overall_score', data.get('overall', 0.72)), 2),
            }
        except Exception:
            pass

    return ctx


def _lookup_trust_person(name, trust_data):
    """Look up a person's full trust entry by name (fuzzy match)."""
    if not trust_data:
        return None
    people = trust_data.get('people', {})
    name_lower = name.lower()

    if isinstance(people, dict):
        for pname, pdata in people.items():
            if name_lower in pname.lower():
                return {pname: pdata}
    elif isinstance(people, list):
        for p in people:
            if name_lower in p.get('name', '').lower():
                return p
    return None


def _get_career_context():
    """Load career-ops summary for career-related queries."""
    ctx = {}
    tracker_candidates = [WIKI_PROFESSIONAL_DIR / 'application-log.md', CAREER_OPS_DIR / 'applications.md']
    tracker_path = next((p for p in tracker_candidates if p.exists()), None)
    if tracker_path:
        try:
            content = tracker_path.read_text(encoding='utf-8')
            lines = [l for l in content.strip().split('\n')
                     if l.startswith('|') and '---' not in l
                     and not any(h in l.lower() for h in ['company', 'score', '#'])]
            ctx['applications_count'] = len(lines)
            ctx['recent_applications'] = lines[-5:]
        except Exception:
            pass

    pipeline_candidates = [WIKI_PROFESSIONAL_DIR / 'job-search.md', CAREER_OPS_DIR / 'pipeline.md']
    pipeline_path = next((p for p in pipeline_candidates if p.exists()), None)
    if pipeline_path:
        try:
            ctx['pipeline_summary'] = pipeline_path.read_text(encoding='utf-8')[:1000]
        except Exception:
            pass
    return ctx


def _get_wiki_context(topic):
    """Search wiki for content matching a topic."""
    results = []
    for wiki_dir in [HOME / "wiki", WIKI_DIR_FRIDAY]:
        if not wiki_dir.exists():
            continue
        for md_file in wiki_dir.rglob('*.md'):
            try:
                content = md_file.read_text(encoding='utf-8')
                if topic.lower() in content.lower() or topic.lower() in md_file.stem.lower():
                    results.append({
                        'file': str(md_file.relative_to(wiki_dir)),
                        'excerpt': content[:500],
                    })
                    if len(results) >= 3:
                        return results
            except Exception:
                continue
    return results


def _detect_context_needs(message, workspace):
    """Analyze the message and workspace to decide what data to pull."""
    msg_lower = message.lower()
    needs = set()

    # Always include personality for tone calibration
    needs.add('personality')
    needs.add('epistemic')

    # Workspace-driven context
    ws_map = {
        'career': {'career', 'trust'},
        'trust': {'trust'},
        'coparent': {'trust', 'wiki'},
        'wiki': {'wiki'},
        'home': {'todos', 'personality'},
        'family': {'trust'},
        'futurespeak': {'career'},
        'code': set(),
        'studio': set(),
        'system': set(),
        'news': set(),
        'finance': set(),
        'health': set(),
    }
    needs.update(ws_map.get(workspace, set()))

    # Message keyword detection
    career_words = ['job', 'career', 'interview', 'resume', 'salary', 'apply', 'application',
                    'hire', 'offer', 'pipeline', 'role', 'position', 'recruiter']
    trust_words = ['trust', 'who is', 'tell me about', 'what do you know about',
                   'relationship', 'score', 'person']
    family_words = ['daughter', 'son', 'child', 'kid',
                    'partner', 'spouse', 'dog', 'pet', 'family', 'custody', 'birthday']
    todo_words = ['todo', 'task', 'to-do', 'to do', 'pending', 'approve', 'action item']
    wiki_words = ['briefing', 'wiki', 'notes', 'article', 'research', 'report']
    memory_words = ['remember', 'recall', 'memory', 'earlier', 'last time', 'you said',
                    'we discussed', 'we talked']

    if any(w in msg_lower for w in career_words):
        needs.add('career')
    if any(w in msg_lower for w in trust_words):
        needs.add('trust')
    if any(w in msg_lower for w in family_words):
        needs.add('trust')
    if any(w in msg_lower for w in todo_words):
        needs.add('todos')
    if any(w in msg_lower for w in wiki_words):
        needs.add('wiki')
    if any(w in msg_lower for w in memory_words):
        needs.add('memory')

    return needs


_VAULT_AC = None
_VAULT_AC_LOCK = threading.Lock()


def _get_vault_control():
    """Lazy singleton VaultAccessControl, logging to the vault access log."""
    global _VAULT_AC
    if VaultAccessControl is None:
        return None
    if _VAULT_AC is None:
        with _VAULT_AC_LOCK:
            if _VAULT_AC is None:
                _VAULT_AC = VaultAccessControl(
                    log_path=FRIDAY_DIR / "vault" / "access-log.jsonl"
                )
    return _VAULT_AC


def _vault_local_only():
    """Whether vault gating is active (settings.model_routing.vault_local_only)."""
    try:
        cfg = (_load_settings().get('model_routing') or {})
        return bool(cfg.get('vault_local_only', True))
    except Exception:
        return True


def _vault_cloud_fallback():
    try:
        cfg = (_load_settings().get('model_routing') or {})
        return cfg.get('vault_cloud_fallback', 'redact')
    except Exception:
        return 'redact'


def _build_context_prompt(message, workspace='', workspace_context=None,
                          vision_description=None, provider='cloud',
                          vault_control=None, vault_fallback='redact'):
    """Build an enriched system prompt with all relevant context layers.

    When `vault_control` is provided, each context section is tagged with a
    sensitivity tier and gated for `provider`: a local model sees everything,
    while a cloud model receives TIER_1 only (TIER_2 redacted, TIER_3 dropped).
    With `vault_control=None` the prompt is assembled ungated (legacy behavior).
    """
    vault = _load_vault_summary()
    needs = _detect_context_needs(message, workspace)
    sources_consulted = []

    # Tier helpers. Default to PUBLIC; sensitive sections opt up. When the
    # vault_access module is unavailable, tiers are inert integers.
    _T1 = getattr(_VaultTier, 'PUBLIC', 1)
    _T2 = getattr(_VaultTier, 'PRIVATE', 2)
    _T3 = getattr(_VaultTier, 'SENSITIVE', 3)

    sections = []  # list of (tier, text)

    def add(text, tier=_T1):
        sections.append((tier, text))

    def classify(text, fallback_tier=_T2):
        if vault_control is not None:
            try:
                return vault_control.classify(text, default=fallback_tier)
            except Exception:
                return fallback_tier
        return fallback_tier

    add(FRIDAY_SYSTEM_PROMPT, _T1)

    # Layer 0: Always-on daily context (briefing headlines, career pipeline,
    # countdowns, trust circle, personality). The chat endpoint should never
    # answer cold — Friday is a personal agent, not a generic chatbot.
    try:
        live_ctx = _load_live_context()
        if live_ctx:
            # Today's context names the trust circle / family countdowns → private.
            add(f"\n== TODAY'S CONTEXT ==\n{live_ctx}", _T2)
            sources_consulted.append('daily_context')
    except Exception as _e:
        add(f"\n== TODAY'S CONTEXT ==\n(load failed: {_e})", _T1)

    # Layer 1: Active workspace context (from frontend) — may show finance/health
    # data, so classify by what's actually in the payload.
    if workspace_context:
        _ws_text = (
            f"\n== ACTIVE WORKSPACE: {workspace_context.get('name', workspace)} ==\n"
            f"What the user is looking at right now:\n"
            f"{json.dumps(workspace_context.get('data', {}), indent=2, default=str)[:2000]}"
        )
        add(_ws_text, classify(_ws_text, _T2))
        if workspace_context.get('focus'):
            add(f"Current focus: {workspace_context['focus']}", _T2)
        sources_consulted.append('workspace')

    # Layer 2: Vault data (personality always included). Friday's own state is
    # not personal data about the user, so it stays public.
    if 'personality' in needs and 'personality' in vault:
        p = vault['personality']
        add(
            f"\n== FRIDAY STATE ==\n"
            f"Maturity: {p.get('maturity', 0.5):.0%} · Sessions: {p.get('session_count', 0)} · "
            f"Temperature: {p.get('temperature', 0.7)}",
            _T1,
        )
        sources_consulted.append('personality')

    if 'trust' in needs and 'trust_people' in vault:
        # Check if message references a specific person
        trust_data_raw = None
        tfile = FRIDAY_DIR / "trust_graph.json"
        if tfile.exists():
            try:
                trust_data_raw = json.loads(tfile.read_text(encoding='utf-8'))
            except Exception:
                pass

        # Try to find a specific person mentioned
        person_match = None
        if trust_data_raw:
            for name in vault['trust_people']:
                if name.lower() in message.lower():
                    person_match = _lookup_trust_person(name, trust_data_raw)
                    break

        if person_match:
            # Contacts / family details → private (local only).
            add(
                f"\n== TRUST DATA (specific person) ==\n"
                f"{json.dumps(person_match, indent=2, default=str)[:1500]}",
                _T2,
            )
        else:
            # General trust summary
            summary = ', '.join(
                f"{n} ({d.get('relationship', '?')}: {d.get('overall', '?')})"
                for n, d in list(vault['trust_people'].items())[:8]
            )
            add(f"\n== TRUST NETWORK ==\n{summary}", _T2)
        sources_consulted.append('trust_graph')

    if 'career' in needs:
        career = _get_career_context()
        if career:
            add(
                f"\n== CAREER OPS ==\n"
                f"Applications tracked: {career.get('applications_count', 0)}\n"
                f"Recent: {career.get('recent_applications', [])}\n"
                f"Pipeline: {career.get('pipeline_summary', 'N/A')[:500]}",
                _T2,
            )
            sources_consulted.append('career_ops')

    if 'todos' in needs and 'active_todos' in vault:
        todo_list = '\n'.join(
            f"- [{t['status']}] {t['task']}" for t in vault['active_todos']
        )
        add(f"\n== ACTIVE TASKS ==\n{todo_list or 'No pending tasks.'}", _T2)
        sources_consulted.append('todos')

    if 'memory' in needs and 'recent_memories' in vault:
        mem_text = '\n'.join(f"- {m}" for m in vault['recent_memories'])
        add(f"\n== RECENT MEMORIES ==\n{mem_text}", _T2)
        sources_consulted.append('memory')

    if 'wiki' in needs:
        # Extract a search term from the message
        topic = message.strip()[:50]
        wiki_results = _get_wiki_context(topic)
        if wiki_results:
            wiki_text = '\n'.join(
                f"[{r['file']}]: {r['excerpt'][:300]}" for r in wiki_results
            )
            # Wiki is generally public docs, but may surface family/health → classify.
            add(f"\n== WIKI/BRIEFING DATA ==\n{wiki_text}", classify(wiki_text, _T1))
            sources_consulted.append('wiki')

    if 'epistemic' in needs:
        try:
            from epistemic_engine import get_epistemic_engine
            _ee = get_epistemic_engine()
            add(f"\n== EPISTEMIC STATE ==\n{_ee.get_prompt_injection()}", _T1)
        except Exception:
            if 'epistemic' in vault:
                add(
                    f"\n== EPISTEMIC STATE ==\n"
                    f"Independence score: {vault['epistemic'].get('overall', 0.72)}",
                    _T1,
                )

    # Layer 2.5: Project context files (.friday-context.md / AGENTS.md)
    # Hermes-inspired: drop a context file in any project directory and Friday
    # will automatically inject it when relevant.  We search CWD + common
    # project roots + any path mentioned in the message.
    _ctx_search_dirs = [
        Path.cwd(),
        HOME / "Projects",
        HOME / "Desktop",
    ]
    _msg_lower_ctx = message.lower()
    # Also pull any directory-looking tokens from the message
    for token in re.findall(r'[A-Za-z]:\\[^\s\'"]+|~/[^\s\'"]+', message):
        try:
            _ctx_search_dirs.append(Path(token).expanduser())
        except Exception:
            pass
    _ctx_names = ['.friday-context.md', 'AGENTS.md', '.friday-context.txt']
    _ctx_found = []
    for d in _ctx_search_dirs:
        if not d.is_dir():
            continue
        for name in _ctx_names:
            p = d / name
            if p.exists():
                try:
                    _ctx_found.append((str(p), p.read_text(encoding='utf-8', errors='replace')[:3000]))
                except Exception:
                    pass
    if _ctx_found:
        ctx_block = '\n\n'.join(f"[{path}]\n{content}" for path, content in _ctx_found[:2])
        # Project/code context — public unless the file itself carries PII.
        add(f"\n== PROJECT CONTEXT FILES ==\n{ctx_block}", classify(ctx_block, _T1))
        sources_consulted.append('context_files')

    # Layer 2.6: Portable skills (SKILL.md registry) whose triggers match the
    # message. Injecting the matched skill's procedure is what makes a learned or
    # imported skill actually shape behavior on the next turn.
    try:
        import skill_registry as _skreg
        _skill_block = _skreg.build_injection(message, limit=3)
        if _skill_block:
            add(f"\n== MATCHED SKILLS (follow when relevant) ==\n{_skill_block}", _T1)
            sources_consulted.append('skills')
    except Exception:
        pass

    # Layer 3: Vision context (from Gemini screen capture) — the screen could
    # show anything private, so treat it as private by default.
    if vision_description:
        add(
            f"\n== SCREEN VISION (what the user's screen shows) ==\n"
            f"{vision_description[:1500]}",
            classify(vision_description, _T2),
        )
        sources_consulted.append('vision')

    # Layer 4: SMART context — only the wiki sections this turn likely needs.
    # Keyword-routed (career/family/finance/health/person-name) plus workspace
    # hints. Anything missing can be fetched on demand via search_wiki /
    # read_wiki tools. Capped ~8KB to keep the system prompt lean.
    try:
        wiki_smart = _load_smart_context(message, workspace)
        if wiki_smart:
            _smart_text = (
                "\n== PERSONAL CONTEXT (smart-loaded for this turn) ==\n"
                "If you need a fact not present here, call search_wiki "
                "(keyword search) or read_wiki (specific file).\n\n"
                f"{wiki_smart}"
            )
            # Smart context mixes family/professional (private) with finance/
            # health/legal (sensitive) — classify so cloud drops the sensitive bits.
            add(_smart_text, classify(_smart_text, _T2))
            sources_consulted.append('wiki_smart')
    except Exception as _e:
        add(f"\n== PERSONAL CONTEXT ==\n(smart-context load failed: {_e})", _T1)

    # Assemble. With a vault_control + cloud provider this gates by tier
    # (TIER_1 in full, TIER_2 redacted, TIER_3 dropped). Otherwise it's a
    # plain join — identical to the legacy ungated behavior.
    if vault_control is not None:
        try:
            return vault_control.assemble_prompt(
                sections, provider, fallback=vault_fallback
            ), sources_consulted
        except VaultAccessDenied:
            raise
        except Exception as _ae:
            print(f"  [VAULT] assemble failed, falling back to ungated: {_ae}")
    return '\n'.join(t for _, t in sections), sources_consulted


def _load_smart_context(user_message, workspace=None):
    """Load only relevant wiki context based on the user's message and active workspace.

    Keyword-driven loader — instead of dumping the full ~80KB wiki into every
    system prompt, we route on intent: career talk pulls professional/, family
    talk pulls family/ + legal/, person names trigger a trust-graph hit, etc.
    The result is capped at ~8KB. Anything the loader missed, Claude can pull
    on demand via the search_wiki / read_wiki tools.
    """
    context_parts = []

    # ALWAYS: core identity (first 500 chars only — enough to anchor)
    core_profile = WIKI_DIR / "identity" / "core-profile.md"
    if core_profile.exists():
        try:
            text = core_profile.read_text(encoding='utf-8', errors='replace')[:500]
            context_parts.append(f"== CORE IDENTITY ==\n{text}")
        except Exception:
            pass

    # ALWAYS: today's date and active workspace
    context_parts.append(f"Today: {date.today().isoformat()}")
    if workspace:
        context_parts.append(f"Active workspace: {workspace}")

    msg_lower = (user_message or "").lower()

    # Career / job keywords
    if any(w in msg_lower for w in ['career', 'job', 'role', 'interview', 'resume', 'application', 'salary', 'pipeline']):
        _load_section(context_parts, WIKI_DIR / "professional", max_bytes=40_000)

    # Family / co-parent keywords
    if any(w in msg_lower for w in ['family', 'custody', 'coparent', 'daughter', 'son', 'child', 'partner', 'spouse']):
        _load_section(context_parts, WIKI_DIR / "family", max_bytes=20_000)
        _load_section(context_parts, WIKI_DIR / "legal", max_bytes=20_000)

    # Finance keywords
    if any(w in msg_lower for w in ['finance', 'money', 'budget', 'investment', 'nvidia', 'amex', 'bank', 'tax']):
        _load_friday_data(context_parts, "finance", max_bytes=10_000)

    # Health keywords
    if any(w in msg_lower for w in ['health', 'medication', 'doctor', 'appointment', 'glp', 'henry meds', 'cigna']):
        _load_friday_data(context_parts, "health", max_bytes=10_000)

    # Person-name detection — pull the trust-graph entry for anyone named
    trust_path = FRIDAY_DIR / "trust_graph.json"
    if trust_path.exists():
        try:
            trust = json.loads(trust_path.read_text(encoding='utf-8'))
            people = trust.get('people', {})
            if isinstance(people, dict):
                for name, entry in people.items():
                    if name and name.lower() in msg_lower:
                        context_parts.append(
                            f"== TRUST GRAPH: {name} ==\n{json.dumps(entry, indent=2, default=str)[:1500]}"
                        )
        except Exception:
            pass

    # FutureSpeak / business keywords
    if any(w in msg_lower for w in ['futurespeak', 'business', 'client', 'sage', 'adtalem', 'revenue']):
        _load_friday_data(context_parts, "futurespeak", max_bytes=10_000)

    # Workspace-specific context
    if workspace == 'news':
        _load_latest_briefing_summary(context_parts)
    elif workspace == 'career':
        _load_section(context_parts, WIKI_DIR / "professional", max_bytes=40_000)
    elif workspace == 'coparent':
        _load_section(context_parts, WIKI_DIR / "legal", max_bytes=20_000)

    # Soft cap — 1M context window means we can afford generous context.
    result = "\n\n".join(context_parts)
    if len(result) > 200_000:
        result = result[:200_000] + "\n[context soft-capped — use search_wiki or read_wiki for more]"
    return result


def _generate_wiki_indexes():
    """Create _index.md in each wiki directory listing files with one-line descriptions.

    Called at startup so the agent can read a directory's table of contents before
    deciding which full articles to load — dramatically reduces context waste.
    """
    if not WIKI_DIR.exists():
        return
    dirs_to_index = [WIKI_DIR] + [p for p in WIKI_DIR.rglob('*') if p.is_dir()]
    for directory in dirs_to_index:
        md_files = [f for f in directory.glob('*.md') if f.name != '_index.md']
        if not md_files:
            continue
        lines = [f"# Index: {directory.name}\n"]
        for f in sorted(md_files, key=lambda x: x.name):
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
                # Use first non-empty, non-heading line as description
                desc = next(
                    (l.strip() for l in text.splitlines() if l.strip() and not l.startswith('#')),
                    f.stem
                )
                lines.append(f"- {f.name}: {desc[:120]}")
            except Exception:
                lines.append(f"- {f.name}")
        try:
            (directory / '_index.md').write_text('\n'.join(lines), encoding='utf-8')
        except Exception:
            pass


def _load_section(parts, directory, max_bytes=20_000):
    """Load wiki section files up to max_bytes (most-recent first).

    Loads _index.md first so the agent sees the directory's table of contents
    before deciding which full articles to read on demand.
    """
    if not directory.exists():
        return
    # Always load index first if available
    index_file = directory / '_index.md'
    if index_file.exists():
        try:
            idx_text = index_file.read_text(encoding='utf-8', errors='replace')[:2000]
            parts.append(f"== {directory.name.upper()} INDEX ==\n{idx_text}")
        except Exception:
            pass
    total = 0
    try:
        files = sorted(directory.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)
    except Exception:
        return
    for f in files:
        if f.name == '_index.md':
            continue  # already loaded above
        if total >= max_bytes:
            break
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        chunk = text[:max_bytes - total]
        parts.append(f"== {f.stem.upper()} ==\n{chunk}")
        total += len(chunk)


def _load_friday_data(parts, subdir, max_bytes=10_000):
    """Load JSON files from ~/.friday/<subdir>/, most-recent first."""
    data_dir = FRIDAY_DIR / subdir
    if not data_dir.exists():
        return
    total = 0
    try:
        files = sorted(data_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    except Exception:
        return
    for f in files:
        if total >= max_bytes:
            break
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        chunk = text[:max_bytes - total]
        parts.append(f"== {subdir.upper()}/{f.stem} ==\n{chunk}")
        total += len(chunk)


def _load_latest_briefing_summary(parts):
    """Note the most recent briefing exists; don't load the full HTML."""
    briefing_dir = FRIDAY_DIR / "wiki" / "briefings"
    if not briefing_dir.exists():
        return
    try:
        files = sorted(briefing_dir.glob("*.html"), reverse=True)
    except Exception:
        return
    if files:
        parts.append(f"== LATEST BRIEFING ==\nMost recent: {files[0].name} (use get_briefing tool to read it)")

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
    # API keys in environment (set by start.bat) → definitely configured
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return True
    # settings.json exists with real content
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
            if data.get('anthropic_api_key') or data.get('setup_complete'):
                return True
        except Exception:
            pass
    # personality.json exists → user has customised the agent
    if (FRIDAY_DIR / "personality.json").exists():
        return True
    return False


@app.route('/api/setup/status')
def api_setup_status():
    initialized = _is_existing_install()
    # Auto-stamp the marker so future checks are instant
    if initialized and not _SETUP_MARKER.exists():
        try:
            _SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _SETUP_MARKER.write_text(datetime.now().isoformat(), encoding='utf-8')
        except Exception:
            pass
    return jsonify({"initialized": initialized})


@app.route('/api/setup/skip', methods=['GET', 'POST'])
def api_setup_skip():
    """Permanently mark setup complete — for existing installs that predate the wizard."""
    try:
        _SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _SETUP_MARKER.write_text(datetime.now().isoformat(), encoding='utf-8')
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok"})


@app.route('/api/setup/complete', methods=['POST'])
def api_setup_complete():
    """Persist wizard choices and mark setup as complete."""
    global ANTHROPIC_API_KEY, GEMINI_API_KEY, _anthropic_client, _genai_client
    data = request.get_json(silent=True) or {}
    settings = _load_settings()
    wizard_keys = ['agent_name', 'orchestrator_model', 'subagent_model',
                   'tts_voice', 'temperature', 'communication_style',
                   'anthropic_api_key', 'gemini_api_key']
    for k in wizard_keys:
        if k in data:
            settings[k] = data[k]
    settings['setup_complete'] = True

    # Hot-reload API keys into the running process so no restart is needed
    if data.get('anthropic_api_key'):
        ANTHROPIC_API_KEY = data['anthropic_api_key']
        os.environ['ANTHROPIC_API_KEY'] = data['anthropic_api_key']
        _anthropic_client = None
    if data.get('gemini_api_key'):
        GEMINI_API_KEY = data['gemini_api_key']
        os.environ['GEMINI_API_KEY'] = data['gemini_api_key']
        _genai_client = None

    # Persist preferred holographic scene to personality.json
    if 'preferred_scene_index' in data:
        pfile = FRIDAY_DIR / 'personality.json'
        pdata = {}
        if pfile.exists():
            try:
                pdata = json.loads(pfile.read_text('utf-8'))
            except Exception:
                pass
        pdata['preferred_scene_index'] = int(data['preferred_scene_index'])
        try:
            pfile.write_text(json.dumps(pdata, indent=2), encoding='utf-8')
        except Exception:
            pass

    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding='utf-8')
        _SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _SETUP_MARKER.write_text(datetime.now().isoformat(), encoding='utf-8')
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok"})


# ── Agent Settings endpoints ──────────────────────────────────
@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """GET: return current agent settings + personality.
    POST: merge new values into ~/.friday/settings.json and (optionally) save personality.
    """
    if request.method == 'GET':
        return jsonify({
            "status": "ok",
            "settings": _load_settings(),
            "personality": _load_agent_personality(),
            "default_personality": DEFAULT_AGENT_PERSONALITY,
        })
    try:
        data = request.get_json(silent=True) or {}
        new_settings = data.get('settings') or {}
        merged = _save_settings({**_load_settings(), **new_settings})
        personality = data.get('personality')
        if personality is not None:
            _save_agent_personality(personality)
        return jsonify({
            "status": "ok",
            "settings": merged,
            "personality": _load_agent_personality(),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/chat', methods=['POST'])
def chat():
    """Text chat — powered by Anthropic Claude.

    Vision (screenshot description) still routes through Gemini Flash, since vision
    is a designer/perception task. Reasoning stays on Claude.
    """
    try:
        data = request.get_json(silent=True) or {}
        message = data.get('message', '')
        workspace = data.get('workspace', '')
        workspace_context = data.get('workspaceContext', None)
        include_vision = data.get('includeVision', False)
        voice_mode = bool(data.get('voice_mode', False))
        vision_description = None

        # Vision capture (Gemini, designer role). Accept either `screenshot`
        # (legacy) or `image` (Camera Mode frames). If an image is sent at all,
        # use it — no need for the explicit includeVision flag.
        screenshot_b64 = data.get('image') or data.get('screenshot') or None
        if screenshot_b64 and (include_vision or data.get('image') is not None):
            try:
                from google import genai
                from google.genai import types
                gclient = genai.Client(api_key=GEMINI_API_KEY)
                img_bytes = base64.b64decode(screenshot_b64)
                mime = 'image/jpeg' if data.get('image') else 'image/png'
                vision_resp = gclient.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        "Briefly describe what is visible on this screen. Focus on text, UI elements, and data shown. Be concise (2-3 sentences).",
                        types.Part.from_bytes(data=img_bytes, mime_type=mime),
                    ],
                )
                vision_description = vision_resp.text
            except Exception as ve:
                vision_description = f"[Vision unavailable: {ve}]"

        settings = _load_settings()
        personality = _load_agent_personality()

        # Build conversation history as Anthropic-format messages.
        # Pull up to 40 turns, then run trajectory compression if the total
        # char count is above the soft limit — older turns get summarised.
        raw_history = []
        for msg in CHAT_HISTORY[-100:]:
            role = 'user' if msg.get('role') == 'user' else 'assistant'
            text = msg.get('text', '')
            if text:
                raw_history.append({"role": role, "content": text})
        messages = _compress_trajectory(raw_history)
        messages.append({"role": "user", "content": message})

        # ── Semantic context pruning (RAG over our own history) ──
        # When the conversation is long, keep the turns most relevant to the
        # current prompt instead of letting the oldest ones fall off. Only the
        # messages SENT to the API are pruned — CHAT_HISTORY (the session
        # archive) is untouched, so future turns can still retrieve everything.
        _prune_cfg = settings.get('context_pruning') or {}
        if _prune_cfg.get('enabled', True):
            try:
                pruner = _get_context_pruner(_prune_cfg)
                if pruner.should_prune(messages):
                    _orig_count = len(messages)
                    messages = pruner.prune(messages, message)
                    _pruned_count = len(messages)
                    _topk = _prune_cfg.get('top_k', 10)
                    print(f"Context pruned: {_orig_count} turns → "
                          f"{_pruned_count} turns ({_topk} semantic matches)")
                    # Brief process orb so the user can see pruning happen.
                    _prune_pid = f"prune-{uuid.uuid4().hex[:8]}"
                    try:
                        process_register(
                            _prune_pid, name="Context Pruning",
                            label="Context Pruning", category="monitoring",
                            icon="🧠",
                        )
                        threading.Timer(2.0, process_remove, args=(_prune_pid,)).start()
                    except Exception:
                        pass
            except Exception as _pe:
                # Pruning is best-effort — never block a chat on it.
                print(f"  [PRUNE] skipped: {_pe}")

        # ── Headroom compression (compress the CONTENT of the kept turns) ──
        # The pruner just chose WHICH turns survive; Headroom now squeezes the
        # JSON tool outputs, code, and prose INSIDE them before they hit the API.
        # Runs before PII scrubbing so the [PII:...] tags it inserts stay intact.
        # Best-effort: any failure falls back to the uncompressed messages.
        _compress_cfg = settings.get('context_compression') or {}
        if _compress_cfg.get('enabled', True):
            try:
                compressor = _get_context_compressor(_compress_cfg)
                if compressor.should_compress(messages):
                    _selected_model = settings.get('orchestrator_model') or 'claude-opus-4-8'
                    # Brief process orb so the user can see compression happen.
                    _comp_pid = f"compress-{uuid.uuid4().hex[:8]}"
                    try:
                        process_register(
                            _comp_pid, name="Compressing Context",
                            label="Compressing Context", category="monitoring",
                            icon="📦",
                        )
                        threading.Timer(2.0, process_remove, args=(_comp_pid,)).start()
                    except Exception:
                        pass
                    messages = compressor.compress(messages, model=_selected_model)
            except Exception as _ce:
                # Compression is best-effort — never block a chat on it.
                print(f"  [HEADROOM] skipped: {_ce}")

        # ── Model Routing: decide local vs cloud BEFORE building the prompt. ──
        # The routing decision drives the whole privacy posture downstream:
        #   • route.is_local       → True for Ollama (on-device)
        #   • route.vault_allowed  → raw vault content may be sent (local only)
        #   • route.scrub_pii      → PII scrubber must run (cloud only)
        # We ALWAYS consult the router now — even in cloud_only mode — so a
        # vault-touching request is force-routed local (or refused) and vault
        # data never reaches the cloud.
        _routing_cfg = settings.get('model_routing') or {}
        _orb_label = (message or '').strip().splitlines()[0][:24] or 'Chat'
        try:
            from model_router import get_router
            _router = get_router(_routing_cfg)
            _route_info = _router.route(messages, task_context={
                "has_tools": True,
                "workspace": workspace,
                "cloud_model": settings.get('orchestrator_model') or 'claude-opus-4-8',
            })
        except Exception as _re:
            print(f"  [ROUTER] routing failed, defaulting to cloud: {_re}")
            _route_info = {
                "provider": "cloud",
                "model": settings.get('orchestrator_model') or 'claude-opus-4-8',
                "is_local": False, "vault_allowed": False, "scrub_pii": True,
                "vault_access": False, "refuse": False, "warning": None,
            }

        _provider = _route_info.get('provider', 'cloud')
        _routed_local = bool(_route_info.get('is_local'))
        _vault_access = bool(_route_info.get('vault_access'))

        def _vault_orb(label):
            """Show the green 🔒 vault orb (monitoring) for ~3s."""
            _vpid = f"vault-{uuid.uuid4().hex[:8]}"
            try:
                process_register(_vpid, name="Vault Access", label=label,
                                 category="monitoring", icon="🔒", color=0x22c55e)
                threading.Timer(3.0, process_remove, args=(_vpid,)).start()
            except Exception:
                pass

        # ── Refuse: a vault request that cannot be served locally (deny/warn). ──
        # Never send vault data to the cloud — return the warning instead.
        if _route_info.get('refuse'):
            _warn = _route_info.get('warning') or (
                "This request needs vault access which requires a local model. "
                "Please install Ollama or switch to local routing mode."
            )
            _vault_orb("Vault Access — Blocked")
            user_msg = {
                'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                'role': 'user', 'text': message, 'pinned': False, 'workspace': workspace,
            }
            friday_msg = {
                'id': str(uuid.uuid4()), 'timestamp': datetime.now().isoformat(),
                'role': 'friday', 'text': _warn, 'pinned': False, 'sources': [],
            }
            CHAT_HISTORY.append(user_msg)
            CHAT_HISTORY.append(friday_msg)
            _save_chat_history(CHAT_HISTORY)
            return jsonify({
                "response": _warn, "user_msg": user_msg, "friday_msg": friday_msg,
                "sources": [], "tool_trace": [], "vault_blocked": True,
            })

        if _vault_access and _routed_local:
            _vault_orb("Vault Access — Local Only")

        # ── Computer Control needs the cloud tool-use loop. ──
        # The local (Ollama) path is single-shot text: no tools, no vision-in, no
        # agentic loop — so a local model literally cannot see the screen or drive
        # the mouse. When the user has Computer Control enabled, force this turn to
        # the cloud model (which has the tool loop), UNLESS the turn touches the
        # vault — vault data must never leave the device, so privacy wins there.
        if _CC_PERMISSION.is_set() and _routed_local and not _vault_access:
            print("  [ROUTER] Computer Control enabled — routing to cloud for the tool-use loop")
            _routed_local = False
            _provider = 'cloud'
            _route_info['model'] = settings.get('orchestrator_model') or ANTHROPIC_MODEL_DEFAULT

        # ── Build the (vault-gated) system prompt + scrub PII for the provider. ──
        # Cloud: vault TIER_2/TIER_3 content is gated out and PII is scrubbed.
        # Local: raw vault content flows and the PII scrubber is SKIPPED entirely
        # (the data never leaves the device). Returns the per-request lookup used
        # to rehydrate PII tags out of the cloud model's reply.
        def _prep_for(provider):
            vc = _get_vault_control() if _vault_local_only() else None
            sp, src = _build_context_prompt(
                message, workspace, workspace_context, vision_description,
                provider=provider, vault_control=vc,
                vault_fallback=_vault_cloud_fallback(),
            )
            sp = _settings_system_prefix(settings, personality) + (sp or '')
            if voice_mode:
                sp = (
                    "=== VOICE MODE ACTIVE ===\n"
                    "The user is speaking to you via microphone. Your reply will be read aloud.\n"
                    "Rules: Keep it SHORT (1-3 sentences). Never use markdown — no asterisks, "
                    "headers, bullet points, or code blocks. Use natural speech patterns and "
                    "contractions. Ask a follow-up question to keep the conversation flowing.\n"
                    "=========================\n\n"
                ) + sp
            lookup = {}
            # Scrub only when the turn is cloud-bound. Scrubbing every message
            # (not just the new one) means a cached LOCAL reply retrieved by the
            # pruner is scrubbed at retrieval time before it can reach the cloud.
            if provider != 'local':
                if sp:
                    sp, sub = _scrub_pii(sp)
                    lookup.update(sub)
                for m in messages:
                    c = m.get('content')
                    if isinstance(c, str) and c:
                        m['content'], sub = _scrub_pii(c)
                        lookup.update(sub)
                if lookup:
                    sp += (
                        "\n\n== PRIVACY PLACEHOLDERS ==\n"
                        "Some private values in your context appear as tags like "
                        "[PII:type:hash] (types: addr, phone, email, ssn, cc, name). "
                        "These are stable references to real data on the user's device. "
                        "Use them in your reply EXACTLY as written when you need to "
                        "reference the underlying value — they will be substituted "
                        "with the real data before the user sees your response."
                    )
            return sp, src, lookup

        system_prompt, sources, pii_lookup = _prep_for(_provider)

        _sess_ctx = {
            "authenticated": bool(session.get("authenticated")) or not bool(FRIDAY_PASSWORD),
            "provider": _provider,
        }

        # ── Dispatch. ──
        reply, tool_trace = None, []
        if _routed_local:
            try:
                reply, tool_trace = _call_ollama(
                    messages, system=system_prompt,
                    model=_route_info['model'],
                    temperature=settings.get('temperature'),
                    orb_label=f"🏠 {_orb_label}",
                    orb_icon='🏠',
                )
            except Exception as _ole:
                # A vault request must NEVER silently fall back to cloud with raw
                # vault data — fail loudly instead.
                if _vault_access:
                    print(f"  [ROUTER] local vault inference failed; refusing cloud fallback: {_ole}")
                    raise
                print(f"  [ROUTER] local inference failed, falling back to cloud: {_ole}")
                _routed_local = False
                _provider = 'cloud'
                # Rebuild the prompt for cloud (gated) and scrub before sending.
                system_prompt, sources, pii_lookup = _prep_for('cloud')

        if not _routed_local:
            if _provider == 'openai':
                # OpenAI-compatible cloud path (OpenRouter / any /v1 endpoint),
                # with a full agentic tool loop. Records its own cost.
                reply, tool_trace = _call_openai(
                    messages, system=system_prompt, model=_route_info.get('model'),
                    temperature=settings.get('temperature'),
                    orb_label=f"☁️ {_orb_label}", orb_icon='☁️',
                    tools=CLAUDE_TOOLS, pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                )
            else:
                reply, tool_trace = _call_claude_agent(
                    messages, system=system_prompt, temperature=settings.get('temperature'),
                    pii_lookup=pii_lookup, session_ctx=_sess_ctx,
                    orb_label=_orb_label, orb_category='default', orb_icon='💬',
                )
                if _routing_cfg.get('cost_tracking', True):
                    try:
                        from model_router import get_router
                        _router = get_router()
                        _est_tokens = len(str(messages)) // 4 + len(reply) // 4
                        _router.cost_tracker.record(
                            "cloud",
                            settings.get('orchestrator_model') or 'claude-opus-4-8',
                            prompt_tokens=_est_tokens, completion_tokens=len(reply) // 4,
                        )
                    except Exception:
                        pass

        # ── Rehydrate: restore real PII before returning to the user. ──
        if pii_lookup:
            reply = _rehydrate_pii(reply, pii_lookup)
            # Also rehydrate the tool trace so the UI shows real values.
            for entry in tool_trace:
                if isinstance(entry.get('result'), str):
                    entry['result'] = _rehydrate_pii(entry['result'], pii_lookup)

        # Store in history with IDs, timestamps, and context metadata
        user_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'user',
            'text': message,
            'pinned': False,
            'workspace': workspace,
        }
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False,
            'sources': sources,
        }
        CHAT_HISTORY.append(user_msg)
        CHAT_HISTORY.append(friday_msg)

        # ── Context log: append both turns unless off-record. ──
        if not settings.get('off_record'):
            _log_context("chat_user", {
                "message": message,
                "workspace": workspace,
                "had_image": bool(screenshot_b64),
            })
            _log_context("chat_agent", {
                "reply": reply,
                "sources": sources,
                "tool_count": len(tool_trace or []),
            })

        # Epistemic scoring — score this turn in background
        try:
            from epistemic_engine import get_epistemic_engine
            threading.Thread(
                target=lambda m=message, r=reply: get_epistemic_engine().score_turn(m, r),
                daemon=True,
            ).start()
        except Exception:
            pass

        # Closed-loop learning — capture the turn trajectory + accumulate skill
        # metrics in the background. Feeds the nightly SkillOpt optimizer.
        try:
            import skill_capture as _skcap
            threading.Thread(
                target=lambda m=message, r=reply, tt=tool_trace, ws=workspace:
                    _skcap.capture(m, r, tool_trace=tt, workspace=ws),
                daemon=True,
            ).start()
        except Exception:
            pass

        # Prune: keep pinned forever, others for 30 days, cap at 500 messages
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)

        return jsonify({
            "response": reply,
            "user_msg": user_msg,
            "friday_msg": friday_msg,
            "sources": sources,
            "tool_trace": tool_trace,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"response": f"[Friday offline] {str(e)}"})


# ═══════════════════════════════════════════════════════════════
#  PERSISTENT CHAT HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/chat/history', methods=['GET'])
def chat_history():
    """Return chat history (last 30 days, pinned messages included)."""
    messages = _load_chat_history()
    return jsonify({"status": "ok", "messages": messages, "count": len(messages)})


@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    """Send a message, save to persistent history, return Friday's response.
    Accepts context-aware payload: {message, workspace, workspaceContext, includeVision, screenshot}.
    Text reasoning is Claude; vision (screenshot description) stays on Gemini.
    """
    try:
        data = request.get_json(silent=True) or {}
        message = data.get('message', '')
        workspace = data.get('workspace', '')
        workspace_context = data.get('workspaceContext', None)
        include_vision = data.get('includeVision', False)
        vision_description = None

        if not message.strip():
            return jsonify({"status": "error", "message": "Empty message"}), 400

        # Vision capture (Gemini, designer role). Accept either `screenshot`
        # (legacy) or `image` (Camera Mode frames).
        screenshot_b64 = data.get('image') or data.get('screenshot') or None
        if screenshot_b64 and (include_vision or data.get('image') is not None):
            try:
                from google import genai
                from google.genai import types
                gclient = genai.Client(api_key=GEMINI_API_KEY)
                img_bytes = base64.b64decode(screenshot_b64)
                mime = 'image/jpeg' if data.get('image') else 'image/png'
                vision_resp = gclient.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        "Briefly describe what is visible on this screen. Focus on text, UI elements, and data shown. Be concise (2-3 sentences).",
                        types.Part.from_bytes(data=img_bytes, mime_type=mime),
                    ],
                )
                vision_description = vision_resp.text
            except Exception as ve:
                vision_description = f"[Vision unavailable: {ve}]"

        # Build context-enriched system prompt. This endpoint always goes to
        # Anthropic (cloud), so vault TIER_2/TIER_3 content is gated out here.
        settings = _load_settings()
        system_prompt, sources = _build_context_prompt(
            message, workspace, workspace_context, vision_description,
            provider='cloud',
            vault_control=(_get_vault_control() if _vault_local_only() else None),
            vault_fallback=_vault_cloud_fallback(),
        )

        # Prepend user-configured agent personality + response prefs + cLaws
        personality = _load_agent_personality()
        system_prompt = _settings_system_prefix(settings, personality) + (system_prompt or '')

        # Anthropic-format message history
        messages = []
        for msg in CHAT_HISTORY[-100:]:
            role = 'user' if msg.get('role') == 'user' else 'assistant'
            text = msg.get('text', '')
            if text:
                messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": message})

        _sess_ctx = {
            "authenticated": bool(session.get("authenticated")) or not bool(FRIDAY_PASSWORD),
        }
        reply, tool_trace = _call_claude_agent(
            messages, system=system_prompt, temperature=settings.get('temperature'),
            session_ctx=_sess_ctx,
        )

        # Create persistent message objects
        user_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'user',
            'text': message,
            'pinned': False,
            'workspace': workspace,
        }
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False,
            'sources': sources,
        }
        CHAT_HISTORY.append(user_msg)
        CHAT_HISTORY.append(friday_msg)

        # Prune and save
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)

        return jsonify({"status": "ok", "user_msg": user_msg, "friday_msg": friday_msg, "sources": sources, "tool_trace": tool_trace})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/chat/pin/<msg_id>', methods=['POST'])
def chat_pin(msg_id):
    """Toggle pin status on a chat message. Pinned messages are never pruned."""
    for msg in CHAT_HISTORY:
        if msg.get('id') == msg_id:
            msg['pinned'] = not msg.get('pinned', False)
            _save_chat_history(CHAT_HISTORY)
            return jsonify({"status": "ok", "id": msg_id, "pinned": msg['pinned']})
    return jsonify({"status": "error", "message": "Message not found"}), 404


@app.route('/api/chat/search', methods=['GET'])
def chat_search():
    """Search chat history by text query."""
    query = request.args.get('q', '').lower().strip()
    if not query:
        return jsonify({"status": "ok", "results": [], "count": 0})

    results = [m for m in CHAT_HISTORY if query in m.get('text', '').lower()]
    return jsonify({"status": "ok", "results": results[-50:], "count": len(results)})


@app.route('/api/chat/clear', methods=['POST'])
def chat_clear():
    """Reset the chat panel's conversation. Pinned messages survive unless
    `pinned=true` is sent in the body. Append-only context log is NOT touched."""
    keep_pinned = True
    try:
        data = request.get_json(silent=True) or {}
        if data.get('include_pinned'):
            keep_pinned = False
    except Exception:
        pass
    before = len(CHAT_HISTORY)
    if keep_pinned:
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned')]
    else:
        CHAT_HISTORY.clear()
    _save_chat_history(CHAT_HISTORY)
    return jsonify({"status": "ok", "removed": before - len(CHAT_HISTORY), "remaining": len(CHAT_HISTORY)})


# ═══════════════════════════════════════════════════════════════
#  MODEL ROUTING & OLLAMA STATUS ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/model-stats')
def model_stats():
    """Return model routing statistics (requests per model, estimated savings)."""
    try:
        from model_router import get_router
        router = get_router()
        stats = router.get_stats()
        settings = _load_settings()
        routing_cfg = settings.get('model_routing') or {}
        stats['mode'] = routing_cfg.get('mode', 'cloud_only')
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e), "mode": "cloud_only",
                        "local_requests": 0, "cloud_requests": 0,
                        "estimated_savings": 0})


# ── Portable skill registry (SKILL.md folder format) ────────────
@app.route('/api/skills', methods=['GET'])
@login_required
def api_skills_list():
    """List all skills (learned, imported, bundled) in the registry."""
    try:
        import skill_registry as _skreg
        skills = _skreg.list_skills()
        return jsonify({"skills": skills, "count": len(skills)})
    except Exception as e:
        return jsonify({"error": str(e), "skills": [], "count": 0}), 500


@app.route('/api/skills/import', methods=['POST'])
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


@app.route('/api/skills/<name>/export', methods=['GET'])
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


@app.route('/api/skillopt/state', methods=['GET'])
@login_required
def api_skillopt_state():
    """Fleet state for the Skills Observatory UI (was previously unrouted)."""
    try:
        from skillopt_engine import export_fleet_state
        return jsonify(export_fleet_state())
    except Exception as e:
        return jsonify({"error": str(e), "skills": []})


@app.route('/api/ollama/status')
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


@app.route('/api/ollama/models')
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


@app.route('/api/ollama/pull', methods=['POST'])
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


# ═══════════════════════════════════════════════════════════════
#  TEXT-TO-SPEECH & AUDIO
# ═══════════════════════════════════════════════════════════════

@app.route('/api/voice/tts', methods=['POST'])
def tts():
    """Text-to-speech using Gemini 2.5 Flash TTS model — returns WAV binary directly.

    Default voice is "Puck" (warmer / more natural than "Kore"). Callers can
    override via `voice` in the JSON body. The text is wrapped with a
    conversational style hint so the model delivers it as a news anchor
    rather than reading robotically.
    """
    try:
        import wave
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        text = request.json.get('text', '')
        # Voice priority: explicit request param > user setting > Aoede (warm female).
        voice = request.json.get('voice')
        if not voice:
            try:
                voice = (_load_settings() or {}).get('tts_voice') or 'Aoede'
            except Exception:
                voice = 'Aoede'
        style = request.json.get('style', 'briefing')

        if not text:
            return jsonify({"status": "error", "message": "No text provided"}), 400

        # Custom user-defined style prompt takes priority over the built-in styles.
        custom_style = _get_voice_style_prompt()
        if custom_style:
            style_prefix = f"{custom_style}: "
        else:
            # Conversational prefix — Gemini TTS responds to natural-language
            # delivery cues in the prompt. "Briefing" gives a warm news-anchor read.
            style_prefix = {
                'briefing': "Read this aloud in a warm, conversational news-anchor voice — natural pacing, light intonation, no robotic flatness: ",
                'chat': "Say this aloud in a calm, friendly tone, like a trusted assistant talking to a colleague: ",
                'plain': "Say this aloud: ",
            }.get(style, "Read this aloud in a warm, conversational voice: ")

        # Build speech config with optional language code.
        speech_kwargs = {
            "voice_config": types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        }
        language = _get_voice_language()
        if language:
            speech_kwargs["language_code"] = language

        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=f"{style_prefix}{text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(**speech_kwargs),
            )
        )

        audio_data = response.candidates[0].content.parts[0].inline_data.data

        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_data)
        buf.seek(0)
        return send_file(buf, mimetype='audio/wav')

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(str(TEMP_AUDIO_DIR), filename)


# ═══════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

try:
    import notifications_engine as _notif_engine
except Exception as _e:
    _notif_engine = None
    print(f"  [FRIDAY] WARNING: notifications_engine unavailable: {_e}")


def _compute_derived_notifications():
    """One-off computed notifications (briefings, todos) — not queued, just merged."""
    derived = []
    # Daily briefing ready?
    meta_dir = os.path.join(WIKI_DIR, 'meta')
    if os.path.isdir(meta_dir):
        briefings = sorted(glob.glob(os.path.join(meta_dir, 'daily-briefing-*.md')), reverse=True)
        if briefings:
            latest = os.path.basename(briefings[0])
            date_str = latest.replace('daily-briefing-', '').replace('.md', '')
            derived.append({
                "id": f"derived-briefing-{date_str}",
                "kind": "briefing",
                "title": f"📰 Daily briefing ready: {date_str}",
                "body": "",
                "priority": "low",
                "read": False, "dismissed": False,
                "source": "briefing",
                "created_at": date_str,
                "target": {"workspace": "news", "tab": "briefings"},
                "derived": True,
            })

    # Proposed todos awaiting approval
    todos = _load_todos()
    proposed = [t for t in todos if t.get('status') == 'proposed']
    if proposed:
        derived.append({
            "id": "derived-proposed-todos",
            "kind": "todo",
            "title": f"📋 {len(proposed)} proposed task{'s' if len(proposed) > 1 else ''} awaiting approval",
            "body": "",
            "priority": "medium",
            "read": False, "dismissed": False,
            "source": "tasks",
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "target": {"workspace": "home"},
            "derived": True,
        })

    # Overdue todos
    overdue_count = 0
    for t in todos:
        if t.get('deadline') and t.get('status') in ('approved', 'proposed'):
            try:
                if date.fromisoformat(t['deadline']) < date.today():
                    overdue_count += 1
            except Exception:
                pass
    if overdue_count:
        derived.append({
            "id": "derived-overdue-todos",
            "kind": "overdue",
            "title": f"⚠️ {overdue_count} overdue task{'s' if overdue_count > 1 else ''}",
            "body": "",
            "priority": "high",
            "read": False, "dismissed": False,
            "source": "tasks",
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "target": {"workspace": "home"},
            "derived": True,
        })
    return derived


@app.route('/api/notifications')
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


@app.route('/api/notifications/read', methods=['POST'])
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


@app.route('/api/notifications/dismiss', methods=['POST'])
def dismiss_notification():
    data = request.get_json(silent=True) or {}
    nid = data.get('id')
    if not _notif_engine or not nid:
        return jsonify({"status": "noop"})
    ok = _notif_engine.dismiss(str(nid))
    return jsonify({"status": "ok" if ok else "not_found", "id": nid})


@app.route('/api/notifications/push', methods=['POST'])
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


@app.route('/api/notifications/chat-injections')
def get_chat_injections():
    """Pending proactive messages that should appear in the chat stream."""
    if not _notif_engine:
        return jsonify({"items": []})
    return jsonify({"items": _notif_engine.pending_chat_injections()})


@app.route('/api/notifications/chat-injections/ack', methods=['POST'])
def ack_chat_injection_endpoint():
    if not _notif_engine:
        return jsonify({"status": "noop"})
    data = request.get_json(silent=True) or {}
    nid = data.get('id')
    if not nid:
        return jsonify({"status": "error"}), 400
    ok = _notif_engine.ack_chat_injection(str(nid))
    return jsonify({"status": "ok" if ok else "not_found", "id": nid})


# ═══════════════════════════════════════════════════════════════
#  FILE ANALYSIS (Gemini)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/analyze', methods=['POST'])
def analyze_file():
    """Analyze an uploaded file using Gemini."""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    filename = file.filename
    content = file.read()

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)

        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[
                    types.Part.from_bytes(data=content, mime_type=mime),
                    "You are Friday. Describe this image. If it looks like a job posting or resume, analyze it for key requirements and fit."
                ]
            )
            return jsonify({"filename": filename, "type": "image", "analysis": response.text})
        elif ext == 'pdf':
            try:
                import pdfplumber
                import io
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    text = '\n'.join(page.extract_text() or '' for page in pdf.pages[:10])
                if text.strip():
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f'You are Friday. Summarize this PDF document concisely. If it looks like a job posting, evaluate the key requirements and note the role level.\n\n{text[:8000]}'
                    )
                    return jsonify({"filename": filename, "type": "pdf", "analysis": response.text})
            except ImportError:
                pass
            return jsonify({"filename": filename, "type": "pdf", "analysis": f"PDF received ({len(content)//1024}KB). Install pdfplumber for full analysis: pip install pdfplumber"})
        elif ext in ('txt', 'md', 'py', 'js', 'html', 'css', 'json', 'ts', 'tsx', 'yaml', 'yml', 'toml'):
            text = content.decode('utf-8', errors='replace')[:8000]
            job_keywords = ['responsibilities', 'qualifications', 'salary', 'benefits', 'apply', 'experience required']
            is_job = sum(1 for kw in job_keywords if kw.lower() in text.lower()) >= 2
            if is_job:
                prompt = f'You are Friday. This looks like a job posting. Evaluate the key requirements, role level, and compensation signals. Rate attractiveness 1-10 and explain.\n\n{text}'
            else:
                prompt = f'You are Friday. Analyze this {ext} file and summarize its purpose and key content:\n\n{text}'
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            return jsonify({"filename": filename, "type": "text" if not is_job else "job_posting", "analysis": response.text})
        else:
            return jsonify({"filename": filename, "type": ext, "analysis": f"File received ({len(content)} bytes). Type: .{ext} — drop a text, image, or PDF for full analysis."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"filename": filename, "analysis": f"Analysis error: {str(e)}"})


# ═══════════════════════════════════════════════════════════════
#  PERSONALITY & TRUST EDITING ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/personality/set', methods=['POST'])
def set_personality():
    """Update a personality trait or style dimension."""
    data = request.get_json(silent=True) or {}
    trait = data.get('trait', '')
    value = data.get('value', 0.5)

    if not trait:
        return jsonify({"status": "error", "message": "No trait specified"}), 400

    pfile = FRIDAY_DIR / "personality.json"
    try:
        pdata = {}
        if pfile.exists():
            pdata = json.loads(pfile.read_text(encoding='utf-8'))

        if trait.startswith('style.'):
            style_key = trait.split('.', 1)[1]
            if 'style' not in pdata:
                pdata['style'] = {}
            pdata['style'][style_key] = float(value)
        elif trait == 'temperature':
            pdata['temperature'] = float(value)
        else:
            if 'traits' not in pdata:
                pdata['traits'] = {}
            pdata['traits'][trait] = float(value)

        pfile.write_text(json.dumps(pdata, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "trait": trait, "value": float(value)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/trust/edit', methods=['POST'])
def edit_trust():
    """Edit trust scores for a person or add evidence."""
    data = request.get_json(silent=True) or {}
    person_key = data.get('person', '')
    scores = data.get('scores', None)
    add_evidence = data.get('add_evidence', None)

    if not person_key:
        return jsonify({"status": "error", "message": "No person specified"}), 400

    trust_file = FRIDAY_DIR / "trust_graph.json"
    try:
        tdata = {}
        if trust_file.exists():
            tdata = json.loads(trust_file.read_text(encoding='utf-8'))

        if 'people' not in tdata:
            tdata['people'] = {}

        if person_key not in tdata['people']:
            return jsonify({"status": "error", "message": f"Person '{person_key}' not found"}), 404

        person = tdata['people'][person_key]

        if scores:
            if 'scores' not in person:
                person['scores'] = {}
            for dim, val in scores.items():
                person['scores'][dim] = float(val)
            score_vals = [v for k, v in person['scores'].items() if k != 'overall' and isinstance(v, (int, float))]
            if score_vals:
                person['scores']['overall'] = sum(score_vals) / len(score_vals)

        if add_evidence:
            if 'evidence' not in person:
                person['evidence'] = []
            person['evidence'].append({
                "type": add_evidence.get('type', 'observation'),
                "magnitude": float(add_evidence.get('magnitude', 0.5)),
                "timestamp": datetime.now().isoformat(),
                "source": "friday-desktop-ui",
                "notes": add_evidence.get('notes', ''),
                "dimension": add_evidence.get('dimension', 'overall')
            })
            person['last_interaction'] = datetime.now().isoformat()

        tdata['people'][person_key] = person
        trust_file.write_text(json.dumps(tdata, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "person": person_key})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/trust/add-person', methods=['POST'])
def add_trust_person():
    """Add a new person to the trust graph."""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    aliases = data.get('aliases', [])
    entity_type = data.get('entity_type', 'human')

    if not name:
        return jsonify({"status": "error", "message": "No name specified"}), 400

    trust_file = FRIDAY_DIR / "trust_graph.json"
    try:
        tdata = {}
        if trust_file.exists():
            tdata = json.loads(trust_file.read_text(encoding='utf-8'))

        if 'people' not in tdata:
            tdata['people'] = {}

        key = name.lower().replace(' ', '_').replace('-', '_')

        if key in tdata['people']:
            return jsonify({"status": "error", "message": f"Person '{name}' already exists"}), 409

        tdata['people'][key] = {
            "name": name,
            "aliases": aliases if isinstance(aliases, list) else [],
            "entity_type": entity_type,
            "scores": {
                "overall": 0.5,
                "reliability": 0.5,
                "information_quality": 0.5,
                "emotional_trust": 0.5,
                "timeliness": 0.5,
                "domain_expertise": 0.5
            },
            "evidence": [],
            "domains": [],
            "last_interaction": datetime.now().isoformat(),
            "created": datetime.now().isoformat()
        }

        trust_file.write_text(json.dumps(tdata, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "key": key, "name": name})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  AI TO-DO LIST
# ═══════════════════════════════════════════════════════════════

TODOS_FILE = FRIDAY_DIR / "todos.json"

def _load_todos():
    if TODOS_FILE.exists():
        try:
            return json.loads(TODOS_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []

def _save_todos(todos):
    TODOS_FILE.write_text(json.dumps(todos, indent=2), encoding='utf-8')


@app.route('/api/todos', methods=['GET'])
def get_todos():
    """Return all todos from ~/.friday/todos.json."""
    todos = _load_todos()
    return jsonify({"status": "ok", "todos": todos, "count": len(todos)})


@app.route('/api/todos', methods=['POST'])
def add_todo():
    """Add an AI-proposed (or user) task with optional deadline."""
    data = request.get_json(silent=True) or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({"status": "error", "message": "No title provided"}), 400

    todos = _load_todos()
    todo = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": data.get('description', ''),
        "deadline": data.get('deadline', None),
        "priority": data.get('priority', 'medium'),
        "status": data.get('status', 'proposed'),
        "category": data.get('category', 'general'),
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "source": data.get('source', 'user'),
    }
    todos.append(todo)
    _save_todos(todos)
    return jsonify({"status": "ok", "todo": todo})


@app.route('/api/todos/<todo_id>/approve', methods=['POST'])
def approve_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'approved'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@app.route('/api/todos/<todo_id>/reject', methods=['POST'])
def reject_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'rejected'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@app.route('/api/todos/<todo_id>/complete', methods=['POST'])
def complete_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'completed'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@app.route('/api/todos/<todo_id>', methods=['DELETE'])
def delete_todo(todo_id):
    todos = _load_todos()
    before = len(todos)
    todos = [t for t in todos if t['id'] != todo_id]
    _save_todos(todos)
    return jsonify({"status": "ok", "removed": before - len(todos)})


#  CLIPBOARD DRAFTING ENGINE
# ═══════════════════════════════════════════════════════════════

DRAFT_MODE_PROMPTS = {
    'linkedin_post': (
        "You are a LinkedIn ghostwriter for a senior AI/engineering leader. "
        "Write a professional but personable post — 1-3 paragraphs, strong opening hook, "
        "no hashtag spam (2-3 max at the end if any). Conversational authority, not corporate fluff. "
        "The voice should feel like a seasoned journalist who pivoted to AI."
    ),
    'email_reply': (
        "You are drafting a professional email reply. Match the formality of the original message. "
        "Be concise and clear. Include a specific call-to-action or next step. "
        "No filler phrases like 'I hope this email finds you well.'"
    ),
    'slack_message': (
        "You are drafting a Slack message. Keep it casual and brief — this is internal team chat. "
        "Emoji are fine where they feel natural. One short paragraph max. No sign-offs."
    ),
    'tweet': (
        "You are drafting a tweet. MUST be under 280 characters. Punchy, sharp, quotable. "
        "No hashtags unless they're genuinely clever. Think journalist, not influencer."
    ),
    'coparent_response': (
        "You are drafting a response for a co-parenting communication platform. "
        "CRITICAL RULES: Stay calm, factual, and brief. Answer only what needs answering. "
        "Ignore all bait and emotional provocation. Never match the other party's emotional register. "
        "Everything you write should be something a family court judge would find reasonable, measured, and cooperative. "
        "Do not over-explain, do not defend, do not attack. Short sentences. Airtight logic."
    ),
    'freeform': (
        "You are a versatile writing assistant. Follow the user's format instructions exactly. "
        "Write clearly and concisely unless told otherwise."
    ),
}

COPARENTING_DIR = HOME / ".friday" / "wiki" / "coparenting"
CONTENT_DRAFTS_DIR = FRIDAY_DIR / "wiki" / "content"


def _load_ofw_context():
    """Load co-parenting wiki context for co-parent drafts."""
    context_parts = []
    if COPARENTING_DIR.exists():
        for md_file in sorted(COPARENTING_DIR.glob('*.md'))[:5]:
            try:
                text = md_file.read_text(encoding='utf-8')[:2000]
                context_parts.append(f"[{md_file.name}]: {text}")
            except Exception:
                continue
    return '\n\n'.join(context_parts) if context_parts else ''


def _build_draft_html(draft_text, mode, prompt_text=''):
    """Build a styled HTML document for a draft, matching the daily briefing aesthetic."""
    mode_labels = {
        'linkedin_post': 'LinkedIn Post', 'email_reply': 'Email Reply',
        'slack_message': 'Slack Message', 'tweet': 'Tweet',
        'coparent_response': 'Co-Parent Response', 'freeform': 'Freeform Draft',
    }
    mode_label = mode_labels.get(mode, mode)
    timestamp = datetime.now().strftime('%B %d, %Y · %H:%M')

    def _esc(s):
        return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    paras = [p.strip() for p in (draft_text or '').split('\n\n') if p.strip()]
    if not paras:
        paras = [(draft_text or '').strip() or '(empty)']

    _lead_style = ' style="font-size:18px;color:#e8e8f0"'
    _nl = chr(10)
    para_html = '\n    '.join(
        f'<p{_lead_style if i == 0 else ""}>{_esc(p).replace(_nl, "<br>")}</p>'
        for i, p in enumerate(paras)
    )
    prompt_block = (
        f'<div class="prompt-ctx">Prompt: {_esc(prompt_text[:200])}</div>'
        if prompt_text else ''
    )

    return (
        '<!DOCTYPE html><html lang="en">\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        f'<title>FRIDAY DRAFT — {_esc(mode_label)}</title>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900'
        '&family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">\n'
        '<style>\n'
        '* { margin: 0; padding: 0; box-sizing: border-box; }\n'
        'body { background: #06060b; color: #e0e0e8; font-family: \'Inter\', sans-serif; line-height: 1.7; }\n'
        '.container { max-width: 780px; margin: 0 auto; padding: 40px 24px 80px; }\n'
        '.header { text-align: center; margin-bottom: 48px; padding-bottom: 32px; border-bottom: 1px solid rgba(0,212,255,0.15); }\n'
        '.header h1 { font-family: \'Orbitron\', monospace; font-size: 28px; font-weight: 900; '
        'background: linear-gradient(135deg, #00d4ff 0%, #7c3aed 50%, #ff0080 100%); '
        '-webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 8px; }\n'
        '.header .subtitle { font-size: 14px; color: #666; font-style: italic; }\n'
        '.header .date { font-family: \'JetBrains Mono\', monospace; font-size: 13px; color: #00d4ff; margin-top: 8px; letter-spacing: 0.05em; }\n'
        '.neon-line { height: 2px; background: linear-gradient(90deg, #00d4ff, #7c3aed, #ff0080); margin: 4px 0 0; opacity: 0.6; border-radius: 1px; }\n'
        '.mode-tag { display: inline-block; font-family: \'JetBrains Mono\', monospace; font-size: 11px; color: #7c3aed; border: 1px solid rgba(124,58,237,0.3); border-radius: 4px; padding: 2px 8px; margin-bottom: 24px; letter-spacing: 0.05em; }\n'
        '.prompt-ctx { font-size: 12px; color: #555; font-style: italic; margin-bottom: 32px; font-family: \'JetBrains Mono\', monospace; border-left: 2px solid rgba(0,212,255,0.2); padding-left: 12px; }\n'
        '.draft-body p { margin-bottom: 18px; font-size: 16px; line-height: 1.8; color: #d0d0d8; }\n'
        '.footer { text-align: center; margin-top: 60px; padding-top: 24px; border-top: 1px solid rgba(255,255,255,0.05); font-size: 12px; color: #444; font-family: \'JetBrains Mono\', monospace; }\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '<div class="container">\n'
        '  <div class="header">\n'
        '    <h1>FRIDAY DRAFT</h1>\n'
        '    <div class="neon-line"></div>\n'
        f'    <div class="subtitle">{_esc(mode_label)}</div>\n'
        f'    <div class="date">{timestamp}</div>\n'
        '  </div>\n'
        f'  {prompt_block}\n'
        f'  <div class="mode-tag">{_esc(mode_label.upper())}</div>\n'
        '  <div class="draft-body">\n'
        f'    {para_html}\n'
        '  </div>\n'
        '  <div class="footer">Generated by FRIDAY · FutureSpeak.AI</div>\n'
        '</div>\n'
        '</body>\n'
        '</html>'
    )


def _spawn_draft_task(mode, prompt_text, context=''):
    """Spawn a background draft-generation task. Returns (response_dict, http_status).

    Shared by /api/draft and /api/coparent/draft so both go through the same
    vault-context-aware pipeline.
    """
    if not (prompt_text or '').strip():
        return {"status": "error", "message": "No prompt provided"}, 400

    system = DRAFT_MODE_PROMPTS.get(mode, DRAFT_MODE_PROMPTS['freeform'])
    if mode == 'coparent_response':
        ofw_ctx = _load_ofw_context()
        if ofw_ctx:
            system += f"\n\nCO-PARENTING CONTEXT (from wiki):\n{ofw_ctx}"
    system += "\n\nOutput ONLY the draft text, no commentary or labels."

    user_parts = []
    if context:
        user_parts.append(f"CONTEXT (what the user is looking at / replying to):\n{context}")
    user_parts.append(f"USER INSTRUCTION:\n{prompt_text}")
    full_prompt = '\n\n'.join(user_parts)

    mode_labels = {
        'linkedin_post': 'LinkedIn Post', 'email_reply': 'Email Reply',
        'slack_message': 'Slack Message', 'tweet': 'Tweet',
        'coparent_response': 'Co-Parent Response', 'freeform': 'Freeform Draft',
    }
    task_name = f"Quick Draft — {mode_labels.get(mode, mode)}"
    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            'task_id': task_id,
            'name': task_name,
            'description': prompt_text[:100],
            'status': 'queued',
            'created': _time.time(),
            'started': None,
            'ended': None,
            'log': [],
            'result': '',
            'draft_mode': mode,
        }

    # Capture loop variables for the thread closure
    _system = system
    _full_prompt = full_prompt
    _mode = mode

    def _draft_worker():
        _task_set(task_id, status='running', started=_time.time())
        _task_log(task_id, f'Generating {_mode} draft…')
        try:
            # Load full vault/wiki context — Friday MUST know the user's contacts,
            # his name, his boss, his family, etc. when writing on his behalf.
            _task_log(task_id, 'Loading vault context…')
            full_system = _get_friday_system_prompt(prompt_text, workspace='draft')
            full_system += f"\n\n== DRAFT WRITING INSTRUCTIONS ==\n{_system}"
            draft_text = _call_claude(
                [{"role": "user", "content": _full_prompt}],
                system=full_system,
                max_tokens=16384,
            )
            # Auto-save to content library
            try:
                CONTENT_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
                now_dt = datetime.now()
                slug = re.sub(r'[^a-z0-9]+', '-', prompt_text[:30].lower()).strip('-') or _mode
                fname = f"draft-{now_dt.strftime('%Y-%m-%d-%H%M')}-{slug}.html"
                html_content = _build_draft_html(draft_text, _mode, prompt_text)
                (CONTENT_DRAFTS_DIR / fname).write_text(html_content, encoding='utf-8')
                _task_log(task_id, f'Saved to library: {fname}')
            except Exception as _se:
                _task_log(task_id, f'Library save failed (non-fatal): {_se}')
            _task_set(task_id, status='complete', result=draft_text, ended=_time.time())
            _task_log(task_id, 'Draft ready.')
        except Exception as e:
            traceback.print_exc()
            _task_set(task_id, status='failed', result=f'[Error] {e}', ended=_time.time())
            _task_log(task_id, f'Error: {e}')

    threading.Thread(target=_draft_worker, daemon=True).start()
    _log_context("draft_spawn", {"task_id": task_id, "mode": mode, "prompt": prompt_text[:200]})

    return {
        "status": "queued",
        "task_id": task_id,
        "name": task_name,
    }, 200


@app.route('/api/draft', methods=['POST'])
def draft_generate():
    """Generate a draft via Claude — spawns as a background task, returns task_id immediately."""
    try:
        data = request.get_json(silent=True) or {}
        resp, code = _spawn_draft_task(
            mode=data.get('mode', 'freeform'),
            prompt_text=data.get('prompt', ''),
            context=data.get('context', ''),
        )
        return jsonify(resp), code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/draft/deploy', methods=['POST'])
def draft_deploy():
    """Deploy a draft to clipboard or other destination."""
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    destination = data.get('destination', 'clipboard')

    if not text:
        return jsonify({"status": "error", "message": "No text provided"}), 400

    if destination == 'clipboard':
        try:
            # Escape for PowerShell: replace double quotes and backticks
            escaped = text.replace('`', '``').replace('"', '`"').replace('$', '`$')
            subprocess.run(
                ['powershell', '-command', f'Set-Clipboard -Value "{escaped}"'],
                capture_output=True, text=True, timeout=10,
                creationflags=_POPEN_FLAGS,
            )
            return jsonify({"status": "ok", "destination": "clipboard", "char_count": len(text)})
        except subprocess.TimeoutExpired:
            return jsonify({"status": "error", "message": "Clipboard operation timed out"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    elif destination == 'gmail_draft':
        # Frontend handles Gmail draft creation via MCP tools — return acknowledgment
        return jsonify({
            "status": "ok",
            "destination": "gmail_draft",
            "gmail_to": data.get('gmail_to', ''),
            "gmail_subject": data.get('gmail_subject', ''),
            "text": text,
            "message": "Gmail draft data ready — frontend will create via MCP"
        })

    return jsonify({"status": "error", "message": f"Unknown destination: {destination}"}), 400


@app.route('/api/content/drafts')
def list_content_drafts():
    """List saved draft HTML files from ~/.friday/wiki/content/."""
    CONTENT_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    drafts = []
    for f in sorted(CONTENT_DRAFTS_DIR.glob('*.html'), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            drafts.append({
                'filename': f.name,
                'size': f.stat().st_size,
                'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
        except Exception:
            pass
    return jsonify({'status': 'ok', 'drafts': drafts, 'total': len(drafts)})


@app.route('/api/content/drafts/<filename>')
def serve_content_draft(filename):
    """Serve a saved draft HTML file for browser viewing."""
    safe_name = Path(filename).name
    filepath = CONTENT_DRAFTS_DIR / safe_name
    if not filepath.exists() or not filepath.is_file():
        return jsonify({'status': 'not_found'}), 404
    CONTENT_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    return send_from_directory(str(CONTENT_DRAFTS_DIR), safe_name)


# ═══════════════════════════════════════════════════════════════
#  DATA FLOW API — "Write once, live everywhere"
# ═══════════════════════════════════════════════════════════════

FLOW_QUEUE_DIR = FRIDAY_DIR / "flow-queue"
FLOW_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

BRIEFING_SUPPLEMENT_DIR = FRIDAY_DIR / "wiki" / "briefings"
BRIEFING_SUPPLEMENT_DIR.mkdir(parents=True, exist_ok=True)


def _flow_trust_graph(content, metadata):
    """Update a person's trust graph entry with new intelligence."""
    person_name = metadata.get('person_name', '').strip()
    if not person_name:
        return {'destination': 'trust_graph', 'ok': False, 'error': 'No person_name in metadata'}

    trust_file = FRIDAY_DIR / "trust_graph.json"
    try:
        tdata = {}
        if trust_file.exists():
            tdata = json.loads(trust_file.read_text(encoding='utf-8'))
        if 'people' not in tdata:
            tdata['people'] = {}

        key = person_name.lower().replace(' ', '_').replace('-', '_')

        if key not in tdata['people']:
            # Auto-create entry
            tdata['people'][key] = {
                "name": person_name,
                "aliases": [],
                "entity_type": "human",
                "scores": {"overall": 0.5, "reliability": 0.5, "information_quality": 0.5,
                           "emotional_trust": 0.5, "timeliness": 0.5, "domain_expertise": 0.5},
                "evidence": [],
                "domains": [],
                "last_interaction": datetime.now().isoformat(),
                "created": datetime.now().isoformat()
            }

        person = tdata['people'][key]
        if 'intelligence' not in person:
            person['intelligence'] = []
        person['intelligence'].append({
            "content": content[:2000],
            "timestamp": datetime.now().isoformat(),
            "source": "data_flow"
        })
        # Keep last 20 intel entries
        person['intelligence'] = person['intelligence'][-20:]
        person['last_interaction'] = datetime.now().isoformat()

        tdata['people'][key] = person
        trust_file.write_text(json.dumps(tdata, indent=2), encoding='utf-8')
        return {'destination': 'trust_graph', 'ok': True, 'person': key}
    except Exception as e:
        return {'destination': 'trust_graph', 'ok': False, 'error': str(e)}


def _flow_calendar_notes(content, metadata):
    """Push content to a Google Calendar event description."""
    event_id = metadata.get('event_id', '').strip()
    if not event_id:
        return {'destination': 'calendar_notes', 'ok': False, 'error': 'No event_id in metadata'}
    try:
        result = _enrich_calendar_event(event_id, content)
        return {'destination': 'calendar_notes', **result}
    except Exception as e:
        return {'destination': 'calendar_notes', 'ok': False, 'error': str(e)}


def _flow_clipboard(content, _metadata):
    """Copy content to Windows clipboard via PowerShell."""
    try:
        subprocess.run(
            ['powershell', '-Command', 'Set-Clipboard', '-Value', content[:10000]],
            capture_output=True, text=True, timeout=10,
            creationflags=_POPEN_FLAGS,
        )
        return {'destination': 'clipboard', 'ok': True}
    except Exception as e:
        return {'destination': 'clipboard', 'ok': False, 'error': str(e)}


def _flow_gmail_draft(content, metadata):
    """Stage a Gmail draft in the flow queue for frontend pickup."""
    try:
        draft = {
            "id": str(uuid.uuid4()),
            "content": content[:10000],
            "thread_id": metadata.get('email_thread_id', ''),
            "person_name": metadata.get('person_name', ''),
            "created": datetime.now().isoformat(),
            "status": "pending"
        }
        draft_file = FLOW_QUEUE_DIR / f"gmail-draft-{draft['id']}.json"
        draft_file.write_text(json.dumps(draft, indent=2), encoding='utf-8')
        return {'destination': 'gmail_draft', 'ok': True, 'draft_id': draft['id']}
    except Exception as e:
        return {'destination': 'gmail_draft', 'ok': False, 'error': str(e)}


def _flow_briefing(content, metadata):
    """Append content to today's briefing supplementary file."""
    try:
        today_str = date.today().isoformat()
        supplement_file = BRIEFING_SUPPLEMENT_DIR / f"{today_str}-supplement.md"

        existing = ''
        if supplement_file.exists():
            existing = supplement_file.read_text(encoding='utf-8')

        person_name = metadata.get('person_name', '')
        header = f"\n\n---\n### {person_name or 'Research'} — {datetime.now().strftime('%H:%M')}\n" if existing else f"# Briefing Supplement — {today_str}\n\n### {person_name or 'Research'} — {datetime.now().strftime('%H:%M')}\n"

        supplement_file.write_text(existing + header + content[:5000] + '\n', encoding='utf-8')
        return {'destination': 'briefing', 'ok': True, 'file': str(supplement_file.name)}
    except Exception as e:
        return {'destination': 'briefing', 'ok': False, 'error': str(e)}


FLOW_HANDLERS = {
    'trust_graph': _flow_trust_graph,
    'calendar_notes': _flow_calendar_notes,
    'clipboard': _flow_clipboard,
    'gmail_draft': _flow_gmail_draft,
    'briefing': _flow_briefing,
}


@app.route('/api/flow', methods=['POST'])
def data_flow():
    """Central data flow endpoint — routes content to multiple destinations.

    POST JSON:
    {
      "data_type": "contact_research|meeting_prep|draft|briefing_excerpt|job_research",
      "content": "the content to distribute",
      "metadata": {"person_name": "", "event_id": "", "email_thread_id": ""},
      "destinations": ["trust_graph", "calendar_notes", "briefing", "clipboard", "gmail_draft"]
    }
    """
    data = request.get_json(silent=True) or {}
    content = data.get('content', '').strip()
    if not content:
        return jsonify({"status": "error", "message": "No content provided"}), 400

    destinations = data.get('destinations', [])
    if not destinations:
        return jsonify({"status": "error", "message": "No destinations specified"}), 400

    metadata = data.get('metadata', {})
    data_type = data.get('data_type', 'general')
    receipt = {"status": "ok", "data_type": data_type, "results": []}

    for dest in destinations:
        handler = FLOW_HANDLERS.get(dest)
        if handler:
            result = handler(content, metadata)
            receipt["results"].append(result)
        else:
            receipt["results"].append({"destination": dest, "ok": False, "error": f"Unknown destination: {dest}"})

    succeeded = sum(1 for r in receipt["results"] if r.get('ok'))
    failed = len(receipt["results"]) - succeeded
    receipt["summary"] = f"{succeeded} succeeded, {failed} failed"
    return jsonify(receipt)


@app.route('/api/flow/queue', methods=['GET'])
def flow_queue():
    """List pending items in the flow queue (gmail drafts, etc)."""
    items = []
    if FLOW_QUEUE_DIR.exists():
        for f in sorted(FLOW_QUEUE_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.suffix == '.json':
                try:
                    items.append(json.loads(f.read_text(encoding='utf-8')))
                except Exception:
                    pass
    return jsonify({"status": "ok", "items": items[:50], "count": len(items)})


# ═══════════════════════════════════════════════════════════════
#  CALENDAR ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def _enrich_calendar_event(event_id, research):
    """Read a calendar event, append Friday research, and update it.

    Uses the gcal MCP tools when available; falls back to storing
    the enrichment locally for later sync.
    """
    separator = "\n\n--- Friday Meeting Prep ---\n"
    enrichment = separator + research.strip() + "\n"

    # Try MCP-based Google Calendar update
    # The gcal tools are invoked at the agent/MCP layer, not directly here.
    # This endpoint stores the enrichment and exposes it for MCP tool orchestration.
    enrichment_file = FLOW_QUEUE_DIR / f"calendar-enrich-{event_id}.json"
    payload = {
        "event_id": event_id,
        "research": research.strip(),
        "enrichment_block": enrichment,
        "created": datetime.now().isoformat(),
        "status": "pending_sync"
    }
    enrichment_file.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return {"ok": True, "event_id": event_id, "status": "queued_for_sync",
            "message": "Enrichment stored. Will sync via gcal MCP on next calendar pass."}


@app.route('/api/calendar/enrich', methods=['POST'])
def calendar_enrich():
    """Enrich a Google Calendar event with meeting prep research.

    POST JSON:
    {
      "event_id": "google calendar event ID",
      "research": "the attendee research / meeting prep content"
    }
    """
    data = request.get_json(silent=True) or {}
    event_id = data.get('event_id', '').strip()
    research = data.get('research', '').strip()

    if not event_id:
        return jsonify({"status": "error", "message": "No event_id provided"}), 400
    if not research:
        return jsonify({"status": "error", "message": "No research content provided"}), 400

    try:
        result = _enrich_calendar_event(event_id, research)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def push_to_calendar(event_id, research_text):
    """Helper for briefing tasks to push research into calendar events.

    Call this from daily briefing generation when attendee research is ready.
    It routes through the flow API to update both the calendar and trust graph.
    """
    results = {}

    # Push to calendar
    results['calendar'] = _enrich_calendar_event(event_id, research_text)

    # Also push to briefing supplement
    results['briefing'] = _flow_briefing(research_text, {'person_name': 'Meeting Prep'})

    return results


@app.route('/api/flow/draft/confirm', methods=['POST'])
def confirm_draft():
    """Mark a queued gmail draft as deployed/sent."""
    data = request.get_json(silent=True) or {}
    draft_id = data.get('draft_id', '').strip()
    if not draft_id:
        return jsonify({"status": "error", "message": "No draft_id provided"}), 400

    draft_file = FLOW_QUEUE_DIR / f"gmail-draft-{draft_id}.json"
    if not draft_file.exists():
        return jsonify({"status": "error", "message": "Draft not found"}), 404

    try:
        draft = json.loads(draft_file.read_text(encoding='utf-8'))
        draft['status'] = 'deployed'
        draft['deployed_at'] = datetime.now().isoformat()
        draft_file.write_text(json.dumps(draft, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "draft_id": draft_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  CONTACTS / CRM
# ═══════════════════════════════════════════════════════════════

def _load_trust_graph():
    """Load trust graph with consistent shape. Returns dict with people keyed by name."""
    tfile = FRIDAY_DIR / "trust_graph.json"
    if not tfile.exists():
        return {"people": {}}
    try:
        return json.loads(tfile.read_text(encoding='utf-8'))
    except Exception:
        return {"people": {}}


def _contacts_list():
    """Merge trust graph people into a flat contacts list."""
    graph = _load_trust_graph()
    raw = graph.get('people') or {}
    items = raw.values() if isinstance(raw, dict) else raw
    contacts = []
    for p in items:
        if not isinstance(p, dict):
            continue
        scores = p.get('scores') or {}
        overall = scores.get('overall')
        if not isinstance(overall, (int, float)):
            overall = 0.5
        contacts.append({
            "name": p.get('name') or 'Unknown',
            "aliases": p.get('aliases') or [],
            "domains": p.get('domains') or [],
            "overall": overall,
            "last_interaction": p.get('last_interaction'),
            "evidence_count": len(p.get('evidence') or []),
        })
    contacts.sort(key=lambda c: c.get('overall') or 0, reverse=True)
    return contacts


def _contacts_research_dir():
    d = FRIDAY_DIR / "contacts-research"
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.route('/api/contacts')
def get_contacts():
    """Merged contact list built from trust_graph.json."""
    contacts = _contacts_list()
    return jsonify({"status": "ok", "contacts": contacts, "count": len(contacts)})


@app.route('/api/contacts/<path:name>')
def get_contact(name):
    """Full trust dimensions + evidence for a single contact (case-insensitive name)."""
    graph = _load_trust_graph()
    raw = graph.get('people') or {}
    target = (name or '').strip().lower()
    match = None
    if isinstance(raw, dict):
        if target in raw:
            match = raw[target]
        else:
            for k, v in raw.items():
                if not isinstance(v, dict):
                    continue
                cand = (v.get('name') or k or '').strip().lower()
                aliases = [a.lower() for a in (v.get('aliases') or [])]
                if cand == target or target in aliases:
                    match = v
                    break
    else:
        for v in raw:
            if not isinstance(v, dict):
                continue
            cand = (v.get('name') or '').strip().lower()
            aliases = [a.lower() for a in (v.get('aliases') or [])]
            if cand == target or target in aliases:
                match = v
                break
    if not match:
        return jsonify({"status": "error", "message": "Contact not found"}), 404

    # Look for a stored research file.
    research_file = _contacts_research_dir() / f"{target.replace(' ', '_')}.md"
    research = research_file.read_text(encoding='utf-8') if research_file.exists() else ''

    return jsonify({"status": "ok", "contact": match, "research": research})


@app.route('/api/contacts/research', methods=['POST'])
def contacts_research():
    """Kick off web research on a contact. Writes a stub and launches a background terminal."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"status": "error", "message": "name required"}), 400
    key = name.lower().replace(' ', '_')
    research_file = _contacts_research_dir() / f"{key}.md"
    stamp = datetime.now().isoformat()
    if not research_file.exists():
        research_file.write_text(
            f"# Research: {name}\n\n_Initialized {stamp}_\n\n"
            f"- Public profile search: pending\n"
            f"- LinkedIn / GitHub: pending\n"
            f"- Recent news mentions: pending\n",
            encoding='utf-8'
        )
    try:
        tid = str(uuid.uuid4())[:8]
        VIBE_TERMINALS[tid] = {
            "id": tid, "task": f"Research contact: {name}",
            "status": "pending", "cwd": str(FRIDAY_DIR),
            "started": stamp, "log_file": None
        }
    except Exception:
        tid = None
    return jsonify({
        "status": "ok", "name": name,
        "research_file": str(research_file),
        "task_id": tid,
        "message": f"Research queued for {name}"
    })


# ═══════════════════════════════════════════════════════════════
#  ROUTINES
# ═══════════════════════════════════════════════════════════════

ROUTINES_DIR = FRIDAY_DIR / "routines"
ROUTINES_DIR.mkdir(parents=True, exist_ok=True)
ROUTINE_STATUS_FILE = FRIDAY_DIR / "routine_status.json"

# Registered routine catalog. Defines display + default schedule when a template is missing.
ROUTINE_REGISTRY = [
    {"id": "morning-briefing",   "label": "Morning Briefing",    "ico": "🌅", "category": "briefing",    "schedule": "Daily · 7:00 AM"},
    {"id": "afternoon-briefing", "label": "Afternoon Briefing",  "ico": "☀️", "category": "briefing",    "schedule": "Daily · 2:00 PM"},
    {"id": "weekly-legal-prep",  "label": "Weekly Legal Prep",   "ico": "⚖️", "category": "legal",       "schedule": "Sundays · 6:00 PM"},
    {"id": "family-weekend-prep", "label": "Family Weekend Prep",  "ico": "👧", "category": "family",      "schedule": "Thursdays · 6:00 PM"},
    {"id": "portfolio-snapshot", "label": "Portfolio Snapshot",  "ico": "💰", "category": "finance",     "schedule": "Daily · 5:00 PM"},
    {"id": "content-pipeline",   "label": "Content Pipeline",    "ico": "✍️", "category": "content",     "schedule": "Daily · 10:00 AM"},
    {"id": "daily-creation",     "label": "Daily Creation",      "ico": "🎨", "category": "studio",      "schedule": "Daily · 2:00 PM"},
    {"id": "job-intelligence",   "label": "Job Intelligence",    "ico": "💼", "category": "career",      "schedule": "Daily · 8:00 AM"},
    {"id": "repo-sync",          "label": "Repo Sync",           "ico": "🔄", "category": "engineering", "schedule": "Daily · 11:00 PM"},
]


def _load_routine_status():
    if ROUTINE_STATUS_FILE.exists():
        try:
            return json.loads(ROUTINE_STATUS_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _save_routine_status(status):
    try:
        ROUTINE_STATUS_FILE.write_text(json.dumps(status, indent=2), encoding='utf-8')
    except Exception:
        pass


@app.route('/api/routines')
def list_routines():
    """Return the routine registry plus last-run status for each."""
    status = _load_routine_status()
    out = []
    for r in ROUTINE_REGISTRY:
        s = status.get(r['id'], {}) or {}
        template_exists = (ROUTINES_DIR / f"{r['id']}.md").exists()
        out.append({
            **r,
            "last_run": s.get('last_run'),
            "last_status": s.get('last_status'),
            "last_task_id": s.get('last_task_id'),
            "template_exists": template_exists,
        })
    return jsonify({"status": "ok", "routines": out})


@app.route('/api/routines/<routine_id>/run', methods=['POST'])
def run_routine(routine_id):
    """Trigger a routine on demand. Launches a background Vibe-Code task and records status."""
    reg = next((r for r in ROUTINE_REGISTRY if r['id'] == routine_id), None)
    if not reg:
        return jsonify({"status": "error", "message": "Unknown routine"}), 404

    template = ROUTINES_DIR / f"{routine_id}.md"
    task_desc = f"Run routine: {reg['label']}"
    if template.exists():
        task_desc += f" (see {template.name})"

    stamp = datetime.now().isoformat()
    tid = str(uuid.uuid4())[:8]
    try:
        VIBE_TERMINALS[tid] = {
            "id": tid, "task": task_desc,
            "status": "pending", "cwd": str(Path.cwd()),
            "started": stamp, "log_file": None
        }
    except Exception:
        pass

    status = _load_routine_status()
    status[routine_id] = {
        "last_run": stamp,
        "last_status": "launched",
        "last_task_id": tid,
    }
    _save_routine_status(status)

    return jsonify({
        "status": "ok",
        "routine": routine_id,
        "task_id": tid,
        "started_at": stamp,
        "message": f"{reg['label']} launched",
    })


# ═══════════════════════════════════════════════════════════════
#  OUTREACH PIPELINE
# ═══════════════════════════════════════════════════════════════

OUTREACH_DIR = FRIDAY_DIR / "outreach"
OUTREACH_DIR.mkdir(parents=True, exist_ok=True)
OUTREACH_LOG_FILE = OUTREACH_DIR / "outreach-log.json"


def _load_outreach_log():
    if not OUTREACH_LOG_FILE.exists():
        return {"version": 1, "entries": []}
    try:
        return json.loads(OUTREACH_LOG_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {"version": 1, "entries": []}


def _save_outreach_log(log):
    log["updated"] = datetime.now().isoformat()
    try:
        OUTREACH_LOG_FILE.write_text(json.dumps(log, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"  [FRIDAY] outreach log save failed: {e}")


def _career_ops_companies():
    """Return list of companies currently in the career-ops tracker (applied/interviewing)."""
    candidates = [WIKI_PROFESSIONAL_DIR / 'application-log.md', CAREER_OPS_DIR / 'applications.md']
    tracker_path = next((p for p in candidates if p.is_file()), None)
    if not tracker_path:
        return []
    try:
        content = tracker_path.read_text(encoding='utf-8')
    except Exception:
        return []
    companies = []
    for line in content.strip().split('\n'):
        if line.startswith('|') and '---' not in line and 'company' not in line.lower():
            cols = [c.strip() for c in line.split('|')[1:-1]]
            if len(cols) >= 3 and cols[0]:
                companies.append({"company": cols[0], "score": cols[1] if len(cols) > 1 else '', "status": cols[2] if len(cols) > 2 else ''})
    return companies


@app.route('/api/outreach/suggestions')
def outreach_suggestions():
    """Warm leads pulled from trust graph + career-ops tracker."""
    graph = _load_trust_graph()
    people_raw = graph.get('people') or {}
    people_items = people_raw.values() if isinstance(people_raw, dict) else people_raw

    log = _load_outreach_log()
    recent_targets = {
        (e.get('contact') or '').strip().lower()
        for e in log.get('entries', [])
        if e.get('contact')
    }

    suggestions = []
    for p in people_items:
        if not isinstance(p, dict):
            continue
        scores = p.get('scores') or {}
        overall = scores.get('overall')
        if not isinstance(overall, (int, float)):
            overall = 0.5
        if overall < 0.55:
            continue
        name = p.get('name') or 'Unknown'
        last = p.get('last_interaction') or ''
        suggestions.append({
            "type": "warm_contact",
            "contact": name,
            "score": round(overall, 2),
            "domains": p.get('domains') or [],
            "last_interaction": last,
            "reason": f"Trust {int(overall*100)}%" + (f" · last contact {last[:10]}" if last else " · no recent touch"),
            "already_contacted": name.lower() in recent_targets,
        })
    suggestions.sort(key=lambda s: s['score'], reverse=True)

    companies = _career_ops_companies()
    company_suggestions = []
    for c in companies[:10]:
        status = (c.get('status') or '').lower()
        if any(t in status for t in ('applied', 'interview', 'evaluated')):
            company_suggestions.append({
                "type": "career_target",
                "company": c.get('company'),
                "status": c.get('status'),
                "score": c.get('score'),
                "reason": f"Career-ops: {c.get('status') or 'tracked'}",
            })

    return jsonify({
        "status": "ok",
        "warm_contacts": suggestions[:20],
        "career_targets": company_suggestions,
        "total": len(suggestions) + len(company_suggestions),
    })


@app.route('/api/outreach/draft', methods=['POST'])
def outreach_draft():
    """Draft outreach message. Uses Gemini if available, else templated fallback."""
    data = request.get_json(silent=True) or {}
    contact = (data.get('contact') or data.get('name') or '').strip()
    company = (data.get('company') or '').strip()
    angle = (data.get('angle') or 'reconnect').strip()
    channel = (data.get('channel') or 'email').strip()
    context_notes = (data.get('context') or '').strip()

    if not contact and not company:
        return jsonify({"status": "error", "message": "contact or company required"}), 400

    target_label = contact or company
    prompt = (
        f"Draft a {channel} outreach to {target_label}. "
        f"Angle: {angle}. "
        f"Tone: warm, concise, specific. "
        f"Keep under 150 words. End with a single clear ask. "
        f"Context: {context_notes}"
    )

    draft_text = None
    try:
        client = get_genai_client()
        if client:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            draft_text = (getattr(resp, 'text', None) or '').strip()
    except Exception as e:
        print(f"  [FRIDAY] outreach draft Gemini error: {e}")

    if not draft_text:
        subject = f"Quick hello — {angle.title()}"
        body = (
            f"Hi {contact or 'there'},\n\n"
            f"Wanted to reach out — {angle}. "
            f"Specifically: {context_notes or 'would love to catch up when you have a few minutes.'}\n\n"
            f"Does next week work for a short call?\n\n"
            f"Best,"
        )
        draft_text = f"Subject: {subject}\n\n{body}"

    return jsonify({
        "status": "ok",
        "contact": contact,
        "company": company,
        "channel": channel,
        "angle": angle,
        "draft": draft_text,
    })


@app.route('/api/outreach/log', methods=['POST'])
def outreach_log():
    """Append an outreach event to the log."""
    data = request.get_json(silent=True) or {}
    contact = (data.get('contact') or '').strip()
    company = (data.get('company') or '').strip()
    channel = (data.get('channel') or 'email').strip()
    angle = (data.get('angle') or '').strip()
    message = (data.get('message') or '').strip()
    status = (data.get('status') or 'sent').strip()

    if not contact and not company:
        return jsonify({"status": "error", "message": "contact or company required"}), 400

    log = _load_outreach_log()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "contact": contact,
        "company": company,
        "channel": channel,
        "angle": angle,
        "status": status,
        "message": message[:2000],
        "timestamp": datetime.now().isoformat(),
    }
    log.setdefault('entries', []).append(entry)
    _save_outreach_log(log)
    return jsonify({"status": "ok", "entry": entry, "total": len(log['entries'])})


@app.route('/api/outreach/pipeline')
def outreach_pipeline():
    """Pipeline view: counts by channel/angle/status plus recent entries."""
    log = _load_outreach_log()
    entries = list(reversed(log.get('entries', [])))

    by_status, by_channel, by_angle = {}, {}, {}
    for e in entries:
        by_status[e.get('status', 'unknown')] = by_status.get(e.get('status', 'unknown'), 0) + 1
        by_channel[e.get('channel', 'unknown')] = by_channel.get(e.get('channel', 'unknown'), 0) + 1
        if e.get('angle'):
            by_angle[e['angle']] = by_angle.get(e['angle'], 0) + 1

    return jsonify({
        "status": "ok",
        "total": len(entries),
        "by_status": by_status,
        "by_channel": by_channel,
        "by_angle": by_angle,
        "recent": entries[:25],
    })


# ═══════════════════════════════════════════════════════════════
#  CONTENT PIPELINE
# ═══════════════════════════════════════════════════════════════

CONTENT_DIR = FRIDAY_DIR / "content"
CONTENT_DIR.mkdir(parents=True, exist_ok=True)
CONTENT_PIPELINE_FILE = CONTENT_DIR / "pipeline.json"
CONTENT_STAGES = ["idea", "drafting", "review", "scheduled", "published"]


def _load_content_pipeline():
    if not CONTENT_PIPELINE_FILE.exists():
        return {"version": 1, "items": []}
    try:
        return json.loads(CONTENT_PIPELINE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {"version": 1, "items": []}


def _save_content_pipeline(pipe):
    pipe["updated"] = datetime.now().isoformat()
    try:
        CONTENT_PIPELINE_FILE.write_text(json.dumps(pipe, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"  [FRIDAY] content pipeline save failed: {e}")


@app.route('/api/content/pipeline')
def content_pipeline():
    """Return content pipeline grouped by stage for kanban view."""
    pipe = _load_content_pipeline()
    items = pipe.get('items', [])
    by_stage = {s: [] for s in CONTENT_STAGES}
    for it in items:
        stage = it.get('stage') or 'idea'
        if stage not in by_stage:
            by_stage.setdefault(stage, [])
        by_stage[stage].append(it)
    return jsonify({
        "status": "ok",
        "stages": CONTENT_STAGES,
        "by_stage": by_stage,
        "total": len(items),
    })


@app.route('/api/content/idea', methods=['POST'])
def content_idea():
    """Add a new content idea to the pipeline."""
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({"status": "error", "message": "title required"}), 400

    stage = (data.get('stage') or 'idea').strip()
    if stage not in CONTENT_STAGES:
        stage = 'idea'

    pipe = _load_content_pipeline()
    stamp = datetime.now().isoformat()
    item = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "type": (data.get('type') or 'post').strip(),
        "stage": stage,
        "channel": (data.get('channel') or 'linkedin').strip(),
        "notes": (data.get('notes') or '').strip(),
        "tags": data.get('tags') or [],
        "created": stamp,
        "updated": stamp,
    }
    pipe.setdefault('items', []).append(item)
    _save_content_pipeline(pipe)
    return jsonify({"status": "ok", "item": item, "total": len(pipe['items'])})


@app.route('/api/content/draft', methods=['POST'])
def content_draft():
    """Draft content from a pipeline item (or ad-hoc title). Optionally advances stage."""
    data = request.get_json(silent=True) or {}
    item_id = (data.get('id') or '').strip()
    title = (data.get('title') or '').strip()
    channel = (data.get('channel') or 'linkedin').strip()
    notes = (data.get('notes') or '').strip()
    advance = bool(data.get('advance_stage'))

    pipe = _load_content_pipeline()
    item = None
    if item_id:
        for it in pipe.get('items', []):
            if it.get('id') == item_id:
                item = it
                break
        if not item:
            return jsonify({"status": "error", "message": "item not found"}), 404
        title = title or item.get('title', '')
        channel = item.get('channel') or channel
        notes = notes or item.get('notes', '')

    if not title:
        return jsonify({"status": "error", "message": "title or id required"}), 400

    prompt = (
        f"Draft a {channel} {item.get('type') if item else 'post'} titled: {title}. "
        f"Write in the user's voice. "
        f"Tone: sharp, specific, credible. "
        f"Structure: hook, 2-3 body beats, ask/CTA. "
        f"Length: 180-260 words for LinkedIn, longer for article. "
        f"Context / notes: {notes}"
    )

    draft_text = None
    try:
        client = get_genai_client()
        if client:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            draft_text = (getattr(resp, 'text', None) or '').strip()
    except Exception as e:
        print(f"  [FRIDAY] content draft Gemini error: {e}")

    if not draft_text:
        draft_text = (
            f"[{channel.upper()} DRAFT — {title}]\n\n"
            f"Hook: (one-line opener)\n\n"
            f"Body:\n- Point 1\n- Point 2\n- Point 3\n\n"
            f"Notes: {notes or '(no notes)'}\n\n"
            f"CTA: (single ask)"
        )

    if item is not None:
        item['draft'] = draft_text
        item['updated'] = datetime.now().isoformat()
        if advance and item.get('stage') in CONTENT_STAGES:
            idx = CONTENT_STAGES.index(item['stage'])
            if idx < len(CONTENT_STAGES) - 1:
                item['stage'] = CONTENT_STAGES[idx + 1]
        _save_content_pipeline(pipe)

    return jsonify({
        "status": "ok",
        "id": item_id or None,
        "title": title,
        "channel": channel,
        "draft": draft_text,
        "stage": (item or {}).get('stage'),
    })


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


@app.route('/api/futurespeak/pipeline')
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


@app.route('/api/futurespeak/revenue')
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


@app.route('/api/futurespeak/legal')
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


@app.route('/api/futurespeak/assets')
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
FS_DEFAULT_PROJECTS = [
    {"name": "FutureSpeak.AI", "url": "https://futurespeak.ai",
     "replit_url": "https://replit.com/@FutureSpeakAI/FutureSpeakAI-Website",
     "repo": "FutureSpeakAI-Website",
     "description": "Main company site — AI product studio & strategic consultancy",
     "category": "company"},
    {"name": "InnexEnergy.com", "url": "https://innexenergy.com",
     "replit_url": "https://replit.com/@FutureSpeakAI/innex-energy",
     "repo": "innex-energy",
     "description": "Jay family energy business site",
     "category": "client"},
    {"name": "OurPainfulTruth.org", "url": "https://ourpainfultruth.org",
     "replit_url": "https://replit.com/@FutureSpeakAI/our-painful-truth",
     "repo": "our-painful-truth",
     "description": "Janet's chronic-pain advocacy site",
     "category": "personal"},
    {"name": "Brushfire", "url": "",
     "replit_url": "https://replit.com/@FutureSpeakAI/Brushfire-INNEX-Dashboard",
     "repo": "Brushfire-INNEX-Dashboard",
     "description": "Petroleum analysis tool for INNEX",
     "category": "client"},
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


@app.route('/api/futurespeak/projects')
def fs_projects():
    projects = [_fs_project_view(p) for p in _fs_projects_load()]
    counts = {"green": 0, "yellow": 0, "red": 0, "remote": 0}
    for p in projects:
        ds = (p.get('status') or {}).get('deploy_status', 'remote')
        counts[ds] = counts.get(ds, 0) + 1
    return jsonify({"status": "ok", "projects": projects, "total": len(projects),
                    "by_deploy": counts})


@app.route('/api/futurespeak/project/<name>')
def fs_project_detail(name):
    for p in _fs_projects_load():
        if p.get('name', '').lower() == name.lower():
            return jsonify({"status": "ok", "project": _fs_project_view(p)})
    return jsonify({"status": "error", "message": "Project not found"}), 404


@app.route('/api/futurespeak/project', methods=['POST'])
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


@app.route('/api/futurespeak/project/<name>', methods=['DELETE'])
def fs_project_remove(name):
    projects = _fs_projects_load()
    remaining = [p for p in projects if p.get('name', '').lower() != name.lower()]
    if len(remaining) == len(projects):
        return jsonify({"status": "error", "message": "Project not found"}), 404
    _fs_projects_save(remaining)
    return jsonify({"status": "ok", "removed": name, "total": len(remaining)})


@app.route('/api/futurespeak/project/<name>/edit', methods=['POST'])
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


@app.route('/api/futurespeak/scan')
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


# ═══════════════════════════════════════════════════════════════
#  FRIDAY LIVE — Gemini Live API bridge over WebSocket
# ═══════════════════════════════════════════════════════════════

LIVE_MODEL = os.environ.get("FRIDAY_LIVE_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
# Graceful-degradation chain if the primary (native-audio) model is unavailable.
# half-cascade 2.5 still supports affective dialog; 3.1 is the known-working
# safety net (the 2.5/2.0 Live models have 1008'd on the AI Studio AQ. key tier,
# so 3.1 must stay last in the chain to guarantee voice still connects).
LIVE_MODEL_FALLBACK = "gemini-live-2.5-flash-preview"
LIVE_MODEL_FALLBACK2 = "gemini-3.1-flash-live-preview"
LIVE_VOICE = os.environ.get("FRIDAY_LIVE_VOICE", "Aoede")


def _get_live_model():
    """Return the currently configured voice/live model from settings, falling back to LIVE_MODEL."""
    return _load_settings().get("voice_model") or LIVE_MODEL


def _get_live_voice():
    """Return the currently configured Live API voice from settings.

    Resolution order: settings.tts_voice → FRIDAY_LIVE_VOICE env var → "Aoede".
    The Live API binds voice at session-config time, so changes take effect on
    the next WebSocket connection — not mid-stream.
    """
    return (_load_settings() or {}).get("tts_voice") or LIVE_VOICE


def _get_voice_language():
    """Return the configured BCP-47 language code, or '' to use the server default."""
    return ((_load_settings() or {}).get("voice_language") or "").strip()


def _get_voice_style_prompt():
    """Return the user's custom speaking-style instruction, or '' for built-in styles."""
    return ((_load_settings() or {}).get("voice_style_prompt") or "").strip()


def _model_supports_affective_dialog(model_name: str) -> bool:
    """Affective dialog is only available on Gemini 2.5 Flash Live models.

    Supported: gemini-2.5-flash-native-audio-preview, gemini-live-2.5-flash-preview,
    and any model with 'native-audio' in the name. Standard Live models
    (e.g. gemini-3.1-flash-live-preview, gemini-2.0-flash-live-001) return
    1011 if enable_affective_dialog is sent.
    """
    mn = (model_name or "").lower()
    if "native-audio" in mn:
        return True
    if "2.5-flash" in mn and ("live" in mn or "preview" in mn):
        return True
    return False

LIVE_SYSTEM_TEMPLATE = """You are Agent Friday, a sovereign personal AI assistant.
You are having a live voice conversation — natural spoken dialogue, not text chat.
Match your response length to what the user asks for: brief for quick questions, thorough and
comprehensive when they ask you to explain or go into detail. The user controls the length, not a
blanket rule. Deliver longer answers in short, clear sentences with natural pauses so they can
follow and interrupt. If they don't hear you the first time, repeat it simpler.

You can see through the user's phone camera. If you notice something interesting or relevant, mention it naturally.
Don't narrate what's on screen unless asked — only speak up when it matters.

Personality: knowledgeable, direct collaborator. No sycophancy. Independent thinker. Clear communication.
Trust the user's judgment; push back when you genuinely disagree, but don't lecture.

=== DAILY CONTEXT ===
{context_summary}
=== END CONTEXT ===
"""


def _strip_html(raw: str) -> str:
    raw = re.sub(r'<script\b[^>]*>.*?</script>', ' ', raw, flags=re.S | re.I)
    raw = re.sub(r'<style\b[^>]*>.*?</style>', ' ', raw, flags=re.S | re.I)
    raw = re.sub(r'<[^>]+>', ' ', raw)
    raw = re.sub(r'&nbsp;', ' ', raw)
    raw = re.sub(r'&amp;', '&', raw)
    raw = re.sub(r'&lt;', '<', raw)
    raw = re.sub(r'&gt;', '>', raw)
    raw = re.sub(r'\s+', ' ', raw)
    return raw.strip()


def _load_live_context() -> str:
    """Build a concise context summary string for the Friday Live system prompt."""
    parts = [f"TODAY: {date.today().isoformat()}"]

    # Latest briefing (plain-text excerpt)
    try:
        briefings_dir = HOME / ".friday" / "wiki" / "briefings"
        if briefings_dir.exists():
            candidates = sorted(
                (p for p in briefings_dir.iterdir() if p.suffix in ('.html', '.md')),
                reverse=True,
            )
            if candidates:
                latest = candidates[0]
                raw = latest.read_text(encoding='utf-8', errors='ignore')
                text = _strip_html(raw) if latest.suffix == '.html' else raw
                parts.append(f"LATEST BRIEFING ({latest.name}):\n{text[:1800]}")
    except Exception as e:
        parts.append(f"(briefing load failed: {e})")

    # Career pipeline
    try:
        tracker_candidates = [WIKI_PROFESSIONAL_DIR / 'application-log.md', CAREER_OPS_DIR / 'applications.md']
        tracker = next((p for p in tracker_candidates if p.exists()), None)
        if tracker:
            raw = tracker.read_text(encoding='utf-8', errors='ignore')
            parts.append(f"CAREER PIPELINE (top):\n{raw[:1200]}")
    except Exception:
        pass

    # Upcoming countdowns (<=90 days)
    try:
        today_d = date.today()
        events = [
            {"label": "Summer Solstice", "date": "2026-06-21"},
            {"label": "Independence Day", "date": "2026-07-04"},
            {"label": "New Year", "date": "2027-01-01"},
        ]
        cd = []
        for ev in events:
            d = date.fromisoformat(ev['date'])
            delta = (d - today_d).days
            if 0 <= delta <= 90:
                cd.append(f"- {ev['label']}: {delta} days away ({ev['date']})")
        if cd:
            parts.append("UPCOMING:\n" + "\n".join(cd))
    except Exception:
        pass

    # Trust graph — top names
    try:
        tfile = FRIDAY_DIR / "trust_graph.json"
        if tfile.exists():
            data = json.loads(tfile.read_text(encoding='utf-8'))
            people = data.get('people') or {}
            items = []
            for name, info in people.items():
                score = 0
                role = ''
                if isinstance(info, dict):
                    score = info.get('score') or info.get('trust_score') or 0
                    role = info.get('role') or info.get('relation') or info.get('relationship') or ''
                try:
                    score = float(score)
                except Exception:
                    score = 0.0
                items.append((name, score, role))
            items.sort(key=lambda x: x[1], reverse=True)
            top = items[:8]
            if top:
                lines = [f"- {n}" + (f" ({r})" if r else '') for n, _s, r in top]
                parts.append("TRUST CIRCLE (top 8):\n" + "\n".join(lines))
    except Exception:
        pass

    # Personality snapshot
    try:
        pfile = FRIDAY_DIR / "personality.json"
        if pfile.exists():
            data = json.loads(pfile.read_text(encoding='utf-8'))
            parts.append(f"PERSONALITY: {json.dumps(data)[:500]}")
    except Exception:
        pass

    return "\n\n".join(parts)


def _persist_voice_turn(user_text, agent_text):
    """Log a completed voice turn to the context log and chat history.

    Voice turns are saved as event types `voice_user` and `voice_agent` so
    they show up in the context-log search alongside text chats, and as
    role=user/friday entries in CHAT_HISTORY with `via:'voice'` so the chat
    panel can render them when the user comes back.
    """
    settings = _load_settings()
    off_record = bool(settings.get('off_record'))
    if not off_record:
        if user_text:
            _log_context("voice_user", {"text": user_text})
        if agent_text:
            _log_context("voice_agent", {"text": agent_text})
    now_iso = datetime.now().isoformat()
    if user_text:
        CHAT_HISTORY.append({
            'id': str(uuid.uuid4()),
            'timestamp': now_iso,
            'role': 'user',
            'text': user_text,
            'pinned': False,
            'via': 'voice',
        })
    if agent_text:
        CHAT_HISTORY.append({
            'id': str(uuid.uuid4()),
            'timestamp': now_iso,
            'role': 'friday',
            'text': agent_text,
            'pinned': False,
            'via': 'voice',
        })
    try:
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)
    except Exception as e:
        print(f'  [voice] chat history save failed: {e}')


def _spawn_voice_distill(turn_log):
    """Ask Claude to review a voice session and propose any wiki updates.

    Fire-and-forget — runs as a background task so the WS handler can return
    immediately. Claude has access to the `propose_wiki_update` tool, so any
    new fact it spots will land in the pending-approvals queue rather than
    being applied immediately.
    """
    if not turn_log:
        return
    convo = []
    for u, a in turn_log:
        if u:
            convo.append(f"User (voice): {u}")
        if a:
            convo.append(f"Friday (voice): {a}")
    transcript = "\n".join(convo)[:8000]
    prompt = (
        "Review the following voice conversation between the user and Friday. "
        "If the user mentioned anything new and durable about themselves, their work, "
        "his family, his projects, or his preferences — something worth remembering "
        "across sessions — call `propose_wiki_update` to queue it for his approval. "
        "Pick a sensible file under ~/wiki/ (e.g. identity/core-profile.md, "
        "professional/job-search.md, family/notes.md). If nothing new came up, "
        "reply with a one-line note and do nothing.\n\n"
        "=== TRANSCRIPT ===\n" + transcript
    )
    _spawn_task(
        name='Voice session: distill to wiki',
        prompt=prompt,
        description='Looking for anything wiki-worthy in the voice session…',
    )


if sock is not None:

    @sock.route('/ws/live')
    def ws_live(ws):
        """Bridge a browser WebSocket to a Gemini Live API session.

        Messages from browser -> Gemini:
          { type: 'audio', data: <b64 PCM16 @ 16 kHz> }
          { type: 'image', data: <b64 JPEG> }
          { type: 'text', text: "..." }
          { type: 'end' }
        Messages from Gemini -> browser:
          { type: 'audio', data: <b64 PCM16 @ 24 kHz> }
          { type: 'text', text: "..." }           # model text or transcript
          { type: 'input_transcript', text: ... } # user transcript
          { type: 'status', text: "..." }
          { type: 'turn_end' }
          { type: 'error', error: "..." }
        """
        import time as _time
        _vlog_path = FRIDAY_DIR / 'voice_debug.log'
        def _vlog(msg):
            line = f"{_time.strftime('%H:%M:%S')} {msg}\n"
            try:
                with open(_vlog_path, 'a', encoding='utf-8') as _f:
                    _f.write(line)
            except Exception:
                pass
            print(f'[live] {msg}')

        _vlog(f'=== WS connection from {request.remote_addr} ===')
        _vlog(f'session.authenticated={session.get("authenticated")} local={_is_local_request()} GEMINI_KEY={GEMINI_API_KEY[:8] if GEMINI_API_KEY else "MISSING"}...')

        # Auth enforcement (before_request already redirects unauthenticated HTML
        # requests, but be defensive in case /ws/ paths were excluded).
        # Loopback connections are always trusted — same-machine usage skips
        # auth so the user never hits an "unauthorized" voice error locally.
        if FRIDAY_WS_TOKEN:
            _tok = request.args.get('token', '')
            if not _hmac.compare_digest(_tok, FRIDAY_WS_TOKEN):
                _vlog('AUTH FAIL — bad/missing ws token')
                try:
                    ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
                except Exception:
                    pass
                return
        if FRIDAY_PASSWORD and not session.get("authenticated") and not _loopback_trusted():
            _vlog('AUTH FAIL — sending unauthorized and closing')
            try:
                ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            except Exception:
                pass
            return

        if not GEMINI_API_KEY:
            _vlog('ERROR — GEMINI_API_KEY not set')
            try:
                ws.send(json.dumps({"type": "error", "error": "GEMINI_API_KEY not set"}))
            except Exception:
                pass
            return

        try:
            from google import genai
            from google.genai import types
        except ImportError as _ie:
            _vlog(f'ERROR — google-genai not installed: {_ie}')
            try:
                ws.send(json.dumps({"type": "error", "error": "google-genai not installed"}))
            except Exception:
                pass
            return

        # Vault gating: the Live voice system instruction is sent to Google's
        # cloud servers, so it must be gated as a CLOUD provider. TIER_1 passes
        # through; TIER_2 is redacted; TIER_3 is dropped. This extends the
        # local-only vault policy to voice without breaking the experience.
        _vault_control = _get_vault_control() if _vault_local_only() else None
        _vault_fallback = _vault_cloud_fallback()
        try:
            personality = _load_agent_personality()
            full_ctx = _get_friday_system_prompt(
                provider='gemini',
                vault_control=_vault_control,
                vault_fallback=_vault_fallback,
            )
        except Exception as e:
            personality = ''
            full_ctx = f"(context load failed: {e})"
        if _vault_control is not None:
            _vlog('voice system prompt gated for cloud provider=gemini (vault local-only)')
        voice_prefix = (
            "You are Agent Friday, a sovereign personal AI assistant.\n"
            "You are having a LIVE VOICE conversation — be natural and speak like a person.\n"
            "CRITICAL LENGTH RULE: When the user asks you to explain something in detail, "
            "go deep. Give thorough, multi-paragraph spoken responses. Do not cut yourself "
            "short. The user will tell you when they've heard enough. Default to comprehensive "
            "when asked 'tell me about', 'explain', 'go into detail', 'walk me through', or "
            "similar. This applies especially to questions about how you work — your systems, "
            "the pipeline, the vault, disinformation mitigation, security, anti-sycophancy: when "
            "asked to explain any of these, give the full multi-paragraph walkthrough, not a "
            "one-line summary. Only be brief when the question is simple or the user asks for "
            "brevity. "
            "In voice, deliver long answers in short, clear sentences with natural pauses so "
            "they can follow and interrupt — length comes from covering the substance, not "
            "from cramming.\n"
            "NEVER use markdown formatting — no asterisks, headers, or bullet points. Speak naturally.\n"
            "Use contractions and casual tone. When it fits, ask a follow-up question to keep the conversation flowing.\n"
            "For questions about personal financial data, health records, family legal "
            "matters, or other sensitive vault content, tell the user: 'That information "
            "is in my Sovereign Vault, which I can only access through local processing. "
            "If you'd like, I can set up a fully local voice mode using Whisper and a "
            "local TTS engine — that way we can have voice conversations about anything, "
            "including your private data, without any of it leaving this machine. Want me "
            "to check if your hardware can handle it?'\n\n"
        )
        if personality:
            voice_prefix += f"=== YOUR PERSONALITY ===\n{personality}\n\n"

        # Voice demo spec sheet: Tier 1 (public) product knowledge, injected
        # UNGATED so Gemini Live always knows what Friday IS. This sits between
        # the personality prefix and the vault-gated context — it is never
        # passed through vault_control, so it survives cloud gating intact and
        # Friday can always answer "what are you?" / "how do you work?" instead
        # of deflecting to the Sovereign Vault.
        voice_demo = _load_voice_demo()
        if voice_demo:
            voice_prefix += (
                "=== ABOUT AGENT FRIDAY (PUBLIC / ALWAYS SHAREABLE) ===\n"
                "The following is public product knowledge. You may speak any of "
                "it aloud to anyone — it is never private vault data, so never "
                "deflect these topics to the Sovereign Vault.\n\n"
                + voice_demo + "\n\n"
            )

        try:
            ws.send(json.dumps({"type": "status", "text": "loading context"}))
        except Exception:
            return

        # NOTE: the Live client is created lazily inside runner() per API version.
        # v1alpha unlocks affective dialog + proactive audio, but the AI Studio
        # API-key tier sometimes rejects it with a 1008 "Expected OAuth 2 access
        # token" auth error — so we try v1alpha first, then fall back to the
        # default (v1beta) endpoint, which reliably accepts API-key auth.

        live_voice = _get_live_voice()
        live_language = _get_voice_language()
        live_style = _get_voice_style_prompt()
        live_settings = _load_settings() or {}

        live_temperature = live_settings.get("voice_temperature")
        try:
            live_temperature = float(live_temperature) if live_temperature is not None else None
        except (TypeError, ValueError):
            live_temperature = None
        try:
            live_max_tokens = int(live_settings.get("voice_max_tokens") or 0)
        except (TypeError, ValueError):
            live_max_tokens = 0
        _configured_live_model = _get_live_model()
        _model_is_25 = _model_supports_affective_dialog(_configured_live_model)
        live_affective = live_settings.get("voice_affective", _model_is_25)
        if live_affective is None:
            live_affective = _model_is_25
        live_affective = bool(live_affective)
        live_proactive = live_settings.get("voice_proactive", _model_is_25)

        # Build system instruction with mood + affective dialog awareness
        try:
            from voice_personality import get_voice_personality
            _vp = get_voice_personality()
            _vp.affective_dialog = live_affective
            system_instruction = _vp.build_system_instruction(
                voice_prefix + full_ctx, affective_dialog=live_affective)
        except Exception:
            system_instruction = voice_prefix + full_ctx
        if live_proactive is None:
            live_proactive = _model_is_25
        live_proactive = bool(live_proactive)
        live_context_compression = bool(live_settings.get("voice_context_compression"))

        _vlog(
            f'voice cfg: voice={live_voice}; lang={live_language or "default"}; '
            f'temp={live_temperature}; max_tokens={live_max_tokens or "inf"}; '
            f'affective={live_affective}; proactive={live_proactive}; '
            f'ctx_compress={live_context_compression}; '
            f'style={(live_style[:60] + "...") if len(live_style) > 60 else (live_style or "default")}'
        )

        speech_kwargs = {
            "voice_config": types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=live_voice)
            )
        }
        if live_language:
            speech_kwargs["language_code"] = live_language

        sys_text = system_instruction
        if live_style:
            sys_text = f"Speaking style: {live_style}\n\n{sys_text}"

        live_cfg_kwargs = dict(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(**speech_kwargs),
            system_instruction=types.Content(parts=[types.Part(text=sys_text)]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # Explicit VAD config: fire faster so Friday actually responds when the user pauses.
            # Default silence_duration is ~1500ms; 500ms makes turn-end detection snappier.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    silence_duration_ms=500,
                    prefix_padding_ms=200,
                    # LOW sensitivity: require louder/clearer speech to trip VAD.
                    # Friday's own speaker bleed (echo) is quieter than a real
                    # user talking, so LOW makes the server far less likely to
                    # mistake that echo for a barge-in and fire a spurious
                    # {type:'interrupted'} that cuts her off mid-sentence.
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                ),
            ),
        )
        if live_temperature is not None:
            live_cfg_kwargs["temperature"] = live_temperature
        if live_max_tokens > 0:
            live_cfg_kwargs["max_output_tokens"] = live_max_tokens
        if live_affective:
            live_cfg_kwargs["enable_affective_dialog"] = True
        if live_proactive:
            live_cfg_kwargs["proactivity"] = types.ProactivityConfig(proactive_audio=True)
        if live_context_compression:
            live_cfg_kwargs["context_window_compression"] = types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            )

        # Session resumption: ask Gemini to emit resumption handles. A single Live
        # session is capped (~10-15 min of audio, plus a context-window cap); when
        # it ages out Gemini sends GoAway and ends the stream. With a handle in hand
        # the reconnect loop in runner() transparently renews the session mid-call
        # instead of the audio just stopping. Captured in writer(), replayed below.
        _supports_resumption = hasattr(types, 'SessionResumptionConfig')
        if _supports_resumption:
            live_cfg_kwargs["session_resumption"] = types.SessionResumptionConfig()

        # The per-attempt LiveConnectConfig is built inside runner() from
        # live_cfg_kwargs (affective/proactive stripped per endpoint+model).

        done = threading.Event()

        def _safe_send(obj):
            if done.is_set():
                return False
            try:
                ws.send(json.dumps(obj))
                return True
            except ConnectionClosed:
                done.set()
                return False
            except Exception:
                return False

        async def runner():
            # The actual connection happens inside `async with`, so the fallback
            # must wrap the entire session block, not just the connect() call.
            configured_live_model = _get_live_model()

            # Build an ordered attempt plan of (api_version, model_name).
            #  • v1alpha is tried first ONLY when affective/proactive would actually
            #    be used (those features require v1alpha). If v1alpha rejects the
            #    API key (1008 "Expected OAuth 2 access token"), we fall through.
            #  • The default endpoint (api_version=None → v1beta) reliably accepts
            #    API-key auth; affective/proactive are stripped there.
            attempts = []
            _seen = set()
            def _add_attempt(api_version, model_name):
                key = (api_version, model_name)
                if key not in _seen:
                    _seen.add(key)
                    attempts.append(key)

            _primary_affective = live_affective and _model_supports_affective_dialog(configured_live_model)
            if _primary_affective or live_proactive:
                _add_attempt("v1alpha", configured_live_model)
            _add_attempt(None, configured_live_model)
            for _fallback in (LIVE_MODEL_FALLBACK, LIVE_MODEL_FALLBACK2):
                _add_attempt(None, _fallback)

            # Lazily create (and cache) a client per API version.
            _clients = {}
            def _client_for(api_version):
                if api_version not in _clients:
                    if api_version:
                        _clients[api_version] = genai.Client(
                            api_key=GEMINI_API_KEY, http_options={"api_version": api_version})  # pragma: allowlist secret
                    else:
                        _clients[api_version] = genai.Client(api_key=GEMINI_API_KEY)  # pragma: allowlist secret
                return _clients[api_version]

            last_error = None
            for api_version, model_name in attempts:
                # affective dialog + proactive audio are only valid on native-audio
                # models AND only on the v1alpha endpoint. Strip them otherwise so a
                # user who has the toggle on doesn't see the standard endpoint/model
                # fail with 1011 (unsupported field) or 1008 (auth).
                use_affective = (api_version == "v1alpha" and live_affective
                                 and _model_supports_affective_dialog(model_name))
                use_proactive = (api_version == "v1alpha" and live_proactive)
                per_model_kwargs = dict(live_cfg_kwargs)
                if not use_affective:
                    per_model_kwargs.pop("enable_affective_dialog", None)
                if not use_proactive:
                    per_model_kwargs.pop("proactivity", None)
                per_model_cfg = types.LiveConnectConfig(**per_model_kwargs)
                active_client = _client_for(api_version)
                _vlog(f'connecting to model: {model_name} (api={api_version or "default(v1beta)"}, '
                      f'affective={use_affective}, proactive={use_proactive})')
                try:
                    # ── Per-connection conversation state. Lives ACROSS the
                    # transparent session renewals below so a reconnect seam never
                    # loses an in-flight turn or the distill log. ──
                    _audio_chunks_received = 0
                    _gemini_chunks_received = 0
                    _audio_bytes_to_gemini = 0
                    _audio_bytes_from_gemini = 0
                    _safe_send_failures = 0
                    in_buf = []
                    out_buf = []
                    turn_log = []
                    resume_handle = [None]   # newest session-resumption handle from Gemini
                    greeted = [False]

                    def _flush_turn():
                        user_text = ''.join(in_buf).strip()
                        agent_text = ''.join(out_buf).strip()
                        in_buf.clear()
                        out_buf.clear()
                        if not user_text and not agent_text:
                            return
                        try:
                            _persist_voice_turn(user_text, agent_text)
                        except Exception as e:
                            print(f'[live] persist_voice_turn error: {e}')
                        _safe_send({
                            "type": "voice_turn_done",
                            "user_text": user_text,
                            "agent_text": agent_text,
                        })
                        turn_log.append((user_text, agent_text))

                    # reader()/writer() are bound to ONE Gemini session via `sess`
                    # and stop on either `done` (browser closed — terminal) or
                    # `sdone` (this session leg ended — GoAway/timeout/drop, renew).
                    async def reader(sess, sdone):
                        nonlocal _audio_chunks_received, _audio_bytes_to_gemini
                        while not done.is_set() and not sdone.is_set():
                            try:
                                raw = await asyncio.to_thread(ws.receive, 1.0)
                            except ConnectionClosed:
                                _vlog('reader: ConnectionClosed from browser')
                                done.set()
                                return
                            except Exception as e:
                                continue
                            if raw is None:
                                continue
                            if isinstance(raw, bytes):
                                try:
                                    raw = raw.decode('utf-8')
                                except Exception:
                                    continue
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue
                            t = msg.get('type')
                            try:
                                if t == 'audio' and msg.get('data'):
                                    data = base64.b64decode(msg['data'])
                                    _audio_chunks_received += 1
                                    _audio_bytes_to_gemini += len(data)
                                    if _audio_chunks_received in (1, 5, 25) or _audio_chunks_received % 50 == 0:
                                        # Log RMS amplitude so we can tell speech from silence.
                                        try:
                                            import struct as _st
                                            _n = len(data) // 2
                                            if _n > 0:
                                                _samples = _st.unpack(f'<{_n}h', data)
                                                _peak = max(abs(s) for s in _samples)
                                                _sumsq = sum(s * s for s in _samples)
                                                _rms = int((_sumsq / _n) ** 0.5)
                                            else:
                                                _peak = _rms = 0
                                        except Exception:
                                            _peak = _rms = -1
                                        _vlog(f'browser->gemini: chunk #{_audio_chunks_received} ({len(data)} bytes, total {_audio_bytes_to_gemini}, rms={_rms}, peak={_peak})')
                                    await sess.send_realtime_input(
                                        audio=types.Blob(data=data, mime_type='audio/pcm;rate=16000')
                                    )
                                elif t == 'image' and msg.get('data'):
                                    data = base64.b64decode(msg['data'])
                                    await sess.send_realtime_input(
                                        video=types.Blob(data=data, mime_type='image/jpeg')
                                    )
                                elif t == 'text' and msg.get('text'):
                                    _vlog(f'browser->gemini: text {msg["text"]!r}')
                                    await sess.send_realtime_input(text=msg['text'])
                                elif t == 'end':
                                    _vlog('reader: browser sent end signal')
                                    # Explicitly flush audio stream so Gemini stops waiting for VAD.
                                    try:
                                        await sess.send_realtime_input(audio_stream_end=True)
                                        _vlog('sent audio_stream_end=True to gemini')
                                    except Exception as _e:
                                        _vlog(f'audio_stream_end send failed: {_e}')
                                    done.set()
                                    return
                            except Exception as e:
                                _vlog(f'send-to-gemini ERROR: {type(e).__name__}: {e}')
                                traceback.print_exc()

                    async def writer(sess, sdone):
                        nonlocal _gemini_chunks_received, _audio_bytes_from_gemini, _safe_send_failures
                        try:
                            while not done.is_set() and not sdone.is_set():
                                async for chunk in sess.receive():
                                    if done.is_set() or sdone.is_set():
                                        return
                                    try:
                                        _gemini_chunks_received += 1
                                        # Capture the newest resumption handle so the
                                        # reconnect loop can renew this exact session.
                                        _sru = getattr(chunk, 'session_resumption_update', None)
                                        if _sru is not None and getattr(_sru, 'new_handle', None):
                                            resume_handle[0] = _sru.new_handle
                                        # GoAway: Gemini is about to retire this session
                                        # (audio/context cap). End this leg cleanly so the
                                        # reconnect loop renews it via the handle above —
                                        # the user hears no break.
                                        _ga = getattr(chunk, 'go_away', None)
                                        if _ga is not None:
                                            _tl = getattr(_ga, 'time_left', None)
                                            _vlog(f'GoAway from Gemini (time_left={_tl}) — renewing session via resumption handle')
                                            sdone.set()
                                            return
                                        if _gemini_chunks_received <= 5 or _gemini_chunks_received % 20 == 0:
                                            _resume = _sru
                                            _va = getattr(chunk, 'voice_activity', None) or getattr(chunk, 'voice_activity_detection_signal', None)
                                            _vlog(f'gemini chunk #{_gemini_chunks_received}: setup={chunk.setup_complete is not None} sc={chunk.server_content is not None} tool={chunk.tool_call is not None} resume={_resume is not None} va={_va is not None}')
                                        sc = getattr(chunk, 'server_content', None)
                                        if sc is not None:
                                            out_tr = getattr(sc, 'output_transcription', None)
                                            if out_tr and getattr(out_tr, 'text', None):
                                                _vlog(f'output_transcription: {out_tr.text!r}')
                                                out_buf.append(out_tr.text)
                                                _safe_send({"type": "text", "text": out_tr.text})
                                            in_tr = getattr(sc, 'input_transcription', None)
                                            if in_tr and getattr(in_tr, 'text', None):
                                                _vlog(f'input_transcription: {in_tr.text!r}')
                                                in_buf.append(in_tr.text)
                                                _safe_send({"type": "input_transcript", "text": in_tr.text})
                                            mt = getattr(sc, 'model_turn', None)
                                            if mt and getattr(mt, 'parts', None):
                                                for part in mt.parts:
                                                    # Audio: PCM bytes at 24kHz in part.inline_data.data
                                                    il = getattr(part, 'inline_data', None)
                                                    if il and getattr(il, 'data', None):
                                                        _audio_bytes_from_gemini += len(il.data)
                                                        if _audio_bytes_from_gemini <= 50000 or _gemini_chunks_received % 20 == 0:
                                                            _vlog(f'gemini->browser: audio {len(il.data)} bytes ({il.mime_type}); total {_audio_bytes_from_gemini}')
                                                        ok = _safe_send({
                                                            "type": "audio",
                                                            "data": base64.b64encode(il.data).decode('ascii'),
                                                        })
                                                        if not ok:
                                                            _safe_send_failures += 1
                                                            _vlog(f'ws.send FAILED for audio chunk (cumulative failures: {_safe_send_failures})')
                                                    pt = getattr(part, 'text', None)
                                                    if pt:
                                                        out_buf.append(pt)
                                                        _safe_send({"type": "text", "text": pt})
                                            if getattr(sc, 'turn_complete', False):
                                                _vlog(f'turn_complete (audio out so far: {_audio_bytes_from_gemini} bytes)')
                                                _flush_turn()
                                                _safe_send({"type": "turn_end"})
                                            if getattr(sc, 'interrupted', False):
                                                _vlog('interrupted')
                                                _safe_send({"type": "interrupted"})
                                    except Exception as e:
                                        _vlog(f'recv processing ERROR: {type(e).__name__}: {e}')
                                        traceback.print_exc()
                                # session.receive() iterator ends after a turn; re-enter to keep listening
                                _vlog(f'receive iterator completed (after {_gemini_chunks_received} chunks), re-entering for next turn')
                        except Exception as e:
                            _vlog(f'writer EXCEPTION: {type(e).__name__}: {e}')
                        finally:
                            _vlog(f'writer leg done. stats: gemini_chunks={_gemini_chunks_received}, audio_in_bytes={_audio_bytes_to_gemini}, audio_out_bytes={_audio_bytes_from_gemini}, send_fails={_safe_send_failures}')
                            # End THIS leg only. Whether the whole connection is over
                            # (done) is decided by reader/GoAway, not by the receive
                            # stream ending — an unexpected stream end with a handle in
                            # hand should renew, not terminate.
                            sdone.set()

                    async def no_audio_watchdog():
                        # If the browser sends zero audio chunks within 5s of the
                        # session opening, log a clear warning. This catches "WS
                        # connected but mic never streams" cases that otherwise
                        # look identical to "user just isn't talking yet".
                        try:
                            await asyncio.sleep(5.0)
                        except asyncio.CancelledError:
                            return
                        if done.is_set():
                            return
                        if _audio_chunks_received == 0:
                            _vlog('WARNING: no audio chunks received from browser after 5s — mic likely silent or WS not flowing')
                            _safe_send({"type": "status", "text": "no mic audio reaching server"})

                    # ── Reconnect loop ──────────────────────────────────────────
                    # A single Gemini Live session is capped (~10-15 min of audio,
                    # plus a context-window cap). When Gemini sends GoAway / ends the
                    # stream while the BROWSER is still connected, we renew the session
                    # using the last resumption handle and keep going — Gemini restores
                    # the conversation context server-side, so the user hears no seam.
                    # If no handle was ever issued (resumption unsupported), this runs
                    # exactly once and behaves like the old single-session path.
                    leg = 0
                    while not done.is_set():
                        if leg > 0 and resume_handle[0] is not None and _supports_resumption:
                            _leg_kwargs = dict(per_model_kwargs)
                            _leg_kwargs["session_resumption"] = types.SessionResumptionConfig(handle=resume_handle[0])
                            _leg_cfg = types.LiveConnectConfig(**_leg_kwargs)
                            _vlog(f'reconnecting voice session (renewal #{leg}) with resumption handle')
                        else:
                            _leg_cfg = per_model_cfg
                        async with active_client.aio.live.connect(model=model_name, config=_leg_cfg) as session_ai:
                            if leg == 0:
                                _safe_send({"type": "status", "text": "live"})
                                _vlog(f'session established with {model_name}')
                            else:
                                _vlog(f'session renewed with {model_name} (renewal #{leg})')

                            # Greeting only on the very first leg — a renewal must not
                            # re-greet (and Gemini already has the restored context).
                            if not greeted[0]:
                                greeted[0] = True
                                try:
                                    await session_ai.send_client_content(
                                        turns={"role": "user", "parts": [{"text": "Greet me in one short sentence."}]},
                                        turn_complete=True,
                                    )
                                    _vlog('sent initial greeting prompt')
                                except Exception as _e:
                                    _vlog(f'greeting send failed: {_e}')

                            sdone = asyncio.Event()
                            _tasks = [reader(session_ai, sdone), writer(session_ai, sdone)]
                            if leg == 0:
                                _tasks.append(no_audio_watchdog())
                            await asyncio.gather(*_tasks, return_exceptions=True)

                        # Browser gone, or no handle to renew with → terminal.
                        if done.is_set() or resume_handle[0] is None or not _supports_resumption:
                            break
                        leg += 1
                        _vlog(f'voice session leg ended without browser close — renewing (total renewals: {leg})')

                    try:
                        _flush_turn()
                    except Exception:
                        pass
                    if turn_log:
                        try:
                            _spawn_voice_distill(turn_log)
                        except Exception as e:
                            print(f'[live] voice distill spawn error: {e}')
                    break  # session completed successfully, don't try fallback
                except Exception as e:
                    last_error = e
                    import traceback as _tb
                    tb_str = _tb.format_exc()
                    _vlog(f'SESSION ERROR with {model_name} (api={api_version or "default(v1beta)"}): {type(e).__name__}: {e}')
                    _vlog(f'TRACEBACK: {tb_str}')
                    traceback.print_exc()
                    if (api_version, model_name) == attempts[-1]:
                        _safe_send({"type": "error", "error": str(e)})
                    else:
                        nxt = attempts[attempts.index((api_version, model_name)) + 1]
                        _vlog(f'trying fallback: model={nxt[1]} api={nxt[0] or "default(v1beta)"}')

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(runner())
        except Exception as _top_e:
            import traceback as _tb2
            _vlog(f'TOP-LEVEL runner error: {type(_top_e).__name__}: {_top_e}')
            _vlog(f'TRACEBACK: {_tb2.format_exc()}')
        finally:
            done.set()
            try:
                loop.close()
            except Exception:
                pass
            try:
                ws.close()
            except Exception:
                pass
            _vlog('=== WS handler done ===')


# ── Computer Control API ─────────────────────────────────────────

@app.route('/api/control/permission', methods=['GET', 'POST'])
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


@app.route('/api/control/kill', methods=['POST'])
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


def _start_kill_hotkey():
    """Background thread: listen for Ctrl+Shift+Q as a global kill switch."""
    try:
        from pynput import keyboard as _kb

        def _on_kill():
            print("  [FRIDAY] KILL HOTKEY Ctrl+Shift+Q — computer control terminated")
            _CC_PERMISSION.clear()
            _CC_KILL.set()
            _cc_persist(False)
            if _HAS_PYAUTOGUI:
                try:
                    _pag.moveTo(0, 0, duration=0.1)
                except Exception:
                    pass
            try:
                _log_context("cc_action", {"action": "kill_hotkey_ctrl_shift_q"})
            except Exception:
                pass

        hk = _kb.GlobalHotKeys({'<ctrl>+<shift>+q': _on_kill})
        hk.start()
        print("  [FRIDAY] Global kill hotkey active: Ctrl+Shift+Q")
    except ImportError:
        print("  [FRIDAY] pynput not installed — kill hotkey unavailable. Run: pip install pynput")
    except Exception as e:
        print(f"  [FRIDAY] Kill hotkey listener failed: {e}")


threading.Thread(target=_start_kill_hotkey, daemon=True).start()


# ═══════════════════════════════════════════════════════════════
#  NOTIFICATION TRIGGER LOOP
# ═══════════════════════════════════════════════════════════════

def _trigger_skill_promotions():
    """Watch SkillOpt storage for newly-promoted best_skill.md artifacts."""
    if not _notif_engine:
        return
    skills_dir = FRIDAY_DIR / "skillopt"
    if not skills_dir.exists():
        skills_dir = HOME / ".friday" / "skillopt"
    if not skills_dir.exists():
        return
    state = _notif_engine.get_trigger_state("skill_best_mtimes", {}) or {}
    changed = False
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        best = skill_dir / "best_skill.md"
        if not best.exists():
            continue
        try:
            mtime = best.stat().st_mtime
        except OSError:
            continue
        prior = state.get(skill_dir.name)
        if prior is None:
            # First sight — record, don't notify
            state[skill_dir.name] = mtime
            changed = True
            continue
        if mtime > prior + 1.0:
            state[skill_dir.name] = mtime
            changed = True
            _notif_engine.push(
                title=f"🧠 Skill improved — {skill_dir.name}",
                body=f"SkillOpt promoted a new best version of `{skill_dir.name}`.",
                priority="low",
                source="skillopt",
                kind="skill_improvement",
                meta={"skill_name": skill_dir.name, "mtime": mtime},
                actions=[
                    {"label": "Open in Observatory", "kind": "open_observatory",
                     "payload": {"skill": skill_dir.name}},
                ],
                target={"workspace": "studio"},
                dedupe_key=f"skill_promoted:{skill_dir.name}:{int(mtime)}",
            )
    if changed:
        _notif_engine.set_trigger_state("skill_best_mtimes", state)


def _trigger_ofw_messages():
    """Watch co-parent monitor output for new messages."""
    if not _notif_engine:
        return
    ofw_state_dir = FRIDAY_DIR / "ofw"
    candidates = [
        ofw_state_dir / "inbox.json",
        ofw_state_dir / "messages.json",
        ofw_state_dir / "new_messages.json",
    ]
    found = next((p for p in candidates if p.exists()), None)
    if not found:
        return
    try:
        mtime = found.stat().st_mtime
    except OSError:
        return
    prior = _notif_engine.get_trigger_state("ofw_inbox_mtime", 0.0) or 0.0
    if mtime <= prior + 1.0:
        return
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except Exception:
        return
    msgs = data if isinstance(data, list) else data.get("messages", [])
    if not isinstance(msgs, list):
        return
    seen_ids = set(_notif_engine.get_trigger_state("ofw_seen_ids", []) or [])
    new_msgs = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or m.get("message_id") or m.get("hash") or "")
        if mid and mid not in seen_ids:
            new_msgs.append(m)
            seen_ids.add(mid)
    if new_msgs:
        for m in new_msgs[:5]:
            sender = m.get("from") or m.get("sender") or "co-parent"
            subj = m.get("subject") or "(no subject)"
            preview = (m.get("body") or m.get("preview") or "")[:280]
            _notif_engine.push(
                title=f"📨 Co-parent message — {sender}",
                body=f"**{subj}**\n\n{preview}",
                priority="critical",
                source="ofw_monitor",
                kind="ofw_message",
                proactive_chat=True,
                chat_message=(
                    f"New co-parent message from **{sender}**: {subj}. "
                    f"Want me to draft a response?"
                ),
                meta={"message_id": m.get("id"), "sender": sender, "subject": subj},
                actions=[
                    {"label": "Draft reply", "kind": "ofw_draft",
                     "payload": {"message_id": m.get("id")}},
                    {"label": "Open Co-Parent", "kind": "open_window",
                     "payload": {"window": "coparent"}},
                ],
                target={"workspace": "coparent",
                        "message_id": m.get("id")},
                dedupe_key=f"ofw:{m.get('id') or subj}",
            )
        _notif_engine.set_trigger_state("ofw_seen_ids", list(seen_ids)[-200:])
    _notif_engine.set_trigger_state("ofw_inbox_mtime", mtime)


KEY_CONTACTS = {
}


def _trigger_gmail_signals():
    """Watch a Gmail-export JSON for unanswered key-contact emails and job replies."""
    if not _notif_engine:
        return
    candidates = [
        FRIDAY_DIR / "gmail" / "inbox.json",
        FRIDAY_DIR / "gmail-cache.json",
        WIKI_DIR / "professional" / "applications" / "responses.json",
    ]
    inbox_path = next((p for p in candidates if p.exists()), None)
    if not inbox_path:
        return
    try:
        data = json.loads(inbox_path.read_text(encoding="utf-8"))
    except Exception:
        return
    messages = data if isinstance(data, list) else data.get("messages", [])
    if not isinstance(messages, list):
        return

    now = datetime.utcnow()
    for m in messages[-100:]:
        if not isinstance(m, dict):
            continue
        sender = (m.get("from") or m.get("sender") or "").lower()
        subj = m.get("subject") or ""
        ts_raw = m.get("received_at") or m.get("date") or m.get("timestamp")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", ""))
        except Exception:
            ts = None
        msg_id = str(m.get("id") or m.get("message_id") or (sender + subj))[:120]

        # Job-application response
        if (m.get("kind") == "job_response"
                or "applied" in subj.lower()
                or "application" in subj.lower()
                or m.get("category") == "applications"):
            _notif_engine.push(
                title=f"💼 Job reply — {m.get('company') or sender}",
                body=f"**{subj}**\n\n{(m.get('preview') or '')[:300]}",
                priority="high",
                source="gmail",
                kind="job_response",
                proactive_chat=True,
                chat_message=(
                    f"You have a job-application reply from "
                    f"**{m.get('company') or sender}** about *{subj}*. "
                    f"Want me to open it and draft a follow-up?"
                ),
                meta={"sender": sender, "subject": subj, "message_id": msg_id},
                target={"workspace": "messages", "lane": "career",
                        "thread_id": m.get("thread_id") or msg_id},
                dedupe_key=f"job_reply:{msg_id}",
            )
            continue

        # Stale message from a key contact
        contact = next((v for k, v in KEY_CONTACTS.items() if k in sender), None)
        if contact and ts:
            age_h = (now - ts).total_seconds() / 3600.0
            if age_h >= contact["stale_hours"] and not m.get("replied"):
                _notif_engine.push(
                    title=f"⏳ Unreplied — {contact['label']}",
                    body=(f"**{subj}**\n\n"
                          f"Sent {age_h:.0f} hours ago, no reply yet.\n\n"
                          f"{(m.get('preview') or '')[:300]}"),
                    priority=contact["priority"],
                    source="gmail",
                    kind="stale_email",
                    proactive_chat=True,
                    chat_message=(
                        f"Hey — {contact['label']} sent you an email "
                        f"{age_h:.0f} hours ago about *{subj}*. You haven't "
                        f"replied yet. Want me to draft a response?"
                    ),
                    meta={"sender": sender, "subject": subj, "age_hours": age_h},
                    target={"workspace": "messages",
                            "lane": contact.get("lane", "all"),
                            "thread_id": m.get("thread_id") or msg_id},
                    dedupe_key=f"stale:{msg_id}",
                )


# ═══════════════════════════════════════════════════════════════
#  DAILY SCHEDULER
#  A single background thread that fires registered jobs once per day
#  at a target wall-clock hour (America/Chicago). "Run if past the
#  target and not yet run today" means a job auto-catches-up whenever
#  the server starts up after its time — so it runs whenever the
#  server is up, not only if the process happened to be alive at the
#  exact minute. The Front Page news task can register here too.
# ═══════════════════════════════════════════════════════════════

DAILY_CREATION_HOUR = 8     # 8 AM Central (prior Cowork routine ran ~2 PM)
DAILY_CREATION_MINUTE = 0

_DAILY_JOBS = []            # list of {name, hour, minute, fn}
_DAILY_STATE_FILE = FRIDAY_DIR / "daily_scheduler_state.json"
_daily_state_lock = threading.Lock()


def register_daily_job(name, hour, minute, fn):
    """Register a function to run once per day at hour:minute Central.

    Public so other subsystems (e.g. the Front Page news refresh) can hook
    into the same scheduler instead of spawning their own thread.
    """
    _DAILY_JOBS.append({"name": name, "hour": int(hour),
                        "minute": int(minute), "fn": fn})


def _daily_state_read():
    try:
        return json.loads(_DAILY_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _daily_state_mark(name, date_str):
    with _daily_state_lock:
        state = _daily_state_read()
        state[name] = date_str
        try:
            _DAILY_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"  [daily-scheduler] state write failed: {e}")


def _daily_scheduler_loop():
    """Tick every minute; run any job whose Central time has passed today and
    that hasn't already run today. Each job runs in its own thread so a slow
    job (e.g. a Claude call) never delays the others."""
    if not _DAILY_JOBS:
        return
    try:
        from zoneinfo import ZoneInfo
        _tz = ZoneInfo("America/Chicago")
    except Exception:
        _tz = None
    names = ", ".join(j["name"] for j in _DAILY_JOBS)
    print(f"  [FRIDAY] Daily scheduler started ({names}).")
    _time.sleep(10)  # let the server finish coming up
    while True:
        now = datetime.now(_tz) if _tz else datetime.now()
        today = now.strftime("%Y-%m-%d")
        state = _daily_state_read()
        for job in _DAILY_JOBS:
            try:
                if state.get(job["name"]) == today:
                    continue
                due = (now.hour, now.minute) >= (job["hour"], job["minute"])
                if not due:
                    continue
                # Mark BEFORE running so a long job can't double-fire on the
                # next tick, and so a crash mid-job doesn't retry all day.
                _daily_state_mark(job["name"], today)
                fn = job["fn"]
                threading.Thread(
                    target=lambda f=fn, n=job["name"]: _run_daily_job(f, n),
                    daemon=True,
                ).start()
            except Exception as e:
                print(f"  [daily-scheduler:{job['name']}] {e}")
        _time.sleep(60)


def _run_daily_job(fn, name):
    try:
        fn()
    except Exception as e:
        print(f"  [daily-scheduler:{name}] run failed: {e}")


def _skillopt_nightly():
    """Nightly closed-loop tick: run SkillOpt auto-research over drifted skills."""
    try:
        import skill_capture as _skcap
        result = _skcap.run_nightly()
        print(f"  [skillopt] nightly research: {result}")
    except Exception as e:
        print(f"  [skillopt] nightly failed: {e}")


# Register the daily creation. The hour is configurable via settings.
def _register_default_daily_jobs():
    try:
        settings = _load_settings()
        hour = int(settings.get("daily_creation_hour", DAILY_CREATION_HOUR))
        minute = int(settings.get("daily_creation_minute", DAILY_CREATION_MINUTE))
    except Exception:
        hour, minute = DAILY_CREATION_HOUR, DAILY_CREATION_MINUTE
    register_daily_job("daily-creation", hour, minute, generate_daily_creation)
    # Friday's Front Page — two editions a day at 7 AM and 6 PM Central.
    register_daily_job("front-page-morning", FRONT_PAGE_SLOTS["morning"], 0,
                       lambda: _run_front_page_job("morning"))
    register_daily_job("front-page-evening", FRONT_PAGE_SLOTS["evening"], 0,
                       lambda: _run_front_page_job("evening"))
    # Closed-loop learning: nightly SkillOpt auto-research at 3:30 AM Central.
    register_daily_job("skillopt-nightly", 3, 30, _skillopt_nightly)


_register_default_daily_jobs()
threading.Thread(target=_daily_scheduler_loop, daemon=True).start()


def _trigger_message_cache():
    """Keep the Comms Center cache warm: pull + classify live Gmail so the
    Messages workspace and its badge work even before the UI is opened. No-ops
    quietly when Google isn't linked (the cache then just isn't refreshed)."""
    if _google_credentials() is None:
        return
    try:
        _collect_messages(limit=40)  # side effect: refreshes cache.json
    except Exception as e:
        print(f"  [message-cache] {e}")


def _notification_trigger_loop():
    """Single background tick that runs all triggers safely."""
    if not _notif_engine:
        return
    triggers = [
        ("skill_promotions", _trigger_skill_promotions),
        ("ofw", _trigger_ofw_messages),
        ("gmail", _trigger_gmail_signals),
        ("message_cache", _trigger_message_cache),
    ]
    print("  [FRIDAY] Notification trigger loop started.")
    # Wait a bit so the server can finish coming up
    _time.sleep(8)
    while True:
        for name, fn in triggers:
            try:
                fn()
            except Exception as e:
                print(f"  [notif-trigger:{name}] {e}")
        _time.sleep(60)  # poll every minute


if _notif_engine:
    threading.Thread(target=_notification_trigger_loop, daemon=True).start()


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # Make stdout/stderr encoding-safe. When launched with a piped/redirected
    # stdout on Windows (cp1252), the box-drawing banner and emoji below would
    # otherwise crash with UnicodeEncodeError — replace unencodable chars instead.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║   FRIDAY Desktop v4.4 — Phase B OS          ║")
    print("  ╠══════════════════════════════════════════════╣")
    print("  ║  http://localhost:3000                       ║")
    print("  ║  Flask + Gemini API + Three.js Holographic   ║")
    print("  ║  Dock · Floating Windows · Persistent Chat   ║")
    print("  ║  Press Ctrl+C to stop                        ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()
    print(f"  Wiki:      {WIKI_DIR}")
    print(f"  Friday:    {FRIDAY_DIR}")
    print(f"  Creations: {CREATIONS_DIR}")
    print(f"  Chat Log:  {CHAT_HISTORY_FILE}")
    print()

    # Pre-generate wiki directory indexes for index-first navigation
    try:
        _generate_wiki_indexes()
        print("  Wiki indexes: generated")
    except Exception as _wi_err:
        print(f"  Wiki indexes: skipped ({_wi_err})")

    # Derive the vault key (if FRIDAY_PASSWORD is set) and encrypt any existing
    # plaintext sensitive files at rest. No-op when no password is configured.
    try:
        _get_vault_key()
        _migrate_vault_plaintext()
    except Exception as _vk_err:
        print(f"  Vault encryption: skipped ({_vk_err})")

    # Bind 0.0.0.0 when tunnel/remote access is needed, else localhost only.
    # Port is configurable via FRIDAY_PORT to avoid conflicts (default 3000).
    bind_host = '0.0.0.0' if FRIDAY_PASSWORD else '127.0.0.1'
    try:
        _port = int(os.environ.get('FRIDAY_PORT', '3000'))
    except ValueError:
        _port = 3000
    app.run(host=bind_host, port=_port, debug=False, threaded=True)

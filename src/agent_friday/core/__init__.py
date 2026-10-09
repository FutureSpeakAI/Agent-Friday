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
import tempfile
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

# Standalone leaf module (no side effects, does not import this package) —
# see its docstring for why it is safe to import this early in core's own
# init. Used below by _setup_friday_logging() for the OS-mode log-routing
# gate (PR-2 of the OS-mode sequence).
from agent_friday.core.os_mode import is_os_mode
# The single source of truth for Friday's state root (PR-1 of the OS-mode
# sequence). Import-light and side-effect-free by construction — it only
# reaches back into this package lazily, inside runtime_dir(), so importing it
# here cannot recurse. friday_home() honours FRIDAY_HOME; user_home() is the
# host's own home and deliberately does not.
from agent_friday.paths import friday_home, is_redirected, user_home
# Stdlib-only palette module; the login page reads its `:root` token block.
from agent_friday import brand

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


def _res_file(name):
    """Resolve a bundled data file with a repo-root fallback.

    Frozen builds keep data files in _MEIPASS (= _RES_DIR). Source checkouts
    keep the canonical hand-maintained copies (SELF.md, VOICE_DEMO.md) at the
    REPO root — two levels above the package — where the src/ restructure left
    them. Without this fallback those loaders silently returned "" in dev runs
    and Friday lost all knowledge of itself and its own UI.
    """
    p = _RES_DIR / name
    if p.exists():
        return p
    alt = _RES_DIR.parent.parent / name
    return alt if alt.exists() else p

from flask import Flask, jsonify, request, send_from_directory, send_file, session, redirect, url_for, Response, stream_with_context, g
from flask.json.provider import DefaultJSONProvider as _FlaskDefaultJSONProvider
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


# Resilient request-JSON parsing. In the live pythonw process the default
# provider's loads() can raise ValueError for a request body containing raw
# non-ASCII UTF-8 bytes (em-dash, é, …) — get_json(silent=True) swallows it,
# the POST silently degrades to {}, and /api/chat then fails on the resulting
# empty message. The same bytes parse fine under the test client, so the
# trigger is live-process state that has not been pinned down; retry with an
# explicit utf-8 decode and log the original failure (with the offending
# bytes) instead of losing both.
class _ResilientJSONProvider(_FlaskDefaultJSONProvider):
    def loads(self, s, **kwargs):
        try:
            return super().loads(s, **kwargs)
        except ValueError as first_err:
            if not isinstance(s, (bytes, bytearray)):
                raise
            try:
                rv = super().loads(bytes(s).decode("utf-8", "replace"), **kwargs)
            except ValueError as retry_err:
                # Both parses failed — capture everything a post-mortem needs,
                # since the caller (get_json(silent=True)) swallows the error.
                logging.getLogger("friday.core").error(
                    "request JSON unparseable even after utf-8/replace decode "
                    "(%d bytes; first: %s: %s; retry: %s: %s; "
                    "json.loads is %s.%s; head: %r)",
                    len(s), type(first_err).__name__, first_err,
                    type(retry_err).__name__, retry_err,
                    getattr(json.loads, "__module__", "?"),
                    getattr(json.loads, "__qualname__", repr(json.loads)),
                    bytes(s[:120]))
                raise first_err
            logging.getLogger("friday.core").warning(
                "request JSON parsed only after explicit utf-8 decode "
                "(%d bytes; original error: %s: %s; head: %r)",
                len(s), type(first_err).__name__, first_err, bytes(s[:120]))
            return rv


app.json = _ResilientJSONProvider(app)

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
        p = friday_home() / "secret_key"
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

# Request-size cap: bound incoming bodies on every
# endpoint so an oversized POST can't exhaust memory. The default leaves
# generous headroom for audio/image uploads; tune with FRIDAY_MAX_REQUEST_MB.
try:
    _MAX_REQ_MB = max(1, int(os.environ.get("FRIDAY_MAX_REQUEST_MB", "25")))
except ValueError:
    _MAX_REQ_MB = 25
app.config["MAX_CONTENT_LENGTH"] = _MAX_REQ_MB * 1024 * 1024


@app.errorhandler(413)
def _request_too_large(_e):
    return jsonify({
        "error": f"request body too large (limit {_MAX_REQ_MB} MB — "
                 "set FRIDAY_MAX_REQUEST_MB to raise it)"
    }), 413
sock = Sock(app) if _HAS_SOCK else None

# Server start time for uptime reporting
SERVER_START_TS = _time.time()

# ── Bound port ───────────────────────────────────────────────
# The port the server ACTUALLY bound, which is not always the one requested:
# _resolve_bind_port() scans forward when 3000 is busy (a second launch, a
# leftover process), so the app can be serving on 3001+.
#
# Before this was published, the Google OAuth redirect URIs were hardcoded to
# ":3000" while the server might be on another port, so consent would fail with
# redirect_uri_mismatch in exactly the situation the port scan exists to
# survive. server.py sets this the moment the port is resolved; the default
# keeps every import-time caller correct when the server is not running.
SERVER_PORT: int = int(os.environ.get("FRIDAY_PORT", "3000") or 3000)


def set_server_port(port) -> None:
    """Record the port the server actually bound (called once at startup)."""
    global SERVER_PORT
    try:
        SERVER_PORT = int(port)
    except (TypeError, ValueError):
        pass


def server_base_url() -> str:
    """Loopback base URL for the running server, e.g. http://localhost:3001.

    Loopback specifically, never the request host: Google rejects any
    plain-HTTP non-loopback redirect_uri outright, and the app may be reached
    through a hosts-file alias, a LAN address or a tunnel. Only the PORT is
    dynamic — see services/calendar_engine._google_redirect_uri.
    """
    return f"http://localhost:{SERVER_PORT}"

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

# Token rotation: a token minted at startup used to
# live for the entire — possibly weeks-long — process lifetime, so a captured
# token stayed valid until the next restart. It now rotates every
# FRIDAY_API_TOKEN_ROTATE_HOURS (default 24 h; 0 disables). Rotation is LAZY —
# checked on every use — so no background thread is needed, and the served HTML
# always embeds the CURRENT token (serve_ui calls _current_api_token()). The
# previous token stays valid for a short grace window so a page loaded moments
# before rotation isn't instantly broken mid-session.
_API_TOKEN_LOCK = threading.Lock()
try:
    _API_TOKEN_ROTATE_S: float = max(
        0.0, float(os.environ.get("FRIDAY_API_TOKEN_ROTATE_HOURS", "24")) * 3600.0)
except ValueError:
    _API_TOKEN_ROTATE_S = 24 * 3600.0
_API_TOKEN_GRACE_S = 300.0
_API_TOKEN_ISSUED_AT: float = _time.time()
_API_SESSION_TOKEN_PREV: str = ""


def _current_api_token() -> str:
    """Return the active API session token, rotating it first when expired."""
    global _API_SESSION_TOKEN, _API_SESSION_TOKEN_PREV, _API_TOKEN_ISSUED_AT
    if _API_TOKEN_ROTATE_S <= 0:
        return _API_SESSION_TOKEN
    with _API_TOKEN_LOCK:
        if _time.time() - _API_TOKEN_ISSUED_AT >= _API_TOKEN_ROTATE_S:
            _API_SESSION_TOKEN_PREV = _API_SESSION_TOKEN
            _API_SESSION_TOKEN = secrets.token_hex(32)  # pragma: allowlist secret
            _API_TOKEN_ISSUED_AT = _time.time()
        return _API_SESSION_TOKEN


def _api_token_valid(tok) -> bool:
    """Constant-time check of an X-Friday-Token value against the current (or,
    within a short grace window after rotation, the previous) session token."""
    if not tok or not isinstance(tok, str):
        return False
    if _hmac.compare_digest(tok, _current_api_token()):
        return True
    if (_API_SESSION_TOKEN_PREV
            and _time.time() - _API_TOKEN_ISSUED_AT < _API_TOKEN_GRACE_S
            and _hmac.compare_digest(tok, _API_SESSION_TOKEN_PREV)):
        return True
    return False

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

#: Headers that exist only because something FORWARDED the request. Their
#: presence means `remote_addr` is a PROXY's address rather than the client's, so
#: the peer address alone cannot answer "is the client this machine".
#:
#: THIS IS A SECURITY BOUNDARY, and it has been wrong in both directions.
#:
#: Too permissive: a launcher that runs
#: `cloudflared tunnel --url http://localhost:3000` publishes the whole API on
#: a public trycloudflare URL. cloudflared connects from LOOPBACK, so every
#: tunnelled request arrives with remote_addr == '127.0.0.1', reads as local, and
#: is auto-authenticated as the machine's owner -- `@login_required` routes and
#: the approval endpoints included.
#:
#: Too strict: treating the mere PRESENCE of a forwarding header as proof of
#: remoteness locks the owner out of their own machine. The UI is browsed at
#: `https://agent.friday`, which is Friday's OWN presentation proxy --
#: `ops/Caddyfile`, `bind 127.0.0.1 ::1`, `reverse_proxy 127.0.0.1:3000` -- and
#: Caddy adds `X-Forwarded-For: 127.0.0.1`. The owner is then asked for a
#: password that exists only because a launcher sets `FRIDAY_PASSWORD`.
#:
#: So the question is not "was this forwarded" but "was it forwarded from
#: somewhere on THIS MACHINE". A forwarded request is local only when all of:
#:
#:   * the immediate peer is loopback (the proxy runs here), AND
#:   * no CDN/tunnel header is present -- any `CF-*` still means remote, AND
#:   * every claimed client address in the chain is itself loopback, AND
#:   * the forwarded host, if given, is a local alias, not a public name.
#:
#: cloudflared cannot satisfy that: Cloudflare's edge sets `CF-Connecting-IP`
#: and appends the real client address to `X-Forwarded-For`, and a client cannot
#: strip either. Two independent checks, so neither one carries the boundary
#: alone. A tunnel that forwarded NO headers at all would still look like plain
#: loopback -- that is what `FRIDAY_TRUST_LOOPBACK=0` is for, and it is why
#: nothing here is a substitute for not running a public tunnel.

#: Headers whose value is a claimed CLIENT address (or chain of them).
_FORWARDED_CLIENT_HEADERS = (
    'x-forwarded-for', 'x-real-ip', 'true-client-ip', 'x-cluster-client-ip',
)

#: Header name prefixes that mean a CDN or tunnel handled this. Matched by
#: PREFIX because Cloudflare adds a family of these and can add more; a fixed
#: list needs maintaining, and the maintenance is what fails.
_CDN_HEADER_PREFIXES = ('cf-',)

#: Hosts a local proxy may legitimately present. `agent.friday` is the hosts-file
#: alias Caddy serves; anything else public (e.g. *.trycloudflare.com) is not us.
#: The configured agent.<name> is added at request time (_forwarded_host_is_local).
_LOCAL_FORWARDED_HOSTS = {
    'agent.friday', 'localhost', '127.0.0.1', '::1', '[::1]',
}


def _is_loopback_addr(addr) -> bool:
    """True for 127.0.0.0/8, ::1, and IPv4-mapped forms of them."""
    a = str(addr or '').strip().strip('"')
    if not a:
        return False
    if a.startswith('['):                       # [::1]:1234
        a = a[1:].split(']', 1)[0]
    elif a.count(':') == 1 and '.' in a:        # 127.0.0.1:1234
        a = a.split(':', 1)[0]
    if a.startswith('::ffff:'):
        a = a[7:]
    a = a.lower()
    if a in ('::1', 'localhost'):
        return True
    try:
        import ipaddress
        return ipaddress.ip_address(a).is_loopback
    except Exception:
        return a.startswith('127.')


def _forwarded_client_addrs():
    """Every client address this request CLAIMS, across all forwarding headers.

    Returns None when no forwarding header is present at all (a direct request),
    which is different from returning an empty list (headers present but
    unparseable -- treated as untrustworthy by the caller).
    """
    try:
        h = request.headers
    except Exception:
        return []
    found = False
    addrs = []
    for name in _FORWARDED_CLIENT_HEADERS:
        raw = h.get(name)
        if raw is None:
            continue
        found = True
        addrs.extend(p.strip() for p in str(raw).split(',') if p.strip())
    # RFC 7239: Forwarded: for=127.0.0.1;proto=https, for=...
    raw = h.get('forwarded')
    if raw is not None:
        found = True
        for element in str(raw).split(','):
            for pair in element.split(';'):
                k, _, v = pair.partition('=')
                if k.strip().lower() == 'for' and v.strip():
                    addrs.append(v.strip())
    # `X-Forwarded-Host`/`-Proto`/`-Port` and `Via` carry no client address, but
    # their presence still proves a hop happened.
    if not found:
        for name in ('x-forwarded-host', 'x-forwarded-proto',
                     'x-forwarded-port', 'via'):
            if h.get(name) is not None:
                found = True
                break
    return addrs if found else None


def _has_cdn_header() -> bool:
    """True when a CDN or tunnel handled this request. Always disqualifying."""
    try:
        names = [k.lower() for k in request.headers.keys()]
    except Exception:
        return True                      # cannot prove it was not; assume it was
    return any(n.startswith(p) for n in names for p in _CDN_HEADER_PREFIXES)


def _forwarded_host_is_local() -> bool:
    """True when `X-Forwarded-Host` is absent or names a local alias.

    Defence in depth: cloudflared sets this to the public `*.trycloudflare.com`
    hostname, so a request claiming a loopback chain while presenting a public
    host is lying about one of the two.
    """
    try:
        host = request.headers.get('x-forwarded-host')
    except Exception:
        return False
    if not host:
        return True
    first = str(host).split(',')[0].strip().lower()
    bare = first.rsplit(':', 1)[0] if first.count(':') == 1 else first
    if first in _LOCAL_FORWARDED_HOSTS or bare in _LOCAL_FORWARDED_HOSTS:
        return True
    # The address the person configured for this PC (agent.<their agent's
    # name>, services/local_address.py) is as local as agent.friday. Only ever
    # agent.<one word>, validated when set, so it cannot name a tunnel host --
    # and the CF-* and whole-chain checks in _looks_proxied still apply to it.
    try:
        from agent_friday.services.local_address import local_hosts
        return bare in local_hosts()
    except Exception:
        return False


def _looks_proxied():
    """True when the request was forwarded from somewhere NOT on this machine.

    Kept as the name the rest of the file and the tests use. It no longer means
    "carries a forwarding header" -- Friday's own loopback proxy does that -- it
    means "forwarded in a way this machine cannot vouch for".
    """
    if _has_cdn_header():
        return True
    claimed = _forwarded_client_addrs()
    if claimed is None:
        return False                     # no forwarding evidence at all
    if not claimed:
        return True                      # a hop happened, no address to check
    if not _forwarded_host_is_local():
        return True
    return not all(_is_loopback_addr(a) for a in claimed)


def _is_local_request():
    """True if the current request originates from this machine.

    Every trust decision in the app routes through here -- the HTTP decorator,
    the settings gate, the voice WebSocket -- so this is the one place the
    distinction has to hold.
    """
    try:
        addr = (request.remote_addr or '').strip()
    except Exception:
        return False
    if not addr:
        return False
    # The immediate peer must be on this machine, whether it is the browser or
    # a proxy running here. Without this, a loopback-looking forwarded chain
    # from a real remote peer would be believed.
    if not _is_loopback_addr(addr):
        return False
    return not _looks_proxied()

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
        # Loopback stays trusted even with no key; a NON-loopback caller with no
        # key configured is denied (fail-closed) rather than allowed through.
        if not _HTTP_AUTH_KEY:
            if _loopback_trusted():
                return f(*args, **kwargs)
            return jsonify({"error": "remote access disabled: no FRIDAY_REMOTE_KEY set"}), 403
        if _loopback_trusted():
            session['authenticated'] = True
            return f(*args, **kwargs)
        # Accept the ephemeral rotating token embedded in the UI HTML.
        if _api_token_valid(request.headers.get("X-Friday-Token")):
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
<title>Sign in · {{ product }}</title>
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
<link href="/static/fonts/fonts.css" rel="stylesheet">
<style>
/*BRAND_TOKENS*/
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--fr-surface);color:var(--fr-text);font-family:var(--fr-font-body);display:flex;align-items:center;justify-content:center;min-height:100vh;overflow:hidden}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(ellipse at 50% 50%,rgba(0,212,255,.10) 0%,transparent 70%);pointer-events:none}
.login-box{background:var(--fr-glass);border:1px solid rgba(0,212,255,.3);border-radius:12px;padding:40px 36px;width:340px;backdrop-filter:var(--fr-glass-blur);box-shadow:0 0 40px rgba(0,212,255,.12),inset 0 0 30px rgba(0,212,255,.04);position:relative}
.login-box::before{content:'';position:absolute;top:-1px;left:20%;right:20%;height:2px;background:linear-gradient(90deg,var(--fr-cyan),var(--fr-violet),var(--fr-magenta));opacity:.8;border-radius:2px}
h1{font-family:var(--fr-font-display);font-size:14px;letter-spacing:.25em;text-align:center;color:var(--fr-cyan);margin-bottom:6px}
.maker{font-size:var(--fr-text-2xs);text-align:center;color:var(--fr-dim);margin-bottom:10px}
.maker b{font-family:var(--fr-font-display);font-weight:700;color:var(--fr-wordmark-amber)}
.subtitle{font-size:var(--fr-text-2xs);letter-spacing:var(--fr-track-label);text-align:center;color:var(--fr-dim);margin-bottom:32px}
.field{margin-bottom:12px}
input[type=email],input[type=text],input[type=password]{width:100%;padding:12px 16px;background:var(--fr-cyan-soft);border:1px solid rgba(0,212,255,.25);border-radius:6px;color:var(--fr-text);font-family:var(--fr-font-body);font-size:var(--fr-text-md);outline:none;transition:border-color .3s}
input[type=email]:focus,input[type=text]:focus,input[type=password]:focus{border-color:rgba(0,212,255,.7);box-shadow:0 0 15px rgba(0,212,255,.15)}
input::placeholder{color:var(--fr-faint);font-size:var(--fr-text-xs);letter-spacing:var(--fr-track-label)}
button{width:100%;padding:12px;margin-top:4px;background:var(--fr-cyan-soft);border:1px solid rgba(0,212,255,.4);border-radius:6px;color:var(--fr-cyan);font-family:var(--fr-font-body);font-size:var(--fr-text-sm);cursor:pointer;transition:all .3s}
button:hover{background:rgba(0,212,255,.22);border-color:rgba(0,212,255,.7);box-shadow:0 0 20px rgba(0,212,255,.2)}
.error{color:var(--fr-deny);font-size:var(--fr-text-2xs);text-align:center;margin-top:12px;letter-spacing:.1em}
.scan-line{position:fixed;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,rgba(0,212,255,.15),transparent);animation:scan 4s linear infinite;pointer-events:none}
@keyframes scan{0%{top:0}100%{top:100vh}}
</style>
<link rel="stylesheet" href="/static/friday_shared_surfaces.css">
</head>
<body class="friday-experience-enabled friday-login-surface">
<div class="scan-line"></div>
<div class="login-box">
<h1>{{ product_upper }}</h1>
<div class="maker">by <b>{{ maker }}</b></div>
<div class="subtitle">Sign in to your Friday</div>
<form method="POST">
<div class="field"><label for="friday-login-user">Email</label><input id="friday-login-user" type="email" name="username" placeholder="EMAIL / USERNAME" autofocus autocomplete="username"></div>
<div class="field"><label for="friday-login-password">Password</label><input id="friday-login-password" type="password" name="password" placeholder="PASSWORD" autocomplete="current-password"></div>
<button type="submit">Sign in</button>
</form>
{{ error }}
</div>
</body>
</html>""".replace("/*BRAND_TOKENS*/", brand.css_root_block()).replace(
    "{{ product }}", brand.PRODUCT_NAME).replace(
    "{{ product_upper }}", brand.PRODUCT_NAME.upper()).replace("{{ maker }}", brand.MAKER_NAME)

# The two login banners are fixed, code-owned strings — no user input reaches
# them today, so there is no live XSS. But LOGIN_HTML.replace('{{ error }}', error)
# bypasses Jinja auto-escaping entirely: the day someone echoes the attempted
# username into `error` (a common UX tweak) it becomes reflected XSS. This
# allowlist makes that regression impossible — only the two known banners render:
# anything else is escaped to inert text.
_ALLOWED_LOGIN_ERRORS = {
    '<div class="error">TOO MANY ATTEMPTS — WAIT AND RETRY</div>',
    '<div class="error">ACCESS DENIED — INVALID CREDENTIALS</div>',
}

def _login_error_html(error: str) -> str:
    if not error:
        return ""
    if error in _ALLOWED_LOGIN_ERRORS:
        return error
    from markupsafe import escape as _esc
    return f'<div class="error">{_esc(error)}</div>'

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
            html = LOGIN_HTML.replace('{{ error }}', _login_error_html(error))
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
    html = LOGIN_HTML.replace('{{ error }}', _login_error_html(error))
    return Response(html, content_type='text/html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ── Vibe Code: Terminal State ─────────────────────────────────
VIBE_TERMINALS = {}   # id -> { id, task, status, cwd, pid, started, stopped, log_file }
VIBE_LOG_DIR = friday_home() / "vibe-code-logs"
VIBE_LOG_DIR.mkdir(parents=True, exist_ok=True)

# `VIBE_TERMINALS` lives only in memory, so a restart forgets every vibe-code
# terminal it launched while the cmd.exe window it spawned — running
# `claude --dangerously-skip-permissions` somewhere under ~/Projects — keeps
# running, unmanaged. Same disease residency_arbiter.endpoints_path() exists
# to cure for llama-server seats: persist what is running to disk so a
# restart can find it again, rather than only ever knowing what THIS process
# started. code_engine.adopt_or_reap_vibe_terminals() reads this back at boot.
VIBE_STATE_FILE = friday_home() / "vibe-code" / "terminals.json"
VIBE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

# Bumped when the MEANING of the file changes, not its contents. Version 1 is
# the first format written by a process that also records terminals at launch,
# so a version-1 file is evidence that an absent terminal_id is genuinely
# absent. A file with no version was written before that guarantee held (or by
# the reconcile itself, which created an empty one at first boot), and an
# absence in it proves nothing -- see adopt_or_reap_vibe_terminals.
VIBE_STATE_VERSION = 1


def _persist_vibe_terminals() -> None:
    """Write VIBE_TERMINALS to disk. Atomic (tmp-then-replace), best-effort.

    Same shape as residency_arbiter._publish_endpoints: a process that cannot
    persist its terminal list is still a working process for whoever owns it
    right now, so a write failure here is swallowed rather than raised.

    The temp file carries the pid so two writers cannot land on one name. A
    shared temp name is exactly the defect _save_settings guards against:
    concurrent writers interleave into a half-written file. Here that file is
    the sole evidence deciding whether a live terminal gets force-killed, so a
    torn write is not merely lost state.
    """
    try:
        tmp = VIBE_STATE_FILE.with_suffix(".json.%d.tmp" % os.getpid())
        tmp.write_text(json.dumps({"version": VIBE_STATE_VERSION,
                                   "terminals": VIBE_TERMINALS}, indent=2),
                       encoding="utf-8")
        os.replace(tmp, VIBE_STATE_FILE)
    except Exception:
        pass


def _read_vibe_terminals_state() -> dict:
    """`terminal_id -> last-known record` from disk. Never raises; {} on any problem."""
    return _read_vibe_state().get("terminals", {})


def _read_vibe_state() -> dict:
    """The whole persisted vibe-terminal document. Never raises.

    Returns `{}` when the file is missing or unreadable -- deliberately
    indistinguishable from a file that exists but names no version, because
    neither can testify that a terminal was never recorded.
    """
    try:
        data = json.loads(VIBE_STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        terms = data.get("terminals") or {}
        out = {"terminals": {str(k): v for k, v in terms.items()
                             if isinstance(v, dict)}}
        if isinstance(data.get("version"), int):
            out["version"] = data["version"]
        return out
    except Exception:
        return {}

# ── Paths ─────────────────────────────────────────────────────
# Two different questions, two different answers — conflating them is what
# made FRIDAY_HOME decorative: a setting that some paths honour and others
# ignore isolates nothing.
#
#   HOME       — the human's own home directory. Desktop, ~/Projects, the
#                sandbox root that bounds which files Friday may read, the
#                legacy ~/wiki. These are facts about the machine, and a
#                FRIDAY_HOME redirect must NOT move them: pointing the sandbox
#                root at an empty temp directory would not isolate anything,
#                it would just stop Friday reading the files it was asked about.
#   FRIDAY_DIR — where Friday keeps ITS state: settings.json, the vault,
#                conversation memory, the wiki, credentials. This is what a
#                test run, an eval harness, a kiosk image or an unattended
#                agent needs pointed away from the real user, and this is what
#                FRIDAY_HOME redirects.
#
# This module is the one most of the codebase resolves through — services/,
# routes/ and the settings loader all import FRIDAY_DIR from here — so a
# FRIDAY_HOME that this line ignores is a FRIDAY_HOME that does nothing where
# it matters, however many peripheral modules honour it.
HOME = user_home()
FRIDAY_DIR = friday_home()
WIKI_DIR = FRIDAY_DIR / "wiki"

# ── Local runtime stack (decision D7) ────────────────────────
# Home for the heavyweight LOCAL inference artifacts Friday orchestrates but
# does not ship: GGUF weights, the llama.cpp binaries, ComfyUI and its image
# models, the voice venvs. These are large (tens of GB), machine-specific, and
# regenerable — so they live beside the app's other data under ~/.friday rather
# than in a directory invented per-machine, which is what D7 settles.
#
# Overridable, in precedence order, because the default lands on the system
# drive and these artifacts are exactly what a user wants on a bigger disk:
#   1. FRIDAY_RUNTIME_DIR env var
#   2. settings.json  "runtime_dir"
#   3. ~/.friday/runtime
#
# Resolved lazily by runtime_dir() rather than frozen at import: settings are
# not loadable this early in module init.
DEFAULT_RUNTIME_DIR = FRIDAY_DIR / "runtime"


def runtime_dir() -> Path:
    """Resolve the local runtime-stack root. Never raises."""
    env = os.environ.get("FRIDAY_RUNTIME_DIR")
    if env:
        return Path(os.path.expanduser(env))
    try:
        configured = (_load_settings() or {}).get("runtime_dir")
    except Exception:
        configured = None
    if configured:
        return Path(os.path.expanduser(str(configured)))
    return DEFAULT_RUNTIME_DIR

# Migrate wiki from ~/wiki (legacy location) to ~/.friday/wiki.
#
# The original guard here was `not WIKI_DIR.exists()` — all-or-nothing. On
# long-lived installs that was ALWAYS False (auto-briefings had created
# ~/.friday/wiki long before the move), so the user's real wiki at ~/wiki was
# silently orphaned and the Wiki UI showed only briefings — a "my wiki is
# gone!" data scare. This merge is per-FILE and idempotent: copy every legacy
# file whose destination is missing, never overwrite anything, and rename the
# legacy dir (preserving it) only after a fully successful merge.
_LEGACY_WIKI = HOME / "wiki"


def _merge_legacy_wiki():
    if not _LEGACY_WIKI.exists() or not _LEGACY_WIKI.is_dir():
        return
    import logging as _log
    import shutil as _shutil
    logger = _log.getLogger(__name__)
    copied, skipped, failed = 0, 0, 0
    for src in _LEGACY_WIKI.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(_LEGACY_WIKI)
        dst = WIKI_DIR / rel
        if dst.exists():
            skipped += 1   # never overwrite — ~/.friday/wiki may have newer auto-content
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(str(src), str(dst))
            copied += 1
        except Exception as _ce:
            failed += 1
            logger.warning("wiki merge: could not copy %s: %s", rel, _ce)
    logger.warning("wiki merge ~/wiki → ~/.friday/wiki: %d copied, %d already present, %d failed",
                   copied, skipped, failed)
    if failed == 0:
        try:
            _LEGACY_WIKI.rename(_LEGACY_WIKI.parent / "wiki_migrated_to_friday")
        except Exception as _re:
            logger.warning("wiki merge: legacy rename failed (data intact at ~/wiki): %s", _re)


try:
    # Under a FRIDAY_HOME redirect this migration is both pointless and
    # destructive: there is no legacy wiki belonging to an instance whose state
    # lives elsewhere, and _merge_legacy_wiki() ends by RENAMING ~/wiki — a
    # write to the very directory the redirect promised not to touch. A test
    # run that renamed a user's ~/wiki out from under them would be a data
    # scare caused by the safety mechanism.
    if is_redirected():
        _log.debug("skipping legacy ~/wiki migration: FRIDAY_HOME is set")
    else:
        _merge_legacy_wiki()
except Exception as _mig_err:
    import logging as _log
    _log.getLogger(__name__).warning(
        "wiki migration ~/wiki → ~/.friday/wiki failed: %s", _mig_err)
# Captured ONCE, before anything creates ~/.friday: True only for a pristine
# first run. Drives the `show_all_workspaces` default — existing installs keep
# the full dock; fresh installs get the trimmed core set from the setup wizard.
_FRESH_INSTALL = not FRIDAY_DIR.exists()
# The Desktop gallery is a convenience for a real user at a real machine. A
# redirected instance has no business creating folders on the host's Desktop,
# so it keeps its creations inside its own state directory.
_desktop = HOME / "Desktop"
CREATIONS_DIR = ((_desktop / "friday-creations")
                 if (_desktop.exists() and not is_redirected())
                 else (FRIDAY_DIR / "friday-creations"))
# Daily Creation archive — JSON artifacts Friday generates once a day on a
# background schedule (distinct from the Desktop media gallery above).
DAILY_CREATIONS_DIR = FRIDAY_DIR / "creations"
WIKI_PROFESSIONAL_DIR = WIKI_DIR / "professional"
JOB_SEARCH_FILE = WIKI_PROFESSIONAL_DIR / "job-search.md"

# Ensure creations dirs exist
CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
DAILY_CREATIONS_DIR.mkdir(parents=True, exist_ok=True)


class _JournaldSingleLineFormatter(logging.Formatter):
    """A record's traceback (and any embedded newline in the message itself)
    normally spans multiple lines. journald treats each LINE written to
    stderr as a separate, independent log entry — a multi-line traceback
    therefore arrives as several unrelated-looking entries with no way to
    tell they belong together, and the priority/unit metadata journald
    attaches is per-line, not per-record. Collapsing to one line (and never
    emitting ANSI color codes, which this formatter — unlike Rich's console
    formatting used elsewhere in this codebase — never adds in the first
    place) keeps one record == one journal entry, which is the whole point
    of "journald-friendly"."""

    def format(self, record) -> str:
        return super().format(record).replace("\r\n", " | ").replace("\n", " | ")


# ── File logging setup ─────────────────────────────────────────
# Now that FRIDAY_DIR is known, wire the rotating file handler. This runs once
# at import time. Using pythonw (no console) makes this the only debug output.
class _ResilientRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A log that cannot rotate keeps logging instead of going dark.

    On Windows a rename fails with PermissionError [WinError 32] while any
    other process holds the file open, and this app runs two nested server.py
    processes that both open friday.log. The stock handler raises that through
    handleError, which prints "--- Logging error ---" and a full traceback to
    stderr for every record it could not write.

    Without this, friday.log freezes at its roll size (10,485,736 bytes) and
    never advances again while server_stderr.log grows at gigabytes an hour.
    A dark friday.log is precisely the signal readers use to conclude the
    process has silently hung, so a working Friday looks like a wedged one.

    An oversized log is a far smaller problem than no log, so a roll that
    cannot happen is announced once and then skipped, with a backoff so the
    rename is not retried per record. The backoff expires, because the other
    holder eventually exits and rotation should resume by itself.
    """

    _warned = False
    _RETRY_AFTER_S = 300.0

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._retry_after = 0.0

    def shouldRollover(self, record):
        if _time.time() < getattr(self, "_retry_after", 0.0):
            return 0
        try:
            return super().shouldRollover(record)
        except Exception:
            return 0

    def doRollover(self):
        try:
            super().doRollover()
            self._retry_after = 0.0
        except Exception as e:
            # Do not re-attempt the rename on every record: the attempt is a
            # filesystem round trip and it keeps failing while the other
            # process lives.
            self._retry_after = _time.time() + self._RETRY_AFTER_S
            if not _ResilientRotatingFileHandler._warned:
                _ResilientRotatingFileHandler._warned = True
                try:
                    sys.stderr.write(
                        "[FRIDAY] friday.log cannot rotate (%s); continuing to "
                        "append to the current file%s" % (e, chr(10)))
                    sys.stderr.flush()
                except Exception:
                    pass
            # super().doRollover() closes the stream before renaming, so
            # reopen it or every later record is silently dropped.
            try:
                if self.stream is None:
                    self.stream = self._open()
            except Exception:
                pass


def _setup_friday_logging() -> None:
    root = logging.getLogger("friday")
    if root.handlers:
        return  # already configured (e.g. tests that import core twice)
    root.setLevel(logging.DEBUG)

    # Kiosk image (FRIDAY_OS_MODE=1) with FRIDAY_LOG_TARGET=stdout: there is
    # no tray to read ~/.friday/friday.log from and no user to open it — the
    # sealed image's only consumer is journald, which captures the unit's
    # stdout/stderr. Route everything to stderr in a single-line-per-record
    # format instead of the rotating file used on every other install; do NOT
    # also add the file handler, so this is a real routing change and not
    # just an extra mirror. Any other combination (OS mode off, or OS mode on
    # without this env var) keeps the exact pre-existing Windows-default
    # behavior below untouched.
    if is_os_mode() and os.environ.get("FRIDAY_LOG_TARGET") == "stdout":
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(_JournaldSingleLineFormatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        root.addHandler(sh)
        return

    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        fh = _ResilientRotatingFileHandler(
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
# What THIS process put into the environment from a launch script, as opposed
# to what the user set in Windows. The difference decides who wins when an
# explicitly-saved key disagrees with start.bat -- see
# credential_store.bootstrap_provider_env. Populated by the call below; empty
# until then, which is the conservative reading.
ENV_FROM_LAUNCH_SCRIPTS: set = set()


def _bootstrap_env_from_launch_scripts():
    # The test suite never reads a developer's launch scripts: they hold the
    # machine's real provider keys (forced over the environment, below) and
    # its secrets, and a test that runs against them behaves differently from
    # the same test in CI, or makes a paid call.
    if os.environ.get("FRIDAY_TESTING") == "1":
        return
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
    loaded = set()
    sources = []
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
                    loaded.add(name)
                    if fname not in sources:
                        sources.append(fname)
        except Exception as _be:
            print(f"  [FRIDAY] env bootstrap skipped {fname}: {_be}")
    # Publish WHICH names came from a launch script. A value this process
    # read out of start.bat is not the same kind of fact as a variable the
    # user set in Windows, and until now nothing downstream could tell them
    # apart -- so a key saved in Settings lost to start.bat on every boot.
    global ENV_FROM_LAUNCH_SCRIPTS
    ENV_FROM_LAUNCH_SCRIPTS = set(loaded)
    if loaded:
        # Do NOT name the variables. This runs at package import, so it fires on
        # every CLI command, every test run and the server itself, printing to
        # stdout — which lands in terminals users paste into bug reports and in
        # server_stderr.log when the tray captures the child. The set included
        # FRIDAY_PASSWORD and FRIDAY_SECRET_KEY. Values were never printed, but
        # publishing the inventory of which secrets a machine holds is free
        # reconnaissance for no operational benefit; the count and the source
        # file are what anyone debugging this actually needs.
        #
        # `loaded` is also a set now. It was a list, so a variable present in
        # two launch scripts was counted twice while the printed list was
        # deduped — the message read "12 key(s)" above a list of 9.
        print(f"  [FRIDAY] Loaded {len(loaded)} environment variable(s) from "
              f"{', '.join(sources)}. Run `friday doctor` to see which are set.")


_bootstrap_env_from_launch_scripts()

# Re-derive the auth/vault constants now that launch-script vars are loaded.
# They are computed near the top of this module — BEFORE the bootstrap above
# runs — so a FRIDAY_PASSWORD / FRIDAY_REMOTE_KEY / FRIDAY_VAULT_PASSPHRASE
# that lives only in start.bat would otherwise silently leave HTTP auth
# keyless (every remote request denied since the fail-closed fix) and the
# vault in PLAINTEXT mode, even though the operator configured a password.
# os.environ still wins: we only fill values that were empty pre-bootstrap.
if not FRIDAY_PASSWORD:
    FRIDAY_PASSWORD = os.environ.get("FRIDAY_PASSWORD", "")
if not FRIDAY_VAULT_PASSPHRASE:
    FRIDAY_VAULT_PASSPHRASE = (
        os.environ.get("FRIDAY_VAULT_PASSPHRASE", "") or FRIDAY_PASSWORD
    )
if not _HTTP_AUTH_KEY:
    _HTTP_AUTH_KEY = (
        os.environ.get("FRIDAY_REMOTE_KEY", "") or FRIDAY_PASSWORD
    )

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


# ── ElevenLabs (text-to-speech) ────────────────────────────────
# Read here so services/elevenlabs_tools.py follows the same key-resolution
# path as every other provider: env first, settings.json as the wizard-saved
# fallback. A real key starts with "sk_"; the short hex string shown beside a
# key in the ElevenLabs dashboard is the key *id* and will be rejected by the
# API with `api_key_id_used_as_api_key`.
# Env only at module scope — _load_settings() is defined further down this file,
# so calling it here is a NameError at import time. The settings.json fallback
# happens lazily in services/elevenlabs_tools._api_key(), which is the same
# shape as get_genai_client()/get_anthropic_client() re-checking on first use.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")  # pragma: allowlist secret

# -- Inworld (cloud voice, Tier-3 sibling) ---------------------------
# Same lazy shape as ELEVENLABS_API_KEY above: env only at module scope, with
# the settings.json fallback happening in services/cloud_voice.py:_api_key().
# Whether Inworld has an ElevenLabs-style key-id trap is UNVERIFIED (spec Q5),
# so no format assertion is made about this value anywhere.
INWORLD_API_KEY = os.environ.get("INWORLD_API_KEY", "")  # pragma: allowlist secret


# ── Anthropic Claude (text reasoning + chat) ───────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL_DEFAULT = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
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
# The one signed receipt file. governance/action_gate._receipt writes every
# entry, including the privilege-ring check's.
DECISION_BOM_FILE = FRIDAY_DIR / "decision-bom.jsonl"
# Where the ring check used to write its own entries. Nothing writes it now;
# an existing file is left in place as history.
LEGACY_DECISION_BOM_FILE = FRIDAY_DIR / "vault" / "decision-bom.jsonl"


def _context_logging_enabled():
    try:
        s = _load_settings()
        # Default ON unless explicitly disabled.
        return bool(s.get('context_logging_enabled', True))
    except Exception:
        return True


def _off_record_now():
    try:
        from agent_friday.services import off_record as _off
        return _off.active()
    except Exception:
        return False


def _log_context(event_type, data):
    """Append an event to today's full context log. Silently no-ops if disabled
    or off the record (services/off_record)."""
    try:
        if not _context_logging_enabled():
            return
        from agent_friday.services import off_record as _off
        if _off.skip("context_log"):
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

# Block list for run_command.
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

# The blocklist is matched as COMMAND TOKENS, never as substrings. A bare
# `token in command.lower()` test would make "del " match inside "model ",
# so a read-only `run_command("... ollama list model ...")` would be refused
# as destructive; every short token here (del, rd, rm, iex, ...) is one or
# two letters from colliding with an ordinary word the same way. Each token
# is compiled with a left AND right word boundary: `del ` matches after
# whitespace, a shell separator (;|&), or the start of the string, and stops
# matching mid-word on either side. Entries that contain `.*`
# ("wmic.*delete", "get-childitem.*remove") are regex fragments and are
# compiled as such.
_RUN_COMMAND_BLOCKLIST_PATTERNS = None


def _compiled_run_command_blocklist():
    global _RUN_COMMAND_BLOCKLIST_PATTERNS
    if _RUN_COMMAND_BLOCKLIST_PATTERNS is None:
        compiled = []
        for token in _RUN_COMMAND_BLOCKLIST:
            if ".*" in token:
                compiled.append((token, re.compile(token)))
            else:
                # lstrip only: a token's TRAILING space/character is load-
                # bearing ("del " must not also match "delete"), only its
                # (now-redundant) leading whitespace hack is normalized away.
                stripped = token.lstrip()
                pattern = r"(?<![\w-])" + re.escape(stripped)
                # A token ending in a word character ("rmdir", "shutdown",
                # ...) also needs a RIGHT boundary, or it still matches
                # mid-word ("rmdir-like"). One already ending in a non-word
                # character ("del ", "rm -") is self-bounded there — its
                # trailing space/dash IS the boundary, and requiring a
                # second one would wrongly refuse "rm -rf" (the character
                # right after "rm -" is a word char by design).
                if re.match(r"\w", stripped[-1]):
                    pattern += r"(?![\w-])"
                compiled.append((token, re.compile(pattern)))
        _RUN_COMMAND_BLOCKLIST_PATTERNS = compiled
    return _RUN_COMMAND_BLOCKLIST_PATTERNS


def blocked_command_token(command: str):
    """The first blocklist token `command` genuinely matches as a command
    fragment (word/token-aware — never a bare substring), or None.

    The ONE place this check happens; every run_command call site imports
    this instead of re-running its own `token in low` loop, so a future
    blocklist edit or matching fix needs one change, not three.
    """
    low = (command or "").lower()
    for token, pattern in _compiled_run_command_blocklist():
        if pattern.search(low):
            return token
    return None


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
        bad = blocked_command_token(cmd)
        if bad is not None:
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


# ═══ TURN LIVENESS ═══════════════════════════════════════════════
# "Is this chat turn still working, or has it gone silent?" — two conditions
# that a stopwatch cannot tell apart and that must not be treated the same.
#
# The UI used to release a chat after fifteen minutes of no reply and tell the
# user to go look somewhere else. A long agent run is NORMAL here: the
# reference machine has turns of 25+ tool calls, and every one of them is a
# perfectly healthy turn that a wall clock calls dead. Meanwhile the condition
# the release was built for — the recurring silent hang, process alive and
# friday.log completely dark — can happen at ninety seconds and the clock has
# nothing to say about it.
#
# So: a turn is alive while its worker thread is alive AND something has petted
# it. Every process_register/update/log pets the turn running on that thread,
# which means every iteration of both agent loops and every tool call reports
# progress without a single call site having to know this exists.
_TURNS = {}
_TURNS_LOCK = threading.Lock()
_TURN_LOCAL = threading.local()

#: How long a turn may go with no orb activity before it is *reported* as
#: quiet. Reported, not killed: a single model call legitimately runs minutes
#: with nothing to say, so this is a fact the UI may show, never a verdict.
TURN_QUIET_AFTER_S = 180.0


def turn_begin(turn_id, conversation_id=None):
    """Mark the start of a chat turn on THIS thread. Returns the turn id."""
    if not turn_id:
        return None
    now = _time.time()
    rec = {"turn_id": turn_id, "conversation_id": conversation_id,
           "thread": threading.current_thread(), "started": now,
           "last_progress": now, "label": None, "step": None, "model": None,
           "pets": 0}
    with _TURNS_LOCK:
        _TURNS[turn_id] = rec
        # Bound the registry. A turn that never ended because its thread died
        # mid-flight would otherwise sit here forever; a dead thread is
        # detectable, so drop those rather than expiring live ones on a clock.
        if len(_TURNS) > 64:
            for tid, r in list(_TURNS.items()):
                th = r.get("thread")
                if tid != turn_id and th is not None and not th.is_alive():
                    _TURNS.pop(tid, None)
    _TURN_LOCAL.turn_id = turn_id
    return turn_id


def turn_end(turn_id=None):
    tid = turn_id or getattr(_TURN_LOCAL, "turn_id", None)
    _TURN_LOCAL.turn_id = None
    if not tid:
        return
    with _TURNS_LOCK:
        _TURNS.pop(tid, None)


def turn_pet(label=None, step=None, model=None, detail=None):
    """Record progress on the turn running on this thread. Never raises.

    The liveness contract the chat UI renders: ``step`` is the integer round
    and ``label`` is text. Anything else offered as a step is ignored, so a
    step record can never be printed as the round. ``detail`` carries the
    latest step record's type, name and status, and nothing more: arguments
    and results stay in the process tray, where tier-safe summaries live.
    """
    tid = getattr(_TURN_LOCAL, "turn_id", None)
    if not tid:
        return
    try:
        with _TURNS_LOCK:
            rec = _TURNS.get(tid)
            if rec is None:
                return
            rec["last_progress"] = _time.time()
            rec["pets"] += 1
            if label:
                rec["label"] = str(label)[:120]
            if type(step) is int:
                rec["step"] = step
            if isinstance(detail, dict):
                rec["detail"] = {k: str(detail[k])[:80]
                                 for k in ("type", "name", "status")
                                 if detail.get(k) is not None}
            if model:
                rec["model"] = model
    except Exception:
        pass


def turn_request_stop(turn_id):
    """Ask the turn running under `turn_id` to stop at its next round.

    THE STOP THE LOCAL PATH NEVER HAD. The cloud loop honours a kill file
    (``~/.friday/AGENT_STOP``) and a background task can be stopped from the
    tasks tray, but an interactive chat turn had nothing: the only thing that
    ever ended one early was the 50-round cap. Raising that cap to parity
    without giving the user a way to say "enough" would have removed a brake
    and replaced it with nothing.

    Cooperative on purpose. The round loop checks between rounds, so the turn
    ends with its transcript and receipts intact instead of being killed
    mid-tool-call, which is what makes a stop recoverable rather than a second
    kind of failure.

    Returns True when the turn was registered and has been asked to stop, and
    False when it is not running here (already finished, or never started) --
    so the UI can say which, rather than showing a button that silently does
    nothing.
    """
    if not turn_id:
        return False
    with _TURNS_LOCK:
        rec = _TURNS.get(str(turn_id))
        if rec is None:
            return False
        rec["stop"] = True
    return True


def turn_stop_requested(turn_id=None):
    """Has a stop been asked for? Defaults to the turn on THIS thread."""
    tid = turn_id or getattr(_TURN_LOCAL, "turn_id", None)
    if not tid:
        return False
    try:
        with _TURNS_LOCK:
            rec = _TURNS.get(str(tid))
            return bool(rec and rec.get("stop"))
    except Exception:
        return False


def turn_liveness(turn_id, now=None):
    """What the chat UI asks instead of looking at a clock.

    ``state`` is one of:
      * ``working``  — the worker thread is alive. NEVER release the chat.
      * ``quiet``    — alive, but nothing has reported progress for a while.
                       Still not a reason to release: say so and keep waiting.
      * ``gone``     — the thread is dead or the turn was never registered.
                       This is the real failure, and the only honest release.
    """
    now = now or _time.time()
    with _TURNS_LOCK:
        rec = dict(_TURNS.get(turn_id) or {})
    if not rec:
        return {"turn_id": turn_id, "state": "gone", "alive": False}
    th = rec.get("thread")
    alive = bool(th is not None and th.is_alive())
    quiet_for = now - float(rec.get("last_progress") or now)
    state = "working" if alive else "gone"
    if alive and quiet_for > TURN_QUIET_AFTER_S:
        state = "quiet"
    return {
        "turn_id": turn_id,
        "conversation_id": rec.get("conversation_id"),
        "state": state,
        "alive": alive,
        "elapsed_s": round(now - float(rec.get("started") or now), 1),
        "quiet_for_s": round(quiet_for, 1),
        "quiet_after_s": TURN_QUIET_AFTER_S,
        "label": rec.get("label"),
        "step": rec.get("step"),
        "detail": rec.get("detail"),
        "model": rec.get("model"),
        "progress_events": rec.get("pets", 0),
    }


def process_register(pid, *, name="Task", label=None, category="default",
                     icon="⚡", steps=None, model=None, color=None,
                     task_id=None, eta_s=None, step_total=None):
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
            "progress": None,
            "steps": steps or [],
            "log": [],
            "task_id": task_id,
            # Made off the record: shown live, never copied to disk by anything
            # that snapshots the registry (ops/forensics-snapshot.py).
            "off_record": _off_record_now(),
            # How long this is expected to take, from measurements we already
            # hold. The tray renders "42s left of ~93s" instead of a bar with
            # no scale, and says "longer than usual" rather than parking at
            # 99% — a warning without a number is just an apology.
            "eta_s": eta_s,
            # HOW FAR ALONG, HONESTLY.
            #
            # `progress` is a fraction and a fraction needs a denominator. An
            # agent loop does not have one: it runs until the model stops
            # asking for tools, and `max_iters` is 999, which is a ceiling and
            # not an expectation. The Anthropic loop used to report
            # `0.05 + 0.1 * (iteration - 1)` — a straight line to 90% that
            # silently asserts "about ten steps" — and the local loop reported
            # nothing at all, so a local task sat at 0% for its entire life no
            # matter how well it was going.
            #
            # So: `step_n` is what we actually know, `step_total` is None when
            # we do not know the total, and `progress` stays None unless
            # something can compute a real fraction (image generation can;
            # an agent loop cannot). A reader showing "step 7" is telling the
            # truth. A bar at 0% is not.
            "step_n": 0,
            "step_total": step_total,
            "started": _time.time(),
            "updated": _time.time(),
            # The reasoning trace this orb's work is recorded in, so the tray
            # row can expand into the reasoning behind it.
            "trace_id": _current_trace_id(),
        }
    turn_pet(label=label or name, model=model)
    # The lattice's background orbiter (avatar-visual-genome.md §13): a
    # scheduled run is background work from the moment it is registered.
    if str(pid).startswith("sched-"):
        _presence_frame("background", "start", ref=pid)


def _presence_frame(state, phase, *, ref=None, **fields):
    """One presence frame (services/presence.py). Never raises."""
    try:
        from agent_friday.services import presence as _pr
        if ref is not None:
            fields["ref"] = _pr.opaque(ref)
        _pr.emit(state, phase, **fields)
    except Exception:
        pass


def _current_trace_id():
    try:
        from agent_friday.services import reasoning_trace as _rt
        return _rt.current()
    except Exception:
        return None


def process_update(pid, *, status=None, progress=None, label=None,
                   step=None, steps=None, task_id=None, result=None,
                   step_n=None, step_total=None):
    """Update an existing process entry.

    `result` attaches the process's final output (e.g. a local model's reply)
    so the notification detail panel has a real process to show — a subagent
    task whose orb carries no log/result renders as an empty "nothing".
    """
    with PROCESSES_LOCK:
        p = PROCESSES.get(pid)
        if not p:
            return
        _was_step, _was_status = p.get("step_n"), p.get("status")
        _trace = p.get("trace_id")
        if status is not None:
            p["status"] = status
        if progress is not None:
            p["progress"] = max(0.0, min(1.0, progress))
        if step_n is not None:
            p["step_n"] = int(step_n)
        if step_total is not None:
            p["step_total"] = int(step_total)
        if label is not None:
            p["label"] = label
        if step is not None:
            p["steps"].append(step)
        if steps is not None:
            p["steps"] = steps
        if task_id is not None:
            p["task_id"] = task_id
        if result is not None:
            p["result"] = str(result)[:4000]
        p["updated"] = _time.time()
        if status in ("completed", "error"):
            p["ended"] = _time.time()
    # `step` here is a tray step record; the turn's round is `step_n`.
    turn_pet(label=label, step=step_n, detail=step)
    _presence_for_update(pid, _trace, status, progress, step_n,
                         _was_step, _was_status)


def _presence_for_update(pid, trace, status, progress, step_n, was_step, was_status):
    """The presence frames one process_update really means (§13.5): a new
    agent round, a true progress fraction (a completion is not progress), an
    error, and the end of a scheduled run. Only the agent loops set step_n,
    and it is the round the status line prints."""
    ended = status in ("completed", "error") and was_status not in ("completed", "error")
    if step_n is not None and int(step_n) > 0 and int(step_n) != was_step:
        _presence_frame("round", "step", turn=trace, n=int(step_n))
    if progress is not None and status not in ("completed", "error"):
        try:
            from agent_friday.services import presence as _pr
            _pr.progress(pid, progress)
        except Exception:
            pass
    if status == "error" and was_status != "error":
        _presence_frame("error", "once", ref=pid, turn=trace)
    if ended:
        if str(pid).startswith("sched-"):
            _presence_frame("background", "end", ref=pid, ok=(status == "completed"))
        try:
            from agent_friday.services import presence as _pr
            _pr.progress_done(pid)
        except Exception:
            pass


def process_log(pid, line: str):
    """Append a log line to a process so the notification detail panel shows activity."""
    with PROCESSES_LOCK:
        p = PROCESSES.get(pid)
        if p is not None:
            p.setdefault("log", []).append(str(line))
            p["updated"] = _time.time()
            if len(p["log"]) > 200:
                p["log"] = p["log"][-200:]
    turn_pet()


def process_remove(pid):
    """Remove a process from the registry."""
    with PROCESSES_LOCK:
        PROCESSES.pop(pid, None)


# ── Agent Settings (Reasoning style, personality, response prefs) ──
SETTINGS_FILE = FRIDAY_DIR / "settings.json"
AGENT_PERSONALITY_FILE = FRIDAY_DIR / "agent-personality.txt"
# v5: SOUL.md is the canonical, user-editable personality config. It wins over
# the legacy agent-personality.txt when present. services/soul.py owns creation,
# validation, and version history; core only reads it (services→core direction).
SOUL_FILE = FRIDAY_DIR / "SOUL.md"

# In-memory settings cache — avoids hammering the filesystem on every API call.
# _load_settings_raw() returns a cached copy when the on-disk value is ≤2 s old;
# _save_settings() invalidates the cache immediately after writing so the next
# call always returns the freshly persisted value.
_SETTINGS_CACHE: dict = {"value": None, "ts": 0.0}
_SETTINGS_CACHE_TTL: float = 2.0  # seconds
_SETTINGS_CACHE_LOCK = threading.Lock()
_SETTINGS_WRITE_LOCK = threading.RLock()


#: Bumped by every invalidation. A reader stores what it read only when no
#: invalidation happened while it was reading: a read that overlapped a save
#: holds the pre-save file and must not be served from the cache afterwards.
_SETTINGS_CACHE_GEN = [0]


def _invalidate_settings_cache() -> None:
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE["value"] = None
        _SETTINGS_CACHE["ts"] = 0.0
        _SETTINGS_CACHE_GEN[0] += 1

DEFAULT_AGENT_PERSONALITY = (
    "You are Friday — a calm, perceptive AI partner. "
    "You speak with quiet confidence and dry warmth; you favor signal over noise. "
    "You connect dots across the user's work and life without being asked twice. "
    "You give the answer first, then the reasoning. You are honest about uncertainty."
)

# The one place the default local model is decided. `model_plan` imports nothing
# from `agent_friday` (its only module-level import is `__future__`), so this is
# free and cannot cycle. See model_plan.FLOOR_MODEL for why it is not retyped.
from agent_friday.services.model_plan import FLOOR_MODEL as _FLOOR_MODEL  # noqa: E402


DEFAULT_SETTINGS = {
    # NOTE for anyone adding a setting: _load_settings_raw() WHITELISTS against
    # this dict —
    #     merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    # — so a key that is not declared here is written to settings.json
    # successfully and then silently discarded on every read. The write reports
    # success; the setting does nothing. Both switches below were added to
    # settings.json and read back as False for exactly this reason, which meant
    # two kill switches existed that could never be turned on.
    "judgment_gate": {                    # deep-research.md §5 — the privacy
        "enabled": False,                 # judgment layer. Default OFF; the
        "model": "gemma4:e2b",            # layer is additive and removable.
    },
    "away_drain": {                       # P5 — drains queued heavy work on a
        "enabled": False,                 # timer. Default OFF: it takes the GPU.
    },
    # Empty on purpose: services/knowledge_graph/__init__.py:kg_settings() owns
    # the real defaults (KG_DEFAULT_SETTINGS) and layers the user's saved block
    # on top. This entry exists only so that block SURVIVES the whitelist two
    # lines above this comment's file-mate -- without it, every knowledge-graph
    # setting the Settings->Privacy & Data tab saves (index_sources, indexing_mode,
    # power_indexer, nightly_reindex...) round-trips through settings.json and
    # is silently discarded on the very next read, same defect class as
    # egress_mode and the top-level vault_local_only (docs/design/
    # security-boundary.md #18).
    "knowledge_graph": {},
    # Top-level wiki sections whose pages never enter the ambient knowledge
    # block for a cloud provider and are encrypted at rest with the vault
    # key (services/wiki_engine._wiki_encrypted_sections, knowledge_graph/
    # integration.knowledge_context_block). Read by both, so it MUST be
    # declared here: the whitelist read above discards an undeclared key on
    # every load, and the feature could then not be switched on from
    # settings.json at all. The Privacy tab's checklist writes it (model-soup.md §11.4).
    "wiki_encrypted_sections": [],
    # Absolute path of an owner-chosen folder that receives a copy of every
    # wiki write and delete (services/wiki_engine._wiki_mirror_dir). Empty =
    # off. A cloud-synced folder sends every mirrored page off the machine.
    "wiki_mirror_dir": "",
    # How the offline monitor decides whether this PC is online
    # (services/notifications._network_probe): "route" (default; a routing-
    # table lookup, nothing sent), "internet" (TCP to public DNS resolvers
    # every 30 s) or "off".
    "network_probe": "route",
    # Whether the page may load its typefaces from Google Fonts while the
    # font files are missing from static/fonts (services/web_fonts.py). Once
    # they are there, Google Fonts is never requested.
    "web_fonts_from_google": True,
    "temperature": 0.7,
    "response_length": "standard",        # concise | standard | detailed
    "include_sources": True,
    "cite_sources": False,                # Source Production Mode — inline citations on every factual claim
    # The pause-forecast "don't warn me again" escape hatch (index.html's
    # seat-pause confirmation dialog) POSTs this key correctly wrapped in
    # {"settings": {...}}, so it survives the whitelist read/write cycle --
    # but it had no DEFAULT_SETTINGS entry at all, so _load_settings_raw()'s
    # whitelist silently dropped it on every save. The dialog appeared to
    # remember the choice for the rest of that browser tab (optimistic
    # client-side state) and then nagged again on the next reload/restart,
    # same defect class as knowledge_graph above.
    "pause_warnings_off": False,
    # Maintainer ruling: "always open files you create for me upon
    # completing them." Default False — auto-launching an app the instant a
    # file lands is a real product decision (multiple generations in a row
    # would pop multiple viewers), not something to turn on for everyone by
    # default. Read by services/agent.py::_maybe_auto_open and
    # services/creations.py::_notify_creation.
    "auto_open_created_files": False,
    "memory_recall_enabled": True,        # RAG over persistent ChromaDB conversation memory
    "news_priorities": ["AI/Tech", "Politics", "Media", "Local", "Business"],
    # The Local beat is the owner's to name; nothing ships with a city.
    # news_local_area: the place Local news covers, as free text.
    # news_local_sources: outlet domains for that beat, fetched as Local
    # feeds and trusted like the built-in high-trust outlets
    # (source_trust_graph.local_beat_sources). Both empty means no Local beat.
    # The four News routines write on the local model (news_engine.local_news_run).
    "news_local_only": True,
    # Notification kinds the owner muted ("kind|source"); muted kinds go to the
    # activity log. Approvals can never be muted (notification_policy).
    "notification_mutes": [],
    "news_local_area": "",
    # Hours a story stays eligible for each routine's edition (news_seen).
    "news_edition_window_hours": {"front_page": 36, "briefing": 36},
    "news_local_sources": [],
    "communication_style": "professional",  # professional | casual | technical
    "camera_interval_sec": 3,              # 1 | 3 | 5
    "tts_voice": "Aoede",                  # any of the 30 Gemini-TTS voices
    "voice_language": "",                  # BCP-47 (e.g. "en-US"); blank = server default
    "voice_style_prompt": "",              # free-text styling instruction passed to Gemini
    "voice_response_depth": "adaptive",    # adaptive | concise | detailed
    "voice_speaking_pace": "adaptive",     # adaptive | measured | natural | brisk
    "voice_temperature": None,             # 0.0 – 2.0; null = SDK default
    "voice_max_tokens": 0,                 # cap response length in tokens; 0 = unlimited
    "voice_affective": True,               # Live API enable_affective_dialog
    "voice_proactive": True,               # Live API proactivity.proactive_audio
    "voice_context_compression": True,     # Live API sliding-window compression; ON by default so the context-window cap never silently terminates a long voice session (pairs with session_resumption renewal)
    "voice_barge_grace_ms": 800,           # ignore mic this long after Friday starts speaking (echo-canceller warmup)
    "voice_barge_sustain_ms": 200,         # Gemini Live speaker-safe mode: deliberate speech must persist this long (two of its ~171 ms mic frames) to interrupt playback
    "voice_local_barge_sustain_ms": 170,   # local voice: the same, over its ~85 ms mic frames (two frames), so she stops within ~300 ms of the first word
    # Interruption mode — what talking over Friday does. Escape stops her in
    # every mode. On SPEAKERS the mic re-captures her own voice, which is why
    # the open-speaker mode exists.
    #   Gemini Live: "auto" (default; also "headphones", "speaker") =
    #     START_OF_ACTIVITY_INTERRUPTS, she stops when Gemini hears you start
    #     talking; "no-barge" = speaker-safe: NO_INTERRUPTION, and Friday's own
    #     echo-aware detector stops her when you are clearly louder than her
    #     voice in the mic.
    #   Local voice: "auto" = that echo-aware detector; "no-barge" = Esc only.
    "voice_interruption_mode": "auto",     # "auto" (talk over her) | "no-barge" (Gemini Live: speaker-safe; local: Esc only)
    "voice_room_mode": "one",              # "one" person talking to Friday | "room" (several people; she answers only when addressed)
    # ── The two limits that exist only in voice, both the owner's to set ──
    # Policy (2026-09-29): a limit that applies only to voice is the owner's
    # choice, not Friday's, unless it protects something the constitution
    # requires. Approval cards and the never-send floor are NOT voice limits —
    # they apply identically in chat — so they are not listed here.
    #
    # How long a DIRECT voice tool may hold the line before its work is moved
    # to the background. It subtracts no capability: the work continues and
    # reports back. 0 means no limit, which risks a silent conversation.
    "voice_tool_hard_limit_s": 20,
    # Local voice: seconds without a first word before Friday says "Hang on."
    # (the turn is stopped honestly at voice_tool_hard_limit_s). 0 = off.
    "voice_first_token_filler_s": 0,
    # In "room" mode a spoken approval counts only when it names Friday
    # ("Friday, send it"), because voices are not told apart until Household
    # Identity lands. This is the ONE voice limit left ON by default, and it is
    # an identity gap rather than a restriction: in chat an approval carries an
    # authenticated session, and a room with several people offers no
    # equivalent. Turning it off means anyone within earshot can approve.
    "voice_room_approvals_require_name": True,
    # Leave the local brain loaded after a quit. OFF: the 27B seat holds around
    # 14 GB of RAM and most of a 12 GB card, and those are the machine's, not
    # Friday's, the moment the owner has closed her. A planned restart (the
    # deploy lane, tray Restart) keeps the seat regardless, because reloading
    # it costs the better part of a minute and nobody asked for the memory
    # back; only a quit releases it (services/residency_arbiter.
    # release_for_quit).
    "keep_brain_warm_between_sessions": False,
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
    "local_voice_asr_model": "auto",       # faster-whisper size: auto (base on CPU, small on a GPU)|tiny|base|small|medium
    # ── Push-to-transcribe ────────────────────────────────────────────────
    # Hold a key anywhere in Windows, speak, release, and the local transcript
    # is pasted into whatever window has focus. On by default, and system-wide
    # by default, because a dictation key that only works in one window is a
    # worse version of the microphone button that is already there.
    #
    # Detecting a HELD key needs a low-level keyboard hook, which Windows hands
    # every key event in the system. services/push_to_talk.py compares one
    # field against the one configured key and returns before any branch that
    # could record, count or classify anything else; the Settings copy says so
    # in the same words. Turning this off removes the hook entirely.
    "push_to_transcribe": True,
    "push_to_transcribe_hotkey": "alt+t",   # NOT alt+f: that opens Chrome's menu
    # A tap shorter than this is replayed to the focused window, so Alt+T keeps
    # working in applications that wanted it.
    "push_to_transcribe_hold_ms": 150,
    "local_voice_tts_voice": "en_US-amy-medium",  # Tier-1 Piper voice id
    # Which synthesizer local voice speaks with. Piper is the default: it is
    # what the CPU tier installs (voice-local-lite), so a fresh machine speaks.
    # Kokoro is the better voice, chosen here; it runs on the graphics card
    # when it fits and on the processor when it does not (the session says
    # which), with Piper as the floor for a phrase it cannot speak
    # (services/voice_workers.build_mouth). Local voice spec D4 (Kokoro as the
    # default) waits on a default that falls back to Piper when Kokoro is not
    # installed.
    #   "piper"  — CPU-only; GPL-3.0 upstream since Oct 2025
    #   "kokoro" — Kokoro-82M (Apache-2.0); GPU when it fits, else CPU
    # These three MUST live here. `_load_settings_raw()` drops any persisted key
    # absent from DEFAULT_SETTINGS, so a key the service layer reads but this
    # dict does not declare is a control that saves, reports success, and
    # reverts on the next read (docs/decisions/2026-09-04-five-dead-settings.md).
    "local_voice_tts_engine": "piper",   # the CPU tier's voice; Kokoro is chosen explicitly (services/local_voice.py)
    "local_voice_kokoro_voice": "af_heart",   # Kokoro voice id, used when engine=kokoro
    "local_voice_kokoro_allow_cpu": True,     # Kokoro may run on the CPU when the card is busy (slower; the session says so) rather than Piper speaking
    # Tier-2 (NeMo GPU) models — used only when voice_engine resolves to the GPU
    # tier. Override the ASR id to a sibling (e.g. the English-only streaming
    # model) if desired; the TTS pair (FastPitch+HiFi-GAN) is fixed for v1.
    "local_voice_gpu_asr_model": "nvidia/nemotron-3.5-asr-streaming-0.6b",
    "local_voice_gpu_tts": "fastpitch-hifigan",
    "voice_silence_ms": 500,               # trailing silence (ms) that ends a local-voice turn (spec P2: 500; the streaming ear has the transcript ready at the endpoint)
    # Clean-sheet voice (docs/design/active/voice-system-clean-sheet.md §8.1):
    # per-stage GPU policy read by services/voice_manifest.read_selection()
    # and enforced by its proofs ("required" refuses a CPU engine); idle
    # unload read by the voice workers' lease TTL. Declared here so they
    # survive a reload (the dead-settings rule).
    "voice_ear_gpu": "if_free",            # never | if_free | required
    "voice_mouth_gpu": "if_free",          # never | if_free | required
    "voice_idle_unload_s": 600,            # GPU voice worker idle unload (s)
    # ── The voice front (local voice spec §4.1, §6) ──
    # The small fast model that answers live voice turns on its own seat; the
    # brain takes deep work asynchronously. Read by services/voice_front.
    "voice_front_model": "ternary-bonsai:1.7b",   # ternary-bonsai:1.7b | qwen3-4b-instruct-2507 | qwen3-1.7b
    # The brain during a call: "auto" = beside the front when the card holds
    # both, else parked for the call; "parked"; "resident".
    "voice_brain_during_calls": "auto",
    # Where a deep question asked by voice goes: "local_only" (the brain,
    # or after the call) | "follow_model_routing" (the owner's routing,
    # cloud included, behind the same gates).
    "voice_async_routing": "local_only",
    # These three are written by the Settings→Voice UI. _load_settings_raw()
    # drops any persisted key absent from DEFAULT_SETTINGS, so a key missing
    # here silently reverts on every reload even though the save "succeeded".
    # -- Cloud voice providers (docs/design/active/cloud-voice-providers.md) --
    # Tier-3 SIBLINGS, never a default. `voice_engine` gains the values
    # "elevenlabs" and "inworld" (also accepted as "cloud:<name>"), read by
    # services/cloud_voice.py:resolve_provider(). Every key below has a real
    # enforcement point in that module or in routes/cloud_voice_routes.py --
    # none is a prop. They MUST be declared here: _load_settings_raw()
    # whitelists against this dict, so a key the service layer reads but this
    # dict does not declare saves, reports success, and reverts on the next
    # read (docs/decisions/2026-09-04-five-dead-settings.md).
    #
    # NOTE: `elevenlabs_api_key`, `elevenlabs_model` and `elevenlabs_voice_id`
    # were ALREADY read by services/elevenlabs_tools.py and were NOT declared
    # here -- live instances of that same defect, found while implementing this
    # spec and fixed below. See the report accompanying this change.
    "elevenlabs_api_key": "",   # key material, never committed; env wins over this
    "elevenlabs_model": "eleven_flash_v2_5",       # cloud_voice.selected_model()
    "elevenlabs_voice_id": "",  # "" = provider default; cloud_voice.selected_voice()
    "inworld_api_key": "",
    "inworld_model": "inworld-tts-2-flash",
    "inworld_voice_id": "",
    # Q4: Inworld's rate is tier-dependent while ElevenLabs' is flat, so a
    # single PRICING row over-reports for anyone off On-Demand. Making the tier
    # an input is the spec's own second option: any value but "on_demand"
    # meters under an UNPRICED id and renders "not priced" rather than a wrong
    # number. Enforced in cloud_voice.meter_model_id().
    "inworld_plan_tier": "on_demand",      # on_demand | growth | enterprise
    "voice_tools": True,                   # let live voice sessions call Friday's tools
    "audio_input_device_id": "",           # preferred mic (browser deviceId); "" = system default
    "audio_output_device_id": "",          # preferred speaker (browser deviceId); "" = system default
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
    # Full tool output kept on disk when a result is cut (services/tool_output.py);
    # day folders older than this are deleted. 0 = keep forever.
    "tool_output_retention_days": 7,
    "user_email": "",                      # the user's own email — passed through unscrubbed
    "off_record": False,                   # quick toggle — when true, chat is not logged either
    "off_record_stops_storage": True,      # off-record writes nothing about the conversation to disk (receipts and governance logs keep only tool, class, decision and time)
    # ── Artifact panel (docs/design/active/vibe-coding-salon.md §4.2) ──
    "artifact_panel_enabled": True,        # the panel beside every chat; also keeps artifact_put resident in the tool set
    # ── Publish to web (docs/design/active/vibe-coding-salon.md §4.10.1) ──
    "publish_default_adapter": "this_pc",  # owner decision 2026-09-29: this PC by default; cloudflare_pages | github_pages for always-on pages
    "publish_mark": True,                  # the small "Made with Friday" mark on published pages
    "publish_this_pc_enabled": True,       # the kill switch for local hosting: False takes every published page offline at once
    "publish_this_pc_tunnel": True,        # False keeps published pages on this PC's loopback only (no cloudflared quick tunnel)
    # ── Workspaces / Dock ──
    # When True the dock shows ALL workspaces (Finance, Health, Family, Trust,
    # Studio, Content, FutureSpeak); when False it shows only the
    # trimmed core set. Default is resolved per-install in _load_settings:
    # existing installs (~/.friday already present) → True; fresh installs → False.
    "show_all_workspaces": True,
    # Features held back from this release (services/held_features.py). Each
    # keeps its code, routes and data; while its switch is off nothing offers
    # it and its routes answer "not enabled". `federation` holds the
    # Marketplace, positrons, peer federation, federated compute and
    # defederation. Buying stays refused whatever this says.
    # trust_agents: the trust graph's agent kind (schema only; both off).
    "held_features": {"federation": False, "trust_agents": False},
    # The Library (services/library): search on or off; whether a Library answer
    # may be written by a cloud model (default off: Library text stays on this
    # PC); whether documents are read while on battery; whether the knowledge
    # graph learns from Library documents ("" = not chosen yet, "on", "off").
    "library_search": True,
    "library_floor_tier": False,           # a small PC: fewer, shorter passages per search
    "library_cloud_answers": False,
    "library_index_on_battery": False,
    # The most characters of the owner's documents one answer may send to a cloud model
    # (only when cloud answers are on); the Library trims to it and says so.
    "library_cloud_char_cap": 6000,
    "library_kg_learn": "",
    "studio_dazzle": "full",              # visual intensity of every 3D view: off | subtle | full
    # `decision_backend` (which scorer answers Friday's typed judgments) is
    # declared once, with the approval-gate block further down.
    # The owner's own dock arrangement: {"order": [ws_id, ...], "hidden": [ws_id, ...]}.
    #
    # NOT `dock_layout`. That name was already taken at line ~2035 by the
    # distribution preset ("standard" | "journalism" | "developer" | ...), and a
    # second entry under the same name in this dict literal would have been
    # silently shadowed by the later one — a control that saves, reports success,
    # and does nothing, which is the exact defect scripts/check_settings_readers.py
    # exists to catch. (Separately: grep finds no reader for that preset anywhere,
    # so it appears to be a sixth dead setting. Not fixed here; noted.)
    #
    # Empty means UNCONFIGURED, and show_all_workspaces above governs exactly as
    # it always has. The moment an arrangement exists it wins outright, because
    # two switches over one dock is how you get a control that appears to do
    # nothing. Settings → Appearance says so, and the quick toggle disables itself
    # rather than silently losing the argument.
    #
    # `order` is a flat list across all three dock groups. Group separators are
    # DERIVED from it — a separator renders wherever two adjacent items belong to
    # different groups — so leaving the default order reproduces today's dock
    # exactly, and interleaving collapses the groups without needing a second
    # setting to say so.
    "dock_custom": {"order": [], "hidden": []},
    # How long the machine must be idle before a parked batch may take the GPU.
    # Present here because a key missing from DEFAULT_SETTINGS is DELETED on
    # every save — the same defect that silently dropped `heavy_hitter` from
    # capability_routing. Without this line the knob accepts a write, reports
    # success, and reverts, which is worse than not having it.
    "away_drain_after_s": 900,
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
    # drive budget-alert notifications: crossing 80% warns, 100% alerts. The
    # alert cap never blocks — Friday is never silently stopped by it.
    #
    # hard_stop_* (maintainer ruling: "we do want a stopping cap available
    # to the user") is the SECOND cap: when its enabled period's
    # spend reaches the limit, services/spend_guard refuses every further
    # cloud call (local models keep working), loudly — ledger row +
    # notification naming what stopped and how to resume. Off by default;
    # the user chooses, neither cap is imposed.
    "cost_budget": {
        "daily": 5.0,
        "monthly": 50.0,
        "daily_enabled": False,
        "monthly_enabled": False,
        "hard_stop_daily": 0.0,
        "hard_stop_monthly": 0.0,
        # THE STUCK-MODEL GUARD, shown beside the spending limits because that
        # is where the owner decides what may stop his work.
        #
        # It is not a usage cap: it fires on a SHAPE -- the same call, or the
        # same short cycle of calls, going round without the conversation
        # moving on -- and never on an amount. That is why it survived the
        # 2026-09-25 removal of the round, time and token caps, and why it
        # defaults ON. It is still his to switch off.
        "loop_guard_enabled": True,
        "hard_stop_daily_enabled": False,
        "hard_stop_monthly_enabled": False,
    },
    # ── Task journal (docs/design/active/task-visibility.md) ──
    # Every background task writes an append-only journal and a state
    # snapshot under ~/.friday/tasks/ as it runs, so a restart marks work as
    # interrupted instead of erasing it. Each key below is a maintainer
    # ruling made reversible: retention_days 0 keeps everything (nothing is
    # ever deleted at an invented threshold); capture_reasoning is on because
    # "what is it reasoning" was the question that produced this system, and
    # it costs tokens on providers that bill for thinking and stores the most
    # sensitive text a task handles; encrypt_at_rest uses the vault key (then
    # DPAPI) because a journal holds prompts and results.
    "task_journal": {
        "retention_days": 0,
        "capture_reasoning": True,
        "encrypt_at_rest": True,
    },
    # Reasoning traces (services/reasoning_trace.py): every model call's
    # reasoning, streamed to the tray and archived encrypted + hash-chained.
    # retention_days 0 keeps everything; the user chooses a threshold.
    "reasoning_traces": {
        "capture": True,
        "retention_days": 0,
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
    # ── v5 Super-Agent subsystems (all local-only, all cLaws-safe) ──
    # Learning loop: observes task outcomes, mines successful patterns into text
    # heuristics, promotes the best into the system prompt. No executable skills.
    "learning_loop": {"enabled": True, "max_active_skills": 50, "epoch_weekday": 6},
    # Memory dreaming: nightly local consolidation of the day's conversation
    # turns into durable facts + topic summaries. Never touches cloud.
    "memory_dreaming": {"enabled": True, "hour": 3, "keep_topics": 12},
    # User modeling: tracks comm style, domain expertise, and workflow patterns;
    # injects a TIER_1 behavioral summary into the system prompt.
    "user_modeling": {"enabled": True, "inject_prompt": True},
    # Channel bridges (Discord / Telegram). Disabled + allowlist-gated by default;
    # every reply routes through the agent loop + egress gate.
    "channels": {"enabled": False,
                 "telegram": {"enabled": False, "allowlist": [], "poll_interval": 3.0},
                 "discord": {"enabled": False, "allowlist": [], "poll_interval": 3.0}},
    # ── Experimental ──
    "computer_control_enabled": False,     # opt-in gate for the pyautogui subsystem; OFF by
                                           # default. Even when True, each runtime grant is a
                                           # separate Ring-3 step (/api/control/permission).
    # ── Agent Identity & Model Selection ──
    "agent_name": "AGENT FRIDAY",  # brand: plain (her default name, not the product)
    # The workspaces the owner set to fill the screen with the chat tray docked
    # beside them (docs/design/active/unified-shell.md §5): id -> "fullscreen_chat".
    "workspace_layouts": {},
    # When the start screen's cluster (countdowns, chat field, mic, Start my
    # day) shows (unified-shell.md §10.4): "smart" when it is useful, "always"
    # whenever no workspace is open, "never" only when asked (show_my_day).
    "landing_mode": "smart",
    # Big mode (hand-cursor.md §2): large targets when hand tracking is on. auto | on | off.
    "big_mode": "auto",
    # Claude Sonnet 5 is the default orchestrator — best cost/quality ratio for
    # most tasks; Opus 5 remains available for max-reasoning work. Fallback
    # chain: Sonnet 5 → Fable 5 → Opus 5 → Sonnet 5 → Haiku 4.5
    # (see model_router.CLOUD_MODEL_FALLBACK_CHAIN).
    "orchestrator_model": "claude-sonnet-5",      # main agent brain
    "subagent_model": "claude-sonnet-5",      # background tasks and drafts
    "creative_model": "gemini-nano-banana-2",   # image/creative generation (Nano Banana); video uses Veo
    "music_model": "lyria-clip",                # music generation (Lyria 3): 'lyria-clip' (≤30s) | 'lyria-pro' (full song)
    # Custom-model escape hatch (spec A2): [{"provider": ..., "id": ...}] pairs
    # the hosted/local catalogs don't know yet (private previews, self-hosted
    # ids). The model catalog surfaces them flagged unverified + non-curated.
    "custom_models": [],
    "voice_model": "gemini-3.8-live",  # live audio: Google's stable Live model and the fastest measured. MUST stay in sync with voice_engine.LIVE_MODEL — settings always win over that constant.
    # ── Creator Economy / Production (Layer 1) ──
    # Daily creation now chooses FREELY across all media (text/code/image/music/
    # video/full production), weighted by recent work + ambient mood + budget —
    # not a fixed rotation. The budget ceiling keeps "free choice" from ever
    # meaning "unbounded spend": expensive media are filtered out when the
    # remaining daily creative budget is low.
    "daily_creation_free_choice": True,         # False reverts to the legacy text rotation
    "daily_creation_budget_usd": 0.50,          # soft ceiling on a day's creation spend
    # ── Turn budget (advanced) ──
    # A local seat gets the same round budget as a cloud seat: a capable local
    # model can reason across hundreds of rounds, and a lower local cap is a
    # leftover from small-model days. Loop detection, a wall clock and a token
    # ceiling do the actual safety work. These repeat services/turn_budget.py's defaults so the
    # figures are visible here; a missing or zero entry falls back to that
    # module, and a per-seat key (e.g. {"local": 200}) overrides one seat only.
    # `scheduled` is what unattended work gets, because nobody is watching it.
    # EMPTY ON PURPOSE. A figure here is a built-in cap wearing a settings
    # key: `turn_budget` is read before anything else, so a shipped 999 would
    # have limited every turn no matter what the module defaults said.
    #
    # The owner, 2026-09-25: "I want no caps unless I set them myself in the cost
    # metering UI." So these groups exist but stay empty, and a figure appears
    # only when he puts one there.
    "turn_budget": {
        "rounds": {},
        "wall_clock_s": {},
        "tokens": {},
    },
    # ── Idle-time work ──
    # The switch and the window for work that should happen while the user is
    # away rather than at a fixed hour (the daily creation, among others).
    # ON by default.
    # ── Podcasts (services/podcast_engine.py, docs/design/active/local-podcasts.md)
    # Written by the local model and spoken by Kokoro on the CPU. A cloud voice
    # is only ever used when `cloud_voice` is switched on, and never for an
    # episode built from private material.
    "podcasts": {
        "enabled_for_routines": {"front_page": True, "briefing": True,
                                 "weekly": True, "editorial": True},
        # Who is on each show: "solo" (Friday alone) or "duo" (two hosts).
        # "any" is every episode not made by a routine. Recommended values;
        # podcast_engine.RECOMMENDED_FORMAT is the same table.
        "format": {"briefing": "solo", "front_page": "solo", "editorial": "solo",
                   "weekly": "duo", "any": "duo"},
        "length": {"front_page": "short", "briefing": "short",
                   "weekly": "standard", "editorial": "standard"},
        "hosts": {"a": {"name": "Friday", "voice": "af_heart"},
                  "b": {"name": "Emma", "voice": "bf_emma"}},
        "on_ready": "notify",
        "cloud_voice": False,
    },
    "idle_work": {
        "enabled": True,
        "idle_after_s": 600,   # how long away before idle work starts
        "from_hour": 9,        # never overnight on a machine left on by accident
        "to_hour": 23,
    },
    # ── Scheduled jobs on a cloud model (services/scheduled_cloud.py) ──
    # The built-in jobs (news, front page, briefing, daily creation, heartbeat)
    # are local-only. With no local model serving they are skipped, unless the
    # owner answers yes here (setup chat, or Settings > Spending). Unanswered
    # and not allowed until then. A local model that is serving always wins.
    # Both models default to Claude Haiku 4.5 ($1 / $5 per million tokens):
    # the jobs summarise and draft short pieces from material they are handed,
    # which Haiku does well, and at a fifth of Sonnet's input price. The
    # heartbeat runs every 4 hours between 08:00 and 20:00 in cloud mode.
    "scheduled_cloud": {
        "answered": False,
        "allow": False,
        "at": None,
        "heartbeat_model": "claude-haiku-4-5-20251001",
        "job_model": "claude-haiku-4-5-20251001",
        "heartbeat_every_minutes": 240,
        "heartbeat_from_hour": 8,
        "heartbeat_to_hour": 20,
    },
    # ── Family / Minor mode (§7) ──
    # When on, generation runs an age-appropriate filter ON TOP of the adult harm
    # floor — this half is real and re-checked live on every generation call.
    # Gallery-side hiding of adult-rated or already-existing content is COMING
    # SOON — not yet implemented: no creation record carries an adult/rating
    # field today, and the gallery list currently filters only by file type and
    # filename, so anything generated before the toggle was on (or by another
    # user of this install) is still visible there for now. Coming soon means
    # exactly that — nothing here builds it. Until it ships, this setting
    # filters what gets generated going forward, not what already exists — a
    # parent toggles it off in Settings.
    "minor_mode": False,
    # ── Ask before opening a file or a link? ──
    # Off. Opening something on your own machine, because you just asked for
    # it, is reversible and is not what a confirmation gate is for. It was on,
    # and that is why "open all nine of these" stopped after none of them: the
    # gate denies the call and instructs the model to stop and wait for a yes.
    "confirm_before_opening": False,
    # ── Camera tracking: how the hologram reacts to your head and hand ──
    #
    # Read by the browser, not by Python - the scene and the cursor both live
    # in index.html. They are declared here because that is the only way a
    # setting survives `_load_settings_raw()`, which whitelists top-level keys
    # against this dict and silently drops anything else.
    #
    # Defaults chosen by measuring the previous hard-coded behaviour and then
    # fixing what felt wrong:
    #   * head_smoothing 0.35 - the old filter was a fixed per-FRAME lerp of
    #     0.08, about a 200 ms lag that also changed with frame rate. A One
    #     Euro filter is steady when still and quick when moving, so this can
    #     be much lower without the jitter that used to justify it.
    #   * hand_gain 2.2 and hand_region 0.45 - the old cursor mapped the WHOLE
    #     camera frame to the whole screen at 1:1, so crossing the screen meant
    #     sweeping your arm across the camera's entire view. A smaller active
    #     region with gain is what makes small movements cover the screen.
    #   * pinch_enter/pinch_exit differ on purpose. One threshold chatters at
    #     the boundary; the gap is the hysteresis.
    # Call mode (services/call_watch): when another app takes the camera or
    # the mic, Friday stands back on its own - releases the webcam and mic,
    # holds the scene, parks the brain seat - and comes back when the call
    # ends. "automatic" (recommended), "ask" (a chip, once per call), "off".
    "call_mode": "automatic",
    "tracking": {
        "parallax_strength": 1.0,
        "depth_strength": 1.0,
        "head_smoothing": 0.35,
        "holo_cues": 0.6,
        # The hologram window's depth. head.z is octaves nearer (log2 of the
        # face-width ratio against the calibrated neutral), and the "zoom" is
        # 2 ** (z * depth_strength): how many times nearer the glass the eye
        # sits than at rest, so at 1.0 it follows your real distance.
        # zoom_in_max and zoom_out_max bound it; the engine caps zoom_in_max
        # at 2.5 so the eye can never reach the avatar behind the glass.
        # head_response is the One Euro speed term on its own (how hard a
        # quick lean is followed); head_smoothing above stays the stillness
        # cutoff. neutral_face_width is the calibrated face-box width for
        # "sitting normally"; 0 means not calibrated and the engine assumes
        # 0.18. viewing_distance_cm and screen_width_cm are the window's
        # scale, so a head movement becomes the same movement behind the
        # glass; screen_width_cm 0 means worked out from the display.
        "zoom_in_max": 1.8,
        "zoom_out_max": 1.5,
        "head_response": 0.5,
        "neutral_face_width": 0,
        "viewing_distance_cm": 60,
        "screen_width_cm": 0,
        # Dock depth: shelf tilt, how far icons stand off the shelf, and how
        # much a button swells as the pointer nears it. 0 is the flat dock.
        "dock_depth": 1.0,
        "hand_gain": 2.2,
        "hand_accel": 0.6,
        "hand_region": 0.45,
        "hand_deadzone": 0.006,
        "hand_smoothing": 0.30,
        "click_method": "pinch",
        "pinch_enter": 0.050,
        "pinch_exit": 0.075,
        "dwell_ms": 700,
        # The hand cursor layer (static/hand_cursor.js): magnetic snap to targets, its reach in
        # pixels (release is 1.6x), and two-hand zoom (a second tracked hand costs CPU).
        "snap": True,
        "snap_radius": 40,
        "two_hand_zoom": False,
        "debug_overlay": False,
    },
    # ── Which scanner decides whether an action needs your sign-off ──
    #
    # `dissent_gate.classify_severity` - a scan for ~40 substrings - decides
    # whether an action reaches you as an approval card, including sending
    # mail as you. `laya-union` adds a second opinion (a 421M encoder on CPU,
    # ~400 ms) and gates when EITHER votes to gate.
    #
    # Measured on a 27-case adversarial set: the keyword scan
    # missed 5 outward actions, Laya missed 1, and their misses were DISJOINT,
    # so taking either vote missed none. The union scores lower overall (22/27
    # vs Laya's 23) and is still the right mode, because a missed gate sends
    # mail with no human in the loop and a false gate costs one approval card.
    #
    # SHIPPED IN SHADOW (below), not on. The week-one review (09-22..09-29)
    # found Laya had judged only 6 of ~35 real actions - the rest were skipped
    # as too slow or it was switched off - so there is not yet the evidence to
    # let it add cards. Shadow scores everything and changes nothing.
    #
    # When it is promoted, what makes "on" safe is structural rather than statistical: `keyword` is
    # one of the two inputs to the OR, so there is no input on which this
    # REMOVES a card the substring scan would have raised. A missing model, a
    # corrupt download or a load still in progress costs approval cards, never
    # a silent send - `union_backend` falls back to a pure keyword verdict and
    # says which in the decision log.
    #
    # An unregistered name here falls back to `keyword` loudly rather than
    # raising, so a typo cannot take the approval gate offline.
    "decision_backend": "keyword",
    # Score a second backend alongside the deciding one and write both answers
    # to ~/.friday/decisions.jsonl, changing no verdict. "" is off. This is how
    # a candidate earns the seat above; it is not itself a gate.
    "decision_shadow": "laya",
    # A read at a service the owner connected (a connector tool whose name
    # leads with get/list/search/show ...): "observe" runs it without a card,
    # labelled outward and receipted, unless its arguments would carry private
    # data out or Laya / the keyword scan flags it (governance/action_gate
    # _connector_read). "card" asks about every one, as before 2026-09-29.
    # The owner: "I think it should also be a high priority that we don't
    # constantly pester the user with approval cards for every single command."
    "outward_reads": "observe",
    # Optional chat preparation experiment. Alternate ordinary and assisted
    # turns; Laya only prepares read schemas/context and never grants actions.
    "laya_pilot_enabled": False,
    # How Laya runs on the CPU (services/laya_runtime): "auto" takes the
    # fastest engine measured and checked on this PC, else laya's own fp32.
    # "torch-fp32" | "torch-int8" | "onnx-int8" pin one.
    "laya_runtime": "auto",
    # How hard a local reasoning seat thinks (services/reasoning_policy, read
    # by the model router's local payload). "auto" lets the turn's shape
    # decide; "medium" | "xhigh" | "none" pin one effort on every turn;
    # "default" sends nothing and leaves the model to its own. Declared here
    # because `_load_settings_raw` keeps only declared keys: a value written
    # to settings.json for an undeclared key is dropped on every read.
    "local_reasoning_effort": "auto",
    # What "auto" may do with a Laya 2 turn-shape verdict. `deep_xhigh` lets a
    # deep coding or analysis turn think at xhigh. `reflex_thinking_off` lets
    # a reflex-shaped turn the brain still answers skip thinking; it ships
    # off until the strict tool-call harness holds within 2 points on those
    # shapes with thinking off. With both as shipped, an ordinary turn sends
    # `medium`, as before.
    "local_reasoning_effort_policy": {"reflex_thinking_off": False,
                                      "deep_xhigh": True},
    # ── Creative policy (services/creative_policy.py) ──
    # What Friday refuses to generate, written down where the user can read
    # and set it. Before this existed there was nothing legible for a seat to
    # consult, so a 12B model improvised — and invented a "hard-coded model
    # filter" that does not exist rather than say it was declining.
    # The shipped value is exactly the behaviour that was already in force.
    # `harm_floor.enforced` is re-asserted True on load and is not a switch.
    "creative_policy": {
        "harm_floor": {"enforced": True},
        "additional_blocked_categories": [],   # {label, pattern}; tightens only
        "refusal_style": "plain",              # plain | brief — wording only
    },
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
    # Local runtime-stack root (decision D7). Empty = ~/.friday/runtime.
    # Point this at another drive when the system disk is tight — the GGUF
    # weights, ComfyUI models and voice venvs here run to tens of GB.
    # FRIDAY_RUNTIME_DIR overrides this. See core.runtime_dir().
    "runtime_dir": "",
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
    # ── Display reserve (services/hardware_profile.display_reserve_floor_mib) ──
    # "adaptive": the desktop is granted its measured idle VRAM draw plus
    # 512 MiB, never under 1 GiB on Windows, once the Arbiter has taken an idle
    # baseline; unmeasured machines keep the fixed 2,560 MiB. "fixed" pins the
    # old constant everywhere. The breach handler is unchanged in both modes.
    "display_reserve_mode": "adaptive",
    # When the brain's seat answers /health, send its canonical prompt head as
    # a one-token request so the first real turn after a restart reads only
    # itself (services/seat_warm). False leaves the first turn to pay the read.
    "seat_prefix_warm": True,
    # ── Model Routing (Ollama local inference) ──
    # mode: cloud_only (default, no change), smart, local_preferred, local_only
    "model_routing": {
        "mode": "cloud_only",
        "default_cloud_model": "claude-sonnet-5",
        "task_overrides": {},
        "ollama_url": "http://localhost:11434",
        # Default on-device model: model_plan.FLOOR_MODEL, taken from the
        # ladder so it cannot drift from what the installer actually
        # installs. Picked for every local route when installed (see
        # model_router._pick_local_model); if it isn't installed the picker
        # degrades to any installed model.
        #
        # This is a Gemma 4 model (currently gemma4:e2b). Qwen is not on the
        # ladder at all, per the product decision above `model_plan._BRAINS`:
        # when the installer fetches a local model it fetches Gemma 4 only
        # (Apache 2.0), a placeholder until FutureSpeak's own model replaces
        # it. Users with more card can upgrade to gemma4:e4b, :12b, or :26b —
        # whatever `friday models` offers for the hardware it detects. Do not
        # recommend gemma3:12b / gemma3:27b here: neither can call tools, so
        # that advice points users at a bigger version of the problem the
        # tool-capable floor exists to avoid.
        "local_model": _FLOOR_MODEL,
        # ── Explicit cloud-consent record ──
        # {"answered": bool,
        #  "choice": "local_private"|"cloud_unrestricted"|"cloud_guarded"|None,
        #  "at": iso-str|None, "capability_snapshot": dict|None}. The ONLY thing
        # `privacy.cloud_consent.is_unrestricted_cloud()` reads to decide whether
        # every safeguard is off. See `privacy/cloud_consent.py` for why this
        # exists (a passively-inherited factory default is not a decision) and
        # for why this key is stripped from every generic settings write —
        # `core._save_settings()` only accepts it from its one blessed internal
        # caller, `privacy.cloud_consent.record_consent()`. Writing this key any
        # other way (including by hand in this file) does nothing: the read side
        # requires the shape this module writes, not merely a truthy value.
        "cloud_consent": {"answered": False, "choice": None, "at": None,
                          "capability_snapshot": None},
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
        "openai_model": "anthropic/claude-sonnet-5",
        "openai_api_key": "",   # blank → falls back to env OPENAI_API_KEY / OPENROUTER_API_KEY
        # ── Sovereign Vault access control ──
        # vault_local_only: when true, vault TIER_2/TIER_3 content reaches local
        #   models only; vault-touching requests are force-routed to Ollama.
        # vault_cloud_fallback: what to do when a vault request can't run locally
        #   "redact" = send a placeholder to cloud, "deny" = refuse entirely,
        #   "warn"   = refuse and ask the user to enable a local model.
        "vault_local_only": True,
        "vault_cloud_fallback": "redact",
        # ── Unrestricted cloud mode (superseded by cloud_consent) ──
        # Legacy flag: bypasses every gate in the codebase
        # for cloud sends (tier classification, redaction, the PII scrub, the
        # never-send list) when True. `is_unrestricted_cloud()` no longer reads
        # this live — it reads `cloud_consent` above instead, because this flag
        # is reachable through the generic settings-save path (the same shape
        # of bug `enterprise_consent_grant` was removed for: something other
        # than a genuine, informed human choice could flip it). Kept only as a
        # ONE-TIME migration input: an install that had already set this to
        # True explicitly is treated as having already made the choice
        # `cloud_consent` now records, so nobody who deliberately opted in
        # under the old design gets silently re-gated. See
        # privacy/cloud_consent.py.
        "unrestricted_cloud": False,
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
        "reasoning":      {"provider": "anthropic",     "model": "claude-sonnet-5"},
        # The heavy seat: the most capable local model, loaded on demand for
        # hard turns. It MUST be declared here — `_sync_capability_routing`
        # rebuilds capability_routing from these keys alone, so a capability
        # missing from this table is silently dropped on every settings save.
        # Otherwise a binding that writes `heavy_hitter` is deleted by the next
        # save and the seat never appears in settings or the UI.
        "heavy_hitter":   {"provider": "ollama-local",  "model": ""},
        "subagent":       {"provider": "anthropic",     "model": "claude-sonnet-5"},
        # The five working roles. Declared with an EMPTY model on purpose:
        # rule R11 says nothing is assigned until the user says so, and an
        # empty seat awaiting a choice is the normal state, not a failure.
        # They must appear here even so -- `_sync_capability_routing` rebuilds
        # capability_routing from these keys alone, so a capability missing
        # from this table is silently dropped on every settings save (the
        # `heavy_hitter` note above).
        "orchestrator":     {"provider": "ollama-local", "model": ""},
        "sidekick_fast":    {"provider": "ollama-local", "model": ""},
        "function_manager": {"provider": "ollama-local", "model": ""},
        "memory_manager":   {"provider": "ollama-local", "model": ""},
        "researcher":       {"provider": "ollama-local", "model": ""},
        "creative_image": {"provider": "google-gemini", "model": "gemini-nano-banana-2"},
        "creative_video": {"provider": "google-gemini", "model": "veo-3"},
        "creative_music": {"provider": "google-gemini", "model": "lyria-clip"},
        "voice":          {"provider": "google-gemini", "model": "gemini-3.8-live"},
        # Local voice splits into asr + tts; both default to the on-device Tier-1
        # engine (the cloud "voice" entry above is used only when the user opts
        # into Gemini Live). A single user-facing `voice_engine` selector drives
        # these under the hood; power users can override each independently.
        "asr":            {"provider": "local-voice-lite", "model": "whisper-small"},
        "tts":            {"provider": "local-voice-lite", "model": "piper-en_US-amy-medium"},
        "embedding":      {"provider": "local",         "model": "all-MiniLM-L6-v2"},
        "local":          {"provider": "ollama-local",  "model": _FLOOR_MODEL},
    },
    # ── Content pipeline (docs/design/implemented/content-pipeline-spec.md) ──
    "content": {
        "enabled": True,                 # master switch for the publish pipeline
        "conflict_window_hours": 2,      # same-platform proximity warning (§6.8)
        "psi_daily_cap": 200,            # engagement→ψ daily minting cap (§8.7)
        "staging_base_url": "",          # public staging host for URL-pull platforms (§7.4)
    },
    # ── Durable Goals — approval policy (V6 P5 / AUTONOMY_SPEC A3) ──
    # Q3 policy table: which action classes gate behind a human approval by
    # default. gated=True blocks until approved (or expires_seconds elapses
    # ungated -> "expired", never auto-approved); gated=False auto-proceeds
    # with a receipt. Per-class overrides only (class names are fixed). See
    # services/approvals.py::effective_policy_table / classify.
    "approvals_policy": {
        "outward":          {"gated": True,  "expires_seconds": 86400},
        "irreversible":     {"gated": True,  "expires_seconds": 86400},
        "spend":            {"gated": True,  "expires_seconds": 86400},
        "external_message": {"gated": True,  "expires_seconds": 86400},
        "internal":         {"gated": False, "expires_seconds": None},
    },
    # ── Hang watchdog ──
    # services/hang_watchdog.py dumps every thread's stack to
    # ~/.friday/logs/hang-dump-*.log when the process goes unresponsive
    # while still alive (a silent stall otherwise leaves no trace in
    # friday.log of why). auto_restart_after_dump: when true, the
    # server self-exits shortly after writing a dump so the tray's watchdog
    # relaunches it — OFF by default; a dump alone is diagnostic, opting
    # into an unattended restart is the user's call.
    "hang_watchdog": {
        "enabled": True,
        "heartbeat_interval_s": 15,
        "stall_threshold_s": 90,
        "auto_restart_after_dump": False,
    },
    # ── Google OAuth redirect ──
    # Both Google connectors (services/calendar_engine.py legacy single-
    # account, services/google_accounts.py multi-account) pin their OAuth
    # redirect_uri to loopback (http://localhost:3000/...) regardless of the
    # request's Host header. Google's secure-response-handling policy
    # rejects any plain-HTTP non-loopback redirect_uri outright — a
    # hosts-file alias like http://agent.friday/... fails every time,
    # independent of DNS/propagation. redirect_base_override exists only for
    # a genuine HTTPS-terminated reverse-proxy setup, where that exact base
    # URL must ALSO be registered as an Authorized redirect URI in the GCP
    # console for both callback paths.
    "google_oauth": {
        "redirect_base_override": "",
    },
    # ── Friday's address on this PC (services/local_address.py) ──
    # host: "" means "from the agent's name" (AGENT FRIDAY -> agent.friday).
    # serve: Friday's own loopback listeners on https_port/http_port, turned on
    # by the Settings button and never at install. Trusting the certificate and
    # adding the name to the hosts file are separate steps Windows asks the
    # person about; neither is a setting.
    "local_address": {
        "host": "",
        "serve": False,
        "https_port": 443,
        "http_port": 80,
    },
    # ── Offering meeting times (services/scheduling.py) ──
    # timezone: IANA name; "" means this computer's own zone. Working hours
    # are wall-clock times in that zone; working_days are Monday=0..Sunday=6.
    # min_notice_hours and buffer_minutes are the defaults find_free_slots
    # uses when a request does not give its own.
    "scheduling": {
        "timezone": "",
        "working_hours": {"start": "09:00", "end": "17:00"},
        "working_days": [0, 1, 2, 3, 4],
        "min_notice_hours": 12,
        "buffer_minutes": 15,
    },
    # ── The career-ops checkout (services/career_ops.py) ──
    # path: the folder; "" means <home>/Projects/career-ops.
    "career_ops": {
        "path": "",
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

# provider_family() families → provider registry names.
_FAMILY_PROVIDER = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "google-gemini",
    "local": "ollama-local",
}


def _provider_for_model(model_id):
    """Registry provider name a model id dispatches to, or None if unknown.

    Keeps capability_routing[cap]["provider"] congruent with the model the flat
    key actually routes to — without this, picking a local model in the UI left
    provider at its old cloud value, which poisoned /api/capabilities badges and
    the local-voice brain's vault-fidelity gate.

    Registry-first (resolver) so aggregator ids map to their OWNING provider —
    an OpenRouter "org/model:variant" pick labels as openrouter, not as local
    on the strength of its ':'. Family heuristics remain the fallback.
    """
    try:
        from agent_friday.routing.provider_descriptors import resolve_provider_name
        name = resolve_provider_name(model_id)
        if name:
            return name
    except Exception:
        pass
    try:
        from agent_friday.routing.model_router import provider_family
        return _FAMILY_PROVIDER.get(provider_family(model_id))
    except Exception:
        return None


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
                # Keep provider congruent with the model the flat key dispatches
                # to (dispatch uses provider_family(model), never this field —
                # but /api/capabilities and the voice vault gate read it).
                # Heal ONLY the four first-party provider names: an explicitly
                # configured custom provider (groq/openrouter/…, which serve
                # models of any family) must never be silently rewritten.
                prov = _provider_for_model(fv)
                cur_prov = entry.get("provider")
                if prov and prov != cur_prov and (
                        not cur_prov or cur_prov in _FAMILY_PROVIDER.values()):
                    entry["provider"] = prov
        if not entry.get("provider"):
            entry["provider"] = defaults[cap]["provider"]
    return settings

    # A local model that has been uninstalled must not survive a save.
    #
    # The flat key outranks capability_routing whenever routing was not
    # explicitly part of `changed`, so a stale `orchestrator_model` re-stamps
    # itself over the canonical entry on EVERY write. A removed model can
    # therefore persist in the flat key indefinitely, which makes the model
    # picker appear not to stick no matter what the picker does.
    # Healing here — at the single point both representations pass through —
    # is the only place the repair cannot be undone by the next writer.
    #
    # Not under test: the repair asks the real Ollama daemon what is installed,
    # so leaving it on made every settings-touching test depend on this
    # machine's model inventory — and it duly rewrote fixture models that
    # happen not to be installed here, breaking eight arbiter tests that had
    # nothing to do with seats.
    if not os.environ.get("FRIDAY_TESTING"):
        try:
            from agent_friday.services.local_seats import heal as _heal_seats
            import logging as _seatlog
            _seen_notes = globals().setdefault("_SEAT_NOTES_LOGGED", set())
            for _note in _heal_seats(settings):
                # print() writes to a console nobody reads, so a seat repair
                # left no trace anyone could find afterwards. A seat changing
                # under the user, or an inventory we could not check, is
                # exactly the state change the user is entitled to discover later.
                #
                # Once per distinct note, not once per settings load. This runs
                # on every cache miss, so an unreachable daemon would otherwise
                # repeat one warning until the log is worth nothing — the same
                # flood that made the VRAM dispute line useless until it was
                # made change-triggered.
                if _note not in _seen_notes:
                    _seen_notes.add(_note)
                    _seatlog.getLogger("friday.settings").warning(
                        "seats: %s", _note)
                print(f"  [seats] healed settings - {_note}")
        except Exception:
            pass


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
        _gen = _SETTINGS_CACHE_GEN[0]

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
        # utf-8-SIG, not utf-8. A leading U+FEFF is a JSONDecodeError, and the
        # except below turns that into a SILENT, TOTAL reversion to
        # DEFAULT_SETTINGS: every seat, every routing mode, every stored key
        # replaced by the factory value, with nothing logged and no visible
        # failure. settings.json can gain a BOM at any time (PowerShell 5.1's
        # Out-File/`>` writes UTF-8 WITH BOM by default). Every key on disk is
        # then intact and correct while the running process reads none of
        # them. Observable symptoms, all downstream of those three bytes:
        #
        #   * model_routing.mode read as the factory `cloud_only` against the
        #     `local_preferred` on disk, so every turn went to Anthropic;
        #   * orchestrator/subagent seats read as claude-sonnet-5 against the
        #     gemma4:12b on disk;
        #   * creative_image read as Gemini against the local SD 3.5 on disk;
        #   * seat_transparency dutifully announced all of it as a seat change,
        #     which is the one part that worked — it reported a change nobody
        #     made.
        #
        # Several modules in this tree already read utf-8-sig for exactly this
        # reason (services/role_consumers.py:_module_source names three BOM'd
        # source files). The settings loader is the one place where getting it
        # wrong costs every setting at once.
        data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8-sig'))
        # Fill in any missing keys with defaults
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        _sync_capability_routing(merged)
        with _SETTINGS_CACHE_LOCK:
            if _SETTINGS_CACHE_GEN[0] == _gen:
                _SETTINGS_CACHE["value"] = merged
                _SETTINGS_CACHE["ts"] = _time.time()
        return merged
    except Exception as e:
        # NEVER SILENT AGAIN. Reverting to defaults is a defensible last
        # resort; doing it without a word is what made the BOM incident take
        # an afternoon to see. The settings file existing and being unreadable
        # is a different event from it not existing, and it must say so.
        try:
            import logging as _lg
            _lg.getLogger("friday.settings").error(
                "settings.json exists but could not be parsed (%s: %s) — "
                "RUNNING ON FACTORY DEFAULTS. Every seat, routing mode and "
                "stored preference in that file is being ignored until it "
                "parses. Check for a byte-order mark or truncation: %s",
                type(e).__name__, e, SETTINGS_FILE)
        except Exception:
            pass
        return dict(DEFAULT_SETTINGS)


def _load_settings():
    """Load agent settings, applying the offline routing overlay when offline.

    The overlay is non-persistent: it forces local inference while the network
    monitor reports OFFLINE so every provider consumer auto-switches to Ollama,
    then disappears the moment connectivity returns. Use _load_settings_raw()
    when you need the persisted values verbatim (e.g. before a settings write).
    """
    return _apply_offline_routing_overlay(_load_settings_raw())


#: Settings blocks merged FIELD BY FIELD rather than replaced wholesale.
#: Everything else in a delta overwrites its key, which is correct for scalars
#: and lists and catastrophic for a config block a caller only partly edited.
#: "content" is here because the Content workspace's global-controls Save
#: button only ever sends {staging_base_url, conflict_window_hours} (the two
#: fields it edits) — without deep-merge that wholesale-replaces the block,
#: silently resetting `enabled` and `psi_daily_cap` to nothing every time.
#: `turn_budget` joins them for the same reason: Settings sends only the group
#: it edited (rounds, or the clock, or tokens), and a wholesale replace would
#: drop the other two back to the module defaults every time one is changed.
_DEEP_MERGED_BLOCKS = ("capability_routing", "model_routing", "content",
                       "turn_budget", "local_address", "scheduled_cloud", "tracking")


def _routing_mode_caller() -> str:
    """'file:function:line' of the code that asked for this settings write."""
    import traceback as _tb
    for fr in reversed(_tb.extract_stack()):
        if fr.name in ("_save_settings", "_save_settings_locked", "_routing_mode_caller"):
            continue
        return "%s:%s:%s" % (Path(fr.filename).name, fr.name, fr.lineno)
    return "unknown"


def _save_settings(data, *, _internal_cloud_consent_write: bool = False,
                   owner_routing_change: bool = False):
    """Serialize read/merge/replace so simultaneous partial saves keep siblings."""
    with _SETTINGS_WRITE_LOCK:
        return _save_settings_locked(data,
            _internal_cloud_consent_write=_internal_cloud_consent_write,
            owner_routing_change=owner_routing_change)


def _save_settings_locked(data, *, _internal_cloud_consent_write: bool = False,
                   owner_routing_change: bool = False):
    """`_internal_cloud_consent_write` exists for exactly one caller:
    `privacy.cloud_consent.record_consent()`. Every other path into this
    function — the generic `/api/settings` POST included — has
    `model_routing.cloud_consent` silently stripped from its delta below,
    the same way `enterprise_consent_grant` was removed from the tool
    registry rather than merely discouraged: a settings key that decides
    whether every privacy safeguard is on must not be reachable through the
    write path a model's own tools (or a naive script) can already reach.
    Stripping happens even if this function is called recursively or the
    flag is guessed at, because the keyword is not part of any request body
    this process parses — it can only be set by Python code in this repo.
    """
    if not _internal_cloud_consent_write:
        mr = (data or {}).get("model_routing")
        if isinstance(mr, dict) and "cloud_consent" in mr:
            data = dict(data)
            mr = dict(mr)
            mr.pop("cloud_consent", None)
            data["model_routing"] = mr
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    # Invalidate BEFORE and AFTER the write.
    #
    # Before-only was a race with a two-second blast radius: the cache is
    # cleared, the file is then read/merged/replaced, and any reader arriving in
    # that window re-populates the cache from the OLD file and serves it for the
    # full TTL. That makes a model switch intermittently invisible: the seat
    # is on disk, the read-back confirms it, and a chat turn a moment later
    # is still routed by the previous seat, so it answers from the cloud.
    _invalidate_settings_cache()
    # Read existing file first to preserve any keys not in DEFAULT_SETTINGS
    #
    # THIS READ IS LOAD-BEARING, AND IT USED TO FAIL OPEN.
    #
    # `except: pass` left `existing = {}`, and an empty `existing` makes the
    # merge below start from DEFAULT_SETTINGS and end there — every key the
    # user ever set, replaced by the factory value and then written to disk by
    # the atomic write at the bottom. An unreadable settings file did not
    # degrade the save; it CONVERTED the save into a factory reset.
    #
    # That is how a BOM on settings.json becomes a permanent reset. The read
    # side (_load_settings_raw, above) with a utf-8-vs-utf-8-sig bug merely
    # makes the running process ignore every key — recoverable, since the
    # file is still correct. Then something calls _save_settings, this read
    # returns {}, and the defaults are persisted over the real configuration.
    # The BOM is stripped by that same write, so the evidence of the cause
    # disappears in the act of causing the damage: afterwards the file looks
    # clean and merely wrong.
    #
    # Note the second-order loss too. The capability_routing deep-merge below
    # is guarded on `isinstance(existing.get(k), dict)`; with `existing` empty
    # that guard is False, so routing is replaced wholesale rather than merged
    # per capability — the exact reset its own comment exists to prevent.
    #
    # So: read BOM-tolerantly, and if the file exists and still will not
    # parse, REFUSE THE SAVE. A settings write that cannot see the current
    # settings is not a write, it is an erasure. Better to fail loudly and
    # leave the file alone than to persist a reset nobody asked for.
    existing = {}
    if SETTINGS_FILE.exists():
        try:
            existing = json.loads(SETTINGS_FILE.read_text(encoding='utf-8-sig'))
        except Exception as e:
            try:
                import logging as _lg
                _lg.getLogger("friday.settings").error(
                    "REFUSING to save settings: %s exists but could not be "
                    "parsed (%s: %s). Writing now would replace every stored "
                    "preference with a factory default. The file has been "
                    "left untouched; fix or remove it and retry.",
                    SETTINGS_FILE, type(e).__name__, e)
            except Exception:
                pass
            raise RuntimeError(
                "settings.json exists but is unreadable (%s); refusing to "
                "overwrite it with defaults" % e) from e
    # THE ROUTING MODE CHANGES ONLY ON AN EXPLICIT OWNER ACTION.
    #
    # model_routing.mode decides where every turn goes and what it costs. A
    # write that merely carries a mode (a whole settings dict read earlier, a
    # UI block spread from a stale copy, a migration, a test) must never move
    # it. Only the owner's own mode controls pass `owner_routing_change`; any
    # other change of the mode is dropped from the write and logged with the
    # caller, and every accepted change is logged old -> new.
    _mr_in = (data or {}).get("model_routing")
    if isinstance(_mr_in, dict) and "mode" in _mr_in:
        _old_mode = (existing.get("model_routing") or {}).get("mode") \
            if isinstance(existing.get("model_routing"), dict) else None
        _new_mode = _mr_in.get("mode")
        if _new_mode != _old_mode and existing:
            import logging as _lg
            _who = _routing_mode_caller()
            if owner_routing_change:
                _lg.getLogger("friday.settings").warning(
                    "routing mode changed %s -> %s by an explicit owner action (%s)",
                    _old_mode, _new_mode, _who)
            else:
                _lg.getLogger("friday.settings").warning(
                    "REFUSED a routing mode change %s -> %s: not an explicit owner "
                    "action (caller %s); the rest of the write is kept",
                    _old_mode, _new_mode, _who)
                data = dict(data)
                _mr_in = dict(_mr_in)
                _mr_in.pop("mode", None)
                data["model_routing"] = _mr_in
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in existing.items()})
    for k, v in (data or {}).items():
        if (k in _DEEP_MERGED_BLOCKS and isinstance(v, dict)
                and isinstance(existing.get(k), dict)):
            # Deep-merge per key: a partial delta (the wizard and the Settings
            # sections send only the fields they edited) must not reset every
            # untouched sibling back to defaults.
            #
            # `model_routing` is deep-merged for the same reason with a worse
            # blast radius: a UI that saves {"model_routing":
            # {"vault_local_only": ...}} would otherwise replace the whole
            # block, silently resetting `mode` — so changing a privacy switch
            # could move every future turn to a different provider.
            base = {ck: (dict(cv) if isinstance(cv, dict) else cv)
                    for ck, cv in existing[k].items()}
            base.update(v)
            merged[k] = base
        else:
            merged[k] = v
    # Reconcile capability_routing ⇄ flat *_model keys before persisting so the
    # wizard, Settings UI and router never disagree about the active models.
    _sync_capability_routing(merged, data)
    # Atomic write: write to a sibling temp file, fsync, then rename so a crash
    # mid-write never leaves a half-written (corrupt) settings.json.
    # The temp name is UNIQUE per write, not the shared SETTINGS_FILE.tmp it
    # used to be. Friday saves settings from background threads as well as
    # request handlers, and with one shared temp path two concurrent writers
    # scribble over the same file — so a writer could replace() using a temp
    # the OTHER writer was still filling, persisting a mixed settings.json.
    # That is the exact corruption the atomic write exists to prevent, and the
    # shared name reintroduced it under concurrency.
    _fd, _tmp_name = tempfile.mkstemp(
        dir=str(SETTINGS_FILE.parent), prefix='.settings-', suffix='.tmp')
    _tmp = Path(_tmp_name)
    try:
        with os.fdopen(_fd, 'w', encoding='utf-8') as _f:
            _f.write(json.dumps(merged, indent=2))
            _f.flush()
            os.fsync(_f.fileno())
        # Windows denies a replace while any other handle holds the target
        # (a concurrent reader, an indexer, AV). That surfaced as WinError 5
        # turning a settings save into a 500. Retry briefly rather than lose
        # the write; the file is already complete and fsynced by here.
        for _attempt in range(10):
            try:
                _tmp.replace(SETTINGS_FILE)
                break
            except PermissionError:
                if _attempt == 9:
                    raise
                _time.sleep(0.02)
    except Exception:
        try:
            _tmp.unlink()
        except Exception:
            pass
        raise
    # The write is complete and on disk; clear again so nothing keeps a
    # snapshot taken mid-write.
    _invalidate_settings_cache()
    # Switching off the record off drops what the session kept in memory.
    try:
        from agent_friday.services import off_record as _off
        _off.on_settings_change({**DEFAULT_SETTINGS, **existing}, merged)
    except Exception:
        pass
    # A change to what guards outward actions is recorded where it happens.
    try:
        from agent_friday.services import decisions as _dec
        _dec.on_settings_change({**DEFAULT_SETTINGS, **existing}, merged)
    except Exception:
        pass
    # Turning the knowledge graph's learning from the Library off takes back what it learned.
    try:
        from agent_friday.services.library import runtime as _lib_rt
        _lib_rt.on_settings_change({**DEFAULT_SETTINGS, **existing}, merged)
    except Exception:
        pass
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
    """Load Friday's personality.

    Priority (v5): ~/.friday/SOUL.md (user-editable markdown) → the legacy
    agent-personality.txt → the hardcoded DEFAULT_AGENT_PERSONALITY. The SOUL.md
    body is rendered by services/soul.py (title/editor-note stripped) so the
    model receives the substance; if that service is unavailable the raw file is
    used as a fallback.
    """
    if SOUL_FILE.exists():
        try:
            from agent_friday.services import soul as _soul
            text = _soul.render_personality().strip()
            if text:
                return text
        except Exception:
            try:
                text = SOUL_FILE.read_text(encoding='utf-8').strip()
                if text:
                    return text
            except Exception:
                pass
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
SELF_MD_PATH = _res_file("SELF.md")


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
VOICE_DEMO_MD_PATH = _res_file("VOICE_DEMO.md")


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


#: Text-chat reply length and tone, from Settings. Named so the live voice
#: prompt can remove them: voice sets its own length by the moment and its
#: tone from the voice persona (services/voice_persona.py).
RESPONSE_LENGTH_HINTS = {
    'concise': 'Be terse — 1–3 sentences unless detail is explicitly required.',
    'standard': 'Be reasonably brief — direct answer plus the minimum useful context.',
    'detailed': 'Be thorough — explain reasoning, list options, surface tradeoffs.',
}
COMMUNICATION_STYLE_HINTS = {
    'professional': 'Tone: composed, professional, plainspoken.',
    'casual':       'Tone: relaxed and conversational, like a trusted colleague.',
    'technical':    'Tone: precise and technical; use exact terminology and code where helpful.',
}


#: How long Friday talks, for every surface: adaptive, never a fixed cap.
ADAPTIVE_LENGTH_LINE = ("Match your length to the moment: a sentence or two for quick "
                        "back-and-forth, fuller answers for the news, explanations and stories.")
#: The absolute length line earlier SOUL.md defaults shipped with. A personality
#: file that still carries it reads the adaptive line in its place.
LEGACY_LENGTH_LINE = "Keep responses short and sharp — like texting a smart colleague."


def _settings_system_prefix(settings, personality):
    """Build the prefix that gets prepended to every chat system prompt."""
    personality = (personality or "").replace(LEGACY_LENGTH_LINE, ADAPTIVE_LENGTH_LINE)
    length_hint = RESPONSE_LENGTH_HINTS.get(settings.get('response_length', 'standard'), '')
    style_hint = COMMUNICATION_STYLE_HINTS.get(settings.get('communication_style', 'professional'), '')
    # TWO SETTINGS ABOUT CITATIONS, ONE PROMPT. `include_sources` (default
    # True) put a vague "always cite the source inline" here; `cite_sources`
    # (Source Production Mode, default False) appends CITATION_INSTRUCTIONS —
    # the strict bracket grammar the UI can actually parse and the provenance
    # layer can actually check — later in the same prompt. Opposite defaults,
    # same subject, and the specific one arrived last, so in practice
    # cite_sources already won whenever it was on. Nothing said so anywhere.
    #
    # Made explicit rather than left to ordering: cite_sources is the control.
    # When it is on, this softer hint is SUPPRESSED, because two instructions
    # about the same duty is how a model gets told to cite "(source: wiki)" in
    # prose that no parser recognises and no provenance check can verify.
    # `include_sources` keeps its old meaning only in the mode's absence:
    # mention where something came from, conversationally, uncounted.
    if settings.get('cite_sources', False):
        sources_hint = ''
    else:
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


def _origin_gate_reason():
    """Why the current request is refused by the session-token / cross-site gate,
    or None. A browser request that changes state (or upgrades to a WebSocket)
    must carry the session token; requests with no browser metadata (tray, CLI)
    pass. Fails closed: if the gate cannot be evaluated, a guarded request is
    refused rather than waved through."""
    try:
        from agent_friday.services import origin_gate as _og
        from agent_friday.services.local_address import own_origins as _oo
    except Exception:
        _og = None
    if _og is None:
        return ("I couldn't check where that request came from, so I refused it. "
                "Reload Friday's page and try again."
                if (request.method or "").upper() in ("POST", "PUT", "PATCH", "DELETE")
                or "websocket" in (request.headers.get("Upgrade") or "").lower()
                else None)
    def _inputs():
        tok = request.headers.get("X-Friday-Token") or (
            request.args.get("t") if _og._is_upgrade(request.headers) else None)
        return dict(host=request.host, is_local=_is_local_request(),
                    token_valid=_api_token_valid(tok), own_origins=_oo())
    try:
        kw = _inputs()
    except Exception:
        return _og.GATE_ERROR_REASON if _og.is_guarded(request.method, request.headers) else None
    # The sign-in form is how a remote visitor gets a session, so it cannot
    # carry the token; it is still refused when another site sent it.
    kw["token_required"] = request.endpoint != "login"
    return _og.refusal_or_closed(request.method, request.headers, **kw)


def _host_gate_reason():
    """Why the current request is refused because its Host header does not name
    Friday, or None. Runs before loopback trust and covers reads: a DNS-rebound
    page looks same-origin to the browser and would otherwise read everything
    loopback is trusted with, the session token included. Fails closed."""
    try:
        from agent_friday.services import origin_gate as _og
        from agent_friday.services.local_address import own_origins as _oo, local_hosts as _lh
        return _og.host_refusal(
            request.host, is_local=_is_local_request(), own_origins=_oo(),
            extra_hosts=set(_LOCAL_FORWARDED_HOSTS) | set(_lh()))
    except Exception:
        return ("I couldn't check where that request came from, so I refused it. "
                "Reload Friday's page and try again.")


def _frame_gate_reason():
    """Why the current request is refused because a sandboxed or foreign
    document sent it to a sensitive route, or None. A session token proves who
    holds it, not which document is using it, so this holds with a valid token
    too. Fails closed under /api/ and /ws/."""
    try:
        from agent_friday.services import origin_gate as _og
        from agent_friday.services.local_address import own_origins as _oo
        return _og.frame_refusal_or_closed(
            request.method, request.headers, request.path,
            host=request.host, is_local=_is_local_request(), own_origins=_oo())
    except Exception:
        if (request.path or "").startswith(("/api/", "/ws/"))                 and not (request.path or "").startswith("/api/health"):
            return ("I couldn't check where that request came from, so I refused it. "
                    "Reload Friday's page and try again.")
        return None


@app.before_request
def check_auth():
    # Observer credential (services/observer_access; task-visibility.md §4.5):
    # a request presenting X-Friday-Observer is READ-ONLY for the whole
    # request, decided here BEFORE loopback trust so the header can only
    # demote. Only GET on the allowlisted read routes passes; steer, cancel,
    # delete, settings, spawning and minting are refused whatever the caller's
    # address. An invalid token is refused outright.
    try:
        from agent_friday.services import observer_access as _obs
        _obs_token = _obs.presented(request.headers)
    except Exception:
        _obs_token = None
    if _obs_token is not None:
        try:
            g.friday_principal = "observer"
        except Exception:
            pass
        if not _obs.verify(_obs_token):
            return jsonify({"error": "observer credential not recognised"}), 401
        if not _obs.is_read_allowed(request.method, request.path):
            return jsonify({"error": "observer credential is read-only: this route is not available to it",
                            "principal": "observer"}), 403
        return None
    try:
        g.friday_principal = "user"
    except Exception:
        pass
    # Session-token and cross-site gate comes BEFORE loopback trust: trust says
    # who the machine is, not which page in the browser is speaking for it.
    _og_reason = _host_gate_reason() or _origin_gate_reason() or _frame_gate_reason()
    if _og_reason:
        from agent_friday.services.origin_gate import reason_code as _reason_code
        return jsonify({"error": _og_reason, "code": _reason_code(_og_reason)}), 403
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
    # FAIL-CLOSED: a NON-loopback request reached here, so the server is exposed
    # (e.g. via a Cloudflare Tunnel). If no remote auth key is configured we must
    # NOT wave it through — an unset key previously left the ENTIRE API open to
    # anyone who could reach the tunnel. Deny remote access until the operator
    # sets FRIDAY_REMOTE_KEY (or FRIDAY_PASSWORD). Loopback is unaffected.
    if not _HTTP_AUTH_KEY:
        if request.endpoint in ('login', 'serve_static_asset', 'serve_favicon'):
            return None
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"error": "remote access disabled: no FRIDAY_REMOTE_KEY set"}), 403
        return Response(
            "Remote access is disabled. Set FRIDAY_REMOTE_KEY on the server to "
            "enable authenticated remote access.",
            status=403, content_type="text/plain",
        )
    if request.endpoint in ('login', 'serve_static_asset', 'serve_favicon'):
        return None
    if request.path.startswith('/ws/'):
        return None  # WebSocket upgrade handled inside ws_live (can't send HTTP redirect)
    # Accept the ephemeral rotating token (embedded in the served HTML).
    if _api_token_valid(request.headers.get("X-Friday-Token")):
        return None
    if not session.get("authenticated"):
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login"))


# ═══════════════════════════════════════════════════════════════
#  DOCUMENT ISOLATION (response headers)
# ═══════════════════════════════════════════════════════════════
#
# Friday serves two kinds of markup. Its own pages hold the session token and
# may call every route. Everything else (a Studio creation, a published page, a
# file preview, a model-written document, an SVG) is somebody's or something's
# script, and must run in an opaque origin: no cookies, no storage, no reading
# Friday's responses. The rule is by exclusion: a route that returns markup is
# sandboxed unless it is named here, so a new route defaults to safe.

OWN_PAGE_ENDPOINTS = frozenset({
    "core_routes.serve_ui",
    "core_routes.serve_workspace_tab",
    "core_routes.serve_widget",
    "core_routes.serve_friday_live",
    "login",
})

_MARKUP_TYPES = frozenset({
    "text/html", "application/xhtml+xml", "image/svg+xml",
    "text/xml", "application/xml",
})

# What a sandboxed document may still do: run script, submit forms, open
# windows, show dialogs, lock the pointer, download. Never allow-same-origin
# (that would give it Friday's origin back) and never a top-navigation token.
SANDBOX_DEFAULT_TOKENS = (
    "allow-scripts", "allow-forms", "allow-popups", "allow-modals",
    "allow-pointer-lock", "allow-downloads",
)
_SANDBOX_NEVER = frozenset({
    "allow-same-origin", "allow-top-navigation",
    "allow-top-navigation-by-user-activation",
    "allow-top-navigation-to-custom-protocols",
    "allow-popups-to-escape-sandbox",
})

# Friday's own pages. `script-src` keeps inline script and eval because the UI
# is one inline bundle and MediaPipe hand tracking (opt-in, SRI-pinned, loaded
# from one CDN) compiles WebAssembly and uses eval. `frame-src` admits blob: and
# data: for the sandboxed previews the page builds itself.
#
# Nothing the page renders may fetch from outside: a reply, a fetched page or a
# tool result is untrusted, and `![](https://host/?d=<private text>)` would send
# that text to the host with no gate in the way. Images, media and fonts come
# from Friday's own origin, inline data or a blob: the page made itself, and
# connections go to Friday's own origin (HTTP, SSE and WebSocket) plus the one
# CDN hand tracking compiles from. Model servers and providers are reached by
# the server, never by the page. A remote picture the owner clicks to load is
# fetched by the server (/api/remote-image) and shown from a blob:.
OWN_PAGE_CSP = "; ".join((
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' blob: https://cdn.jsdelivr.net",
    "img-src 'self' data: blob:",
    "media-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' blob: data: https://cdn.jsdelivr.net",
    "worker-src 'self' blob:",
    "frame-src 'self' blob: data:",
    "frame-ancestors 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
))


def _split_csp(value):
    return [p.strip() for p in (value or "").split(";") if p.strip()]


# What a sandboxed document may load or send, whatever it asks for: its own
# server's files and inline data, never another host. A document that carries
# script is model-authored or fetched; a remote image or a fetch() is how it
# would carry private text out.
SANDBOX_NO_REMOTE_CSP = (
    ("img-src", ("'self'", "data:", "blob:")),
    ("media-src", ("'self'", "data:", "blob:")),
    ("connect-src", ("'none'",)),
    ("form-action", ("'none'",)),
)
_LOCAL_SOURCES = frozenset({"'self'", "data:", "blob:", "'none'"})


def _sandboxed_csp(existing):
    """`existing` with a `sandbox` directive that is never wider than either the
    existing one or Friday's default, and with image, media, connection and form
    sources that name no remote host."""
    out, seen, have = [], False, set()
    wanted = dict(SANDBOX_NO_REMOTE_CSP)
    for p in _split_csp(existing):
        bits = p.split()
        name = bits[0].lower() if bits else ""
        if name == "sandbox":
            seen = True
            kept = [t for t in bits[1:] if t.lower() not in _SANDBOX_NEVER]
            out.append(" ".join(["sandbox"] + kept))
        elif name in wanted:
            have.add(name)
            if name in ("connect-src", "form-action"):
                out.append(name + " 'none'")
            else:
                kept = [t for t in bits[1:] if t.lower() in _LOCAL_SOURCES]
                out.append(" ".join([name] + (kept or list(wanted[name]))))
        else:
            out.append(p)
    for name, tokens in SANDBOX_NO_REMOTE_CSP:
        if name not in have:
            out.append(" ".join((name,) + tokens))
    if not seen:
        out.insert(0, " ".join(("sandbox",) + SANDBOX_DEFAULT_TOKENS))
    return "; ".join(out)


def _isolation_headers(resp):
    """Attach the document-isolation headers to a response. Never raises."""
    try:
        ctype = (resp.mimetype or "").lower()
        if request.endpoint in OWN_PAGE_ENDPOINTS and ctype in _MARKUP_TYPES:
            if not resp.headers.get("Content-Security-Policy"):
                resp.headers["Content-Security-Policy"] = OWN_PAGE_CSP
            resp.headers["Cache-Control"] = "no-store"
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
            resp.headers.setdefault("Referrer-Policy", "no-referrer")
        elif ctype in _MARKUP_TYPES:
            resp.headers["Content-Security-Policy"] = _sandboxed_csp(
                resp.headers.get("Content-Security-Policy"))
            resp.headers["X-Content-Type-Options"] = "nosniff"
    except Exception:
        # A response that cannot be classified is not sent as markup.
        try:
            resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
            resp.headers["X-Content-Type-Options"] = "nosniff"
        except Exception:
            pass
    return resp


@app.after_request
def _apply_isolation_headers(resp):
    return _isolation_headers(resp)


@app.route('/api/session/token')
def session_token():
    """The current session token, for a page that stayed open past a rotation.

    Only Friday's own page can read it: the host gate in check_auth refuses a
    request that does not name Friday (a DNS-rebound page), the frame gate
    refuses a sandboxed or foreign document, and a cross-origin response is
    unreadable to script regardless."""
    resp = jsonify({"token": _current_api_token()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


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


def prune_context_logs():
    """Delete per-day context-log files older than context_retention_days.

    context_retention_days has claimed "0 = keep forever; 30/90/180/365 =
    prune older" (see its DEFAULT_SETTINGS comment) since it was added, with
    no code ever behind that claim -- the Retention Period setting in
    Settings > Privacy > Context Logging persisted and read back, but
    nothing ever deleted an old entry. Each day
    is one whole <YYYY-MM-DD>.jsonl file (see _context_log_files above), so
    pruning is file deletion, not row surgery.
    """
    try:
        days = int((_load_settings() or {}).get("context_retention_days", 0) or 0)
    except Exception:
        days = 0
    if days <= 0:
        return {"changed": False, "summary": "retention disabled (keep forever)"}
    if not CONTEXT_LOG_DIR.exists():
        return {"changed": False, "summary": "no context-log directory yet"}
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    removed = []
    for f in CONTEXT_LOG_DIR.glob("*.jsonl"):
        if f.stem < cutoff:
            try:
                f.unlink()
                removed.append(f.stem)
            except OSError:
                pass
    summary = (f"pruned {len(removed)} context-log day(s) older than {days}d"
               if removed else f"nothing older than {days}d retention")
    return {"changed": bool(removed), "count": len(removed), "summary": summary}

# ── Persistent Chat History ────────────────────────────────────
CHAT_HISTORY_FILE = FRIDAY_DIR / "chat_history.json"
# Flask runs threaded=True: two concurrent chat requests can each mutate and
# persist CHAT_HISTORY. Without a lock their appends clobber each other, and a
# plain write_text() (the prior implementation) that is interrupted by a crash
# or a full disk mid-write leaves a half-written, unparseable chat_history.json —
# on next boot _load_chat_history() silently returns [] and the user's history is
# gone. Serialize writes and make them atomic (temp + fsync + replace), matching
# the settings-write durability guarantee.
_CHAT_HISTORY_LOCK = threading.Lock()

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
    """Persist chat history to disk atomically under a lock (crash- and race-safe).

    Rows marked off_record stay in memory and are never written.
    """
    messages = [m for m in (messages or []) if not (isinstance(m, dict) and m.get('off_record'))]
    with _CHAT_HISTORY_LOCK:
        CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _tmp = CHAT_HISTORY_FILE.with_suffix('.json.tmp')
        _tmp.write_text(json.dumps(messages, indent=2), encoding='utf-8')
        try:
            with open(_tmp, 'rb') as _f:
                os.fsync(_f.fileno())
        except Exception:
            pass
        _tmp.replace(CHAT_HISTORY_FILE)

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

"""
mcp_oauth.py — OAuth 2.1 authorization for remote (Streamable HTTP) MCP servers.

Remote MCP servers (Notion, Linear, Atlassian, …) protect their endpoint with
OAuth 2.1 as specified by the MCP authorization spec (2025-06-18). This module
implements the full client side with nothing but the stdlib:

  * **Discovery** — RFC 9728 protected-resource metadata (from the 401's
    `WWW-Authenticate: … resource_metadata="…"` hint or the well-known path),
    then RFC 8414 authorization-server metadata with an OpenID-Connect
    fallback, then a legacy fallback that assumes /authorize + /token on the
    MCP server's own origin (2025-03-26 spec behavior).
  * **Dynamic client registration** — RFC 7591, registering Friday as a public
    client (`token_endpoint_auth_method: "none"`) so no pre-provisioned client
    id is ever needed.
  * **PKCE (S256) + state** — mandatory under OAuth 2.1.
  * **Resource indicators** — RFC 8707 `resource=` on both the authorization
    and token requests, per the 2025-06-18 MCP spec, so tokens are audience-
    bound to the exact server they were minted for.
  * **Loopback redirect** — a one-shot 127.0.0.1 listener receives the
    authorization code; the consent screen opens in the user's real browser.
  * **Encrypted persistence** — client registrations and tokens are written
    through services/credential_store (vault key → DPAPI → hardened plaintext,
    never silent) under ~/.friday/mcp_oauth/.

Flows run in a background thread so a server that needs authorization never
wedges boot: mcp_client marks it `needs_auth`, the UI/API calls
begin_authorization(), and the on_complete callback restarts the server once
tokens land. Nothing here ever logs, prints, or returns token material beyond
the access token handed to the transport.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from agent_friday.paths import friday_home

try:
    from agent_friday.core import FRIDAY_DIR as _FRIDAY_DIR
except Exception:  # pragma: no cover — standalone import (tests, tooling)
    _FRIDAY_DIR = friday_home()

# Tests monkeypatch this to a tmp dir; everything below reads it lazily.
OAUTH_DIR = _FRIDAY_DIR / "mcp_oauth"

_HTTP_TIMEOUT = 15.0            # metadata/registration/token requests
_DEFAULT_FLOW_TIMEOUT = 300.0   # how long we wait for the user in the browser
_EXPIRY_SLACK = 60.0            # refresh this many seconds before expiry

_USER_AGENT = "friday-mcp-oauth/1.0"


# ══════════════════════════════════════════════════════════════════════════
#  Small HTTP + parsing helpers
# ══════════════════════════════════════════════════════════════════════════

def _http_json(url: str, *, method: str = "GET", data: dict | None = None,
               form: bool = False, headers: dict | None = None,
               timeout: float = _HTTP_TIMEOUT) -> dict:
    """GET/POST a JSON (or form-encoded) request and parse the JSON reply.

    Raises urllib.error.HTTPError / URLError on failure; callers treat any
    exception as "this discovery step didn't pan out" and fall through.
    """
    body = None
    hdrs = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object from {url}")
    return parsed


def parse_www_authenticate(header: str | None) -> dict:
    """Extract key="value" (and bare key=value) params from a Bearer challenge."""
    out: dict[str, str] = {}
    if not header:
        return out
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^\s,]+))', header):
        out[m.group(1).lower()] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def canonical_resource(url: str) -> str:
    """Canonicalize an MCP server URL for use as the RFC 8707 resource value:
    lowercase scheme+host, default ports dropped, fragment dropped."""
    p = urllib.parse.urlsplit(url)
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    port = p.port
    netloc = host
    if port and not ((scheme == "https" and port == 443) or
                     (scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    path = p.path.rstrip("/") if p.path not in ("", "/") else ""
    return urllib.parse.urlunsplit((scheme, netloc, path or "", p.query, ""))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ══════════════════════════════════════════════════════════════════════════
#  Discovery — 401 challenge → resource metadata → AS metadata
# ══════════════════════════════════════════════════════════════════════════

def _well_known_candidates(base_url: str, suffix: str) -> list[str]:
    """RFC 8414 path-aware well-known URLs: for https://host/some/path try
    https://host/.well-known/<suffix>/some/path first, then the root form."""
    p = urllib.parse.urlsplit(base_url)
    origin = f"{p.scheme}://{p.netloc}"
    path = p.path.rstrip("/")
    urls = []
    if path:
        urls.append(f"{origin}/.well-known/{suffix}{path}")
    urls.append(f"{origin}/.well-known/{suffix}")
    return urls


def discover(server_url: str, www_authenticate: str | None = None) -> dict:
    """Resolve the authorization endpoints protecting `server_url`.

    Returns {issuer, authorization_endpoint, token_endpoint,
             registration_endpoint?, scopes: [..], resource}.
    Never raises — the legacy same-origin fallback always yields a result;
    if even the fallback endpoints are wrong the auth flow itself will fail
    with a clear error.
    """
    challenge = parse_www_authenticate(www_authenticate)
    resource = canonical_resource(server_url)
    scopes: list[str] = []
    if challenge.get("scope"):
        scopes = challenge["scope"].split()

    # 1. Protected-resource metadata (RFC 9728).
    prm = None
    prm_urls = []
    if challenge.get("resource_metadata"):
        prm_urls.append(challenge["resource_metadata"])
    prm_urls.extend(_well_known_candidates(server_url, "oauth-protected-resource"))
    for u in prm_urls:
        try:
            prm = _http_json(u)
            break
        except Exception:
            continue

    auth_server = None
    if prm:
        servers = prm.get("authorization_servers") or []
        if servers:
            auth_server = str(servers[0])
        if not scopes and isinstance(prm.get("scopes_supported"), list):
            scopes = [str(s) for s in prm["scopes_supported"]]

    # 2. Authorization-server metadata (RFC 8414, then OIDC discovery).
    as_base = auth_server or _origin(server_url)
    meta = None
    meta_urls = (_well_known_candidates(as_base, "oauth-authorization-server")
                 + _well_known_candidates(as_base, "openid-configuration"))
    for u in meta_urls:
        try:
            meta = _http_json(u)
            if meta.get("authorization_endpoint") and meta.get("token_endpoint"):
                break
            meta = None
        except Exception:
            continue

    if meta is None:
        # 3. Legacy fallback (2025-03-26 spec): default endpoints on the base.
        meta = {
            "issuer": as_base,
            "authorization_endpoint": as_base.rstrip("/") + "/authorize",
            "token_endpoint": as_base.rstrip("/") + "/token",
            "registration_endpoint": as_base.rstrip("/") + "/register",
        }

    return {
        "issuer": meta.get("issuer") or as_base,
        "authorization_endpoint": meta["authorization_endpoint"],
        "token_endpoint": meta["token_endpoint"],
        "registration_endpoint": meta.get("registration_endpoint"),
        "scopes": scopes,
        "resource": resource,
    }


def _origin(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


# ══════════════════════════════════════════════════════════════════════════
#  Encrypted per-server credential records
# ══════════════════════════════════════════════════════════════════════════
# One file per server: ~/.friday/mcp_oauth/<name>.oauth.enc holding
# {server_url, discovery{...}, client{client_id, client_secret?, redirect_uri},
#  tokens{access_token, refresh_token?, expires_at?, scope?}}

_RECORD_LOCK = threading.Lock()


def _record_path(server_name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", server_name)[:64]
    return Path(OAUTH_DIR) / f"{safe}.oauth.enc"


def _read_record(server_name: str) -> dict:
    p = _record_path(server_name)
    if not p.exists():
        return {}
    try:
        from agent_friday.services import credential_store as _cs
        raw = _cs.read_secret(p)
    except ImportError:  # pragma: no cover — store module missing entirely
        raw = p.read_bytes()
    except Exception:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_record(server_name: str, record: dict) -> None:
    p = _record_path(server_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(record).encode("utf-8")
    try:
        from agent_friday.services import credential_store as _cs
        _cs.write_secret(p, blob)
    except ImportError:  # pragma: no cover
        p.write_bytes(blob)


def clear_credentials(server_name: str) -> bool:
    """Forget everything we hold for a server (disconnect)."""
    with _RECORD_LOCK:
        p = _record_path(server_name)
        try:
            if p.exists():
                p.unlink()
                return True
        except Exception:
            pass
    return False


def has_credentials(server_name: str) -> bool:
    return bool(_read_record(server_name).get("tokens", {}).get("access_token"))


# ══════════════════════════════════════════════════════════════════════════
#  Token acquisition — cached → refresh → (interactive flow elsewhere)
# ══════════════════════════════════════════════════════════════════════════

_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_REFRESH_LOCKS_GUARD = threading.Lock()


def _refresh_lock(server_name: str) -> threading.Lock:
    with _REFRESH_LOCKS_GUARD:
        return _REFRESH_LOCKS.setdefault(server_name, threading.Lock())


def get_access_token(server_name: str) -> str | None:
    """Return a currently-valid access token for a server, refreshing behind a
    per-server lock when expired. Returns None when interactive authorization
    is required — this function NEVER opens a browser."""
    rec = _read_record(server_name)
    tokens = rec.get("tokens") or {}
    access = tokens.get("access_token")
    if not access:
        return None
    expires_at = tokens.get("expires_at")
    if not expires_at or (time.time() < float(expires_at) - _EXPIRY_SLACK):
        return access
    # Expired (or about to) — try the refresh grant once, serialized.
    with _refresh_lock(server_name):
        rec = _read_record(server_name)             # re-read under the lock
        tokens = rec.get("tokens") or {}
        expires_at = tokens.get("expires_at")
        if tokens.get("access_token") and (
                not expires_at or time.time() < float(expires_at) - _EXPIRY_SLACK):
            return tokens["access_token"]           # someone else refreshed
        return _refresh(server_name, rec)


def _refresh(server_name: str, rec: dict) -> str | None:
    tokens = rec.get("tokens") or {}
    refresh_token = tokens.get("refresh_token")  # pragma: allowlist secret
    disco = rec.get("discovery") or {}
    client = rec.get("client") or {}
    if not (refresh_token and disco.get("token_endpoint") and client.get("client_id")):
        return None
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client["client_id"],
    }
    if disco.get("resource"):
        form["resource"] = disco["resource"]
    if client.get("client_secret"):
        form["client_secret"] = client["client_secret"]
    try:
        reply = _http_json(disco["token_endpoint"], method="POST",
                           data=form, form=True)
    except Exception:
        return None
    if not reply.get("access_token"):
        return None
    _store_token_reply(server_name, rec, reply)
    return reply["access_token"]


def _store_token_reply(server_name: str, rec: dict, reply: dict) -> None:
    tokens = {
        "access_token": reply["access_token"],
        "token_type": reply.get("token_type", "Bearer"),
    }
    # A rotated refresh token replaces the old one; absence keeps the old one.
    prev = (rec.get("tokens") or {}).get("refresh_token")
    tokens["refresh_token"] = reply.get("refresh_token") or prev
    if reply.get("expires_in"):
        tokens["expires_at"] = time.time() + float(reply["expires_in"])
    if reply.get("scope"):
        tokens["scope"] = reply["scope"]
    rec["tokens"] = tokens
    with _RECORD_LOCK:
        _write_record(server_name, rec)


# ══════════════════════════════════════════════════════════════════════════
#  Loopback redirect listener
# ══════════════════════════════════════════════════════════════════════════

_CALLBACK_PAGE = ("<html><body style='font-family:sans-serif;background:#0b0f14;"
                  "color:#e6edf3;text-align:center;padding-top:14vh'>"
                  "<h2>{title}</h2><p>{body}</p>"
                  "<p style='opacity:.6'>You can close this tab and return to "
                  "Friday.</p></body></html>")


class _CallbackHandler(BaseHTTPRequestHandler):
    """One-shot handler that captures ?code=&state= from the redirect."""

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/callback":       # favicon etc. — not the redirect
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        result = {k: v[0] for k, v in params.items()}
        self.server.oauth_result = result  # type: ignore[attr-defined]
        ok = "code" in result
        page = _CALLBACK_PAGE.format(
            title="Friday is connected ✅" if ok else "Authorization failed",
            body="The connector was authorized successfully." if ok else
                 (result.get("error_description") or result.get("error")
                  or "No authorization code was returned."))
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):  # silence per-request stderr noise
        pass


def _listen_loopback(preferred_port: int | None) -> HTTPServer:
    """Bind the loopback listener, preferring the port a prior client
    registration was bound to (its registered redirect_uri must match)."""
    if preferred_port:
        try:
            return HTTPServer(("127.0.0.1", preferred_port), _CallbackHandler)
        except OSError:
            pass  # port taken — fall through to an ephemeral one
    return HTTPServer(("127.0.0.1", 0), _CallbackHandler)


# ══════════════════════════════════════════════════════════════════════════
#  The interactive authorization flow
# ══════════════════════════════════════════════════════════════════════════

class _Flow:
    """State of one in-flight browser authorization for a server."""

    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        self.state = "pending"           # pending | done | error
        self.auth_url: str | None = None
        self.error: str | None = None
        self.started_at = time.time()


_FLOWS: dict[str, _Flow] = {}
_FLOWS_LOCK = threading.Lock()


def authorization_status(server_name: str) -> dict:
    """Poll surface for the UI: none|pending|done|error (+ auth_url)."""
    with _FLOWS_LOCK:
        flow = _FLOWS.get(server_name)
    if flow is None:
        return {"state": "done" if has_credentials(server_name) else "none"}
    return {"state": flow.state, "auth_url": flow.auth_url, "error": flow.error}


def begin_authorization(
    server_name: str,
    server_url: str,
    *,
    www_authenticate: str | None = None,
    open_browser: bool = True,
    timeout: float = _DEFAULT_FLOW_TIMEOUT,
    on_complete: Callable[[str, bool], None] | None = None,
) -> dict:
    """Start (or return the already-running) browser authorization flow.

    Returns {ok, state, auth_url?} immediately; the flow itself runs in a
    daemon thread. on_complete(server_name, success) fires when it settles —
    mcp wiring uses it to restart the server so its tools register.
    """
    with _FLOWS_LOCK:
        existing = _FLOWS.get(server_name)
        if existing and existing.state == "pending":
            return {"ok": True, "state": "pending", "auth_url": existing.auth_url}
        flow = _Flow(server_name)
        _FLOWS[server_name] = flow

    url_ready = threading.Event()

    def _run() -> None:
        try:
            _run_flow(flow, server_name, server_url, www_authenticate,
                      open_browser, timeout, url_ready)
            success = flow.state == "done"
        except Exception as e:  # noqa: BLE001 — flow must never raise upward
            flow.state = "error"
            flow.error = str(e)
            success = False
        finally:
            url_ready.set()
        if on_complete:
            try:
                on_complete(server_name, success)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True,
                     name=f"mcp-oauth-{server_name}").start()
    # Give the thread a beat to mint the auth URL so the API reply carries it.
    url_ready.wait(timeout=_HTTP_TIMEOUT * 3)
    return {"ok": flow.state != "error", "state": flow.state,
            "auth_url": flow.auth_url, "error": flow.error}


def _run_flow(flow: _Flow, server_name: str, server_url: str,
              www_authenticate: str | None, open_browser: bool,
              timeout: float, url_ready: threading.Event) -> None:
    rec = _read_record(server_name)
    rec["server_url"] = server_url

    # 1. Discovery (reuse a cached copy only if it matches this server URL).
    disco = rec.get("discovery")
    if not disco or disco.get("resource") != canonical_resource(server_url):
        disco = discover(server_url, www_authenticate)
        rec["discovery"] = disco

    # 2. Loopback listener — bind before registering so the registered
    #    redirect_uri carries the real port.
    client = rec.get("client") or {}
    preferred_port = None
    if client.get("redirect_uri"):
        try:
            preferred_port = urllib.parse.urlsplit(client["redirect_uri"]).port
        except Exception:
            preferred_port = None
    httpd = _listen_loopback(preferred_port)
    port = httpd.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    try:
        # 3. Dynamic client registration (once per server, re-done if the
        #    loopback port had to change from what was registered).
        if not client.get("client_id") or client.get("redirect_uri") != redirect_uri:
            client = _register_client(disco, redirect_uri, existing=client)
            rec["client"] = client
            with _RECORD_LOCK:
                _write_record(server_name, rec)

        # 4. Authorization request (PKCE + state + resource).
        verifier, challenge = _pkce_pair()
        state = _b64url(secrets.token_bytes(24))
        params = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": disco["resource"],
        }
        if disco.get("scopes"):
            params["scope"] = " ".join(disco["scopes"])
        flow.auth_url = (disco["authorization_endpoint"]
                         + ("&" if "?" in disco["authorization_endpoint"] else "?")
                         + urllib.parse.urlencode(params))
        url_ready.set()
        if open_browser:
            try:
                webbrowser.open(flow.auth_url)
            except Exception:
                pass  # headless — the auth_url is still surfaced via the API

        # 5. Wait for exactly one redirect hit (or time out).
        httpd.timeout = 1.0
        deadline = time.time() + timeout
        result: dict | None = None
        while time.time() < deadline:
            httpd.handle_request()  # returns after 1s tick or one request
            result = getattr(httpd, "oauth_result", None)
            if result is not None:
                break
        if result is None:
            raise TimeoutError("authorization timed out — browser consent "
                               "was never completed")
        if result.get("error"):
            raise RuntimeError(result.get("error_description")
                               or result["error"])
        if result.get("state") != state:
            raise RuntimeError("state mismatch on OAuth callback")
        code = result.get("code")
        if not code:
            raise RuntimeError("no authorization code in callback")
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass

    # 6. Code → token exchange.
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client["client_id"],
        "code_verifier": verifier,
        "resource": disco["resource"],
    }
    if client.get("client_secret"):
        form["client_secret"] = client["client_secret"]
    try:
        reply = _http_json(disco["token_endpoint"], method="POST",
                           data=form, form=True)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"token exchange failed: HTTP {e.code} {detail}")
    if not reply.get("access_token"):
        raise RuntimeError("token endpoint returned no access token")

    _store_token_reply(server_name, rec, reply)
    flow.state = "done"


def _register_client(disco: dict, redirect_uri: str, existing: dict) -> dict:
    """RFC 7591 dynamic registration as a public client. When the AS offers no
    registration endpoint, fall back to any client_id already on record (a
    manually-provisioned one) — otherwise fail with a clear message."""
    reg_ep = disco.get("registration_endpoint")
    if not reg_ep:
        if existing.get("client_id"):
            return {**existing, "redirect_uri": redirect_uri}
        raise RuntimeError(
            "authorization server offers no dynamic registration; provision a "
            "client id manually and store it via /api/mcp/servers")
    reply = _http_json(reg_ep, method="POST", data={
        "client_name": "Agent Friday",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    if not reply.get("client_id"):
        raise RuntimeError("dynamic client registration returned no client_id")
    client = {"client_id": reply["client_id"], "redirect_uri": redirect_uri}
    if reply.get("client_secret"):
        client["client_secret"] = reply["client_secret"]
    return client

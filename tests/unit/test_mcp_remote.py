"""Tests for the remote MCP transport (MCPServerHTTP) and OAuth 2.1 client.

Everything runs against an in-process loopback stub that plays BOTH roles on a
single port: a Streamable HTTP MCP server (JSON and SSE reply modes, session
tracking, optional bearer-auth gate) and the OAuth authorization server
protecting it (RFC 9728 resource metadata, RFC 8414 AS metadata, RFC 7591
dynamic registration, PKCE-verified token endpoint). No network, no browser —
the "browser" is a urllib request that follows the authorize redirect to the
flow's loopback listener.
"""
import hashlib
import io
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from base64 import urlsafe_b64encode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday import mcp_oauth  # noqa: E402
from agent_friday.mcp_client import (  # noqa: E402
    MCPManager,
    MCPServerHTTP,
    MCPServerProcess,
    iter_sse_data,
)
from agent_friday.services import extension_security  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
#  The combined MCP + OAuth stub
# ══════════════════════════════════════════════════════════════════════════

class RemoteStub:
    """Streamable HTTP MCP server + OAuth AS on one 127.0.0.1 port."""

    def __init__(self, *, require_auth=False, sse_mode=False):
        self.require_auth = require_auth
        self.sse_mode = sse_mode
        self.issued_tokens: set[str] = set()
        self.sessions: set[str] = set()
        self.codes: dict[str, dict] = {}       # auth code -> {challenge, ...}
        self.authorize_params: dict | None = None
        self.register_body: dict | None = None
        self.token_requests: list[dict] = []
        self.seen_bearer: list[str] = []
        self._lock = threading.Lock()
        self._n = 0

        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            # ── helpers ──────────────────────────────────────────────────
            def _json(self, obj, code=200, headers=None):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            # ── OAuth surface ────────────────────────────────────────────
            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path == "/.well-known/oauth-protected-resource":
                    return self._json({
                        "resource": stub.url,
                        "authorization_servers": [stub.base + "/as"],
                        "scopes_supported": ["mcp.read"],
                    })
                if parsed.path == "/.well-known/oauth-authorization-server/as":
                    return self._json({
                        "issuer": stub.base + "/as",
                        "authorization_endpoint": stub.base + "/as/authorize",
                        "token_endpoint": stub.base + "/as/token",
                        "registration_endpoint": stub.base + "/as/register",
                        "code_challenge_methods_supported": ["S256"],
                    })
                if parsed.path == "/as/authorize":
                    q = {k: v[0] for k, v in
                         urllib.parse.parse_qs(parsed.query).items()}
                    stub.authorize_params = q
                    code = f"code-{stub.next_n()}"
                    stub.codes[code] = q
                    loc = (q["redirect_uri"] + "?" + urllib.parse.urlencode(
                        {"code": code, "state": q.get("state", "")}))
                    self.send_response(302)
                    self.send_header("Location", loc)
                    self.end_headers()
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path == "/as/register":
                    stub.register_body = json.loads(self._body() or b"{}")
                    return self._json({"client_id": "dcr-client-1"}, 201)
                if parsed.path == "/as/token":
                    form = {k: v[0] for k, v in urllib.parse.parse_qs(
                        self._body().decode()).items()}
                    stub.token_requests.append(form)
                    return self._token(form)
                if parsed.path == "/mcp":
                    return self._mcp()
                self.send_response(404)
                self.end_headers()

            def do_DELETE(self):
                self.send_response(200)
                self.end_headers()

            def _token(self, form):
                grant = form.get("grant_type")
                if grant == "authorization_code":
                    stored = stub.codes.pop(form.get("code", ""), None)
                    if stored is None:
                        return self._json({"error": "invalid_grant"}, 400)
                    digest = hashlib.sha256(
                        form.get("code_verifier", "").encode()).digest()
                    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
                    if challenge != stored.get("code_challenge"):
                        return self._json({"error": "invalid_grant",
                                           "error_description": "pkce"}, 400)
                elif grant != "refresh_token":
                    return self._json({"error": "unsupported_grant_type"}, 400)
                access = f"at-{stub.next_n()}"          # pragma: allowlist secret
                stub.issued_tokens.add(access)
                return self._json({
                    "access_token": access,             # pragma: allowlist secret
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "rt-1",            # pragma: allowlist secret
                })

            # ── MCP surface ──────────────────────────────────────────────
            def _mcp(self):
                # Drain the request body BEFORE any early response. Answering
                # 401 with unread bytes in the socket buffer makes close()
                # emit a TCP RST on Windows, which can destroy the response in
                # the client's receive buffer — the client then sees a
                # connection reset instead of the 401 (a load-sensitive flake).
                body_raw = self._body()
                if stub.require_auth:
                    auth = self.headers.get("Authorization") or ""
                    bearer = auth[7:] if auth.startswith("Bearer ") else ""
                    if bearer not in stub.issued_tokens:
                        challenge = (
                            f'Bearer resource_metadata='
                            f'"{stub.base}/.well-known/oauth-protected-resource"')
                        self.send_response(401)
                        self.send_header("WWW-Authenticate", challenge)
                        self.end_headers()
                        return
                    stub.seen_bearer.append(bearer)
                msg = json.loads(body_raw or b"{}")
                if "id" not in msg:                     # notification
                    self.send_response(202)
                    self.end_headers()
                    return
                method = msg.get("method")
                if method == "initialize":
                    sid = f"sess-{stub.next_n()}"
                    stub.sessions.add(sid)
                    return self._json(self._reply(msg, {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "remote-stub", "version": "1"},
                    }), headers={"Mcp-Session-Id": sid})
                if self.headers.get("Mcp-Session-Id") not in stub.sessions:
                    self.send_response(404)
                    self.end_headers()
                    return
                if method == "tools/list":
                    result = {"tools": [{
                        "name": "echo", "description": "echo text back",
                        "inputSchema": {"type": "object", "properties": {
                            "text": {"type": "string"}}},
                    }]}
                elif method == "tools/call":
                    text = (msg["params"].get("arguments") or {}).get("text", "")
                    result = {"content": [{"type": "text",
                                           "text": f"echo: {text}"}]}
                else:
                    return self._json({"jsonrpc": "2.0", "id": msg["id"],
                                       "error": {"code": -32601,
                                                 "message": "no such method"}})
                reply = self._reply(msg, result)
                if stub.sse_mode:
                    return self._sse(reply)
                return self._json(reply)

            @staticmethod
            def _reply(msg, result):
                return {"jsonrpc": "2.0", "id": msg["id"], "result": result}

            def _sse(self, reply):
                notif = {"jsonrpc": "2.0",
                         "method": "notifications/progress", "params": {}}
                body = (": keepalive comment\n\n"
                        f"data: {json.dumps(notif)}\n\n"
                        "event: message\n"
                        f"data: {json.dumps(reply)}\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.url = self.base + "/mcp"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def next_n(self):
        with self._lock:
            self._n += 1
            return self._n

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def oauth_dir(tmp_path, monkeypatch):
    d = tmp_path / "mcp_oauth"
    monkeypatch.setattr(mcp_oauth, "OAUTH_DIR", d)
    return d


def _make_stub(request, **kw):
    stub = RemoteStub(**kw)
    request.addfinalizer(stub.close)
    return stub


def _wait(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _manager_for(stub):
    mgr = MCPManager()
    mgr.load_config({"servers": {"remote": {"url": stub.url}}})
    return mgr


# ══════════════════════════════════════════════════════════════════════════
#  Pure helpers
# ══════════════════════════════════════════════════════════════════════════

def test_iter_sse_data_parses_events():
    raw = (b": comment\n"
           b"event: message\n"
           b"data: {\"a\": 1}\n\n"
           b"data: line1\n"
           b"data: line2\n\n"
           b"data: tail-no-blank\n")
    events = list(iter_sse_data(io.BytesIO(raw), time.time() + 5))
    assert events == ['{"a": 1}', "line1\nline2", "tail-no-blank"]


def test_canonical_resource():
    assert mcp_oauth.canonical_resource("HTTPS://MCP.Example.com:443/x/") == \
        "https://mcp.example.com/x"
    assert mcp_oauth.canonical_resource("http://host:8080/mcp#frag") == \
        "http://host:8080/mcp"
    assert mcp_oauth.canonical_resource("https://host/") == "https://host"


def test_parse_www_authenticate():
    parsed = mcp_oauth.parse_www_authenticate(
        'Bearer realm="mcp", resource_metadata="https://x/.well-known/r", '
        'scope="a b", error=invalid_token')
    assert parsed["resource_metadata"] == "https://x/.well-known/r"
    assert parsed["scope"] == "a b"
    assert parsed["error"] == "invalid_token"
    assert mcp_oauth.parse_www_authenticate(None) == {}


def test_load_config_detects_transport():
    mgr = MCPManager()
    mgr.load_config({"servers": {
        "local": {"command": "node", "args": ["x.js"]},
        "cloud": {"url": "https://mcp.example.com/mcp"},
        "cloud_off": {"url": "https://mcp.example.com/mcp", "enabled": False},
    }})
    assert isinstance(mgr.servers["local"], MCPServerProcess)
    assert isinstance(mgr.servers["cloud"], MCPServerHTTP)
    assert mgr.servers["cloud_off"].status == "disabled"
    assert mgr.servers["cloud"].info()["transport"] == "http"
    assert mgr.servers["local"].info()["transport"] == "stdio"


# ══════════════════════════════════════════════════════════════════════════
#  Transport — handshake, calls, sessions
# ══════════════════════════════════════════════════════════════════════════

def test_remote_handshake_and_call_json(request, oauth_dir):
    stub = _make_stub(request)
    mgr = _manager_for(stub)
    mgr.start_all()
    assert _wait(lambda: mgr.status()["remote"]["status"] == "ready")
    st = mgr.status()["remote"]
    assert st["tools"] == ["echo"]
    assert st["server_info"]["name"] == "remote-stub"
    assert mgr.call("remote", "echo", {"text": "hi"}) == "echo: hi"


def test_remote_handshake_and_call_sse(request, oauth_dir):
    stub = _make_stub(request, sse_mode=True)
    mgr = _manager_for(stub)
    mgr.start_all()
    assert _wait(lambda: mgr.status()["remote"]["status"] == "ready")
    # The SSE stream interleaves a notification before the response.
    assert mgr.call("remote", "echo", {"text": "streamed"}) == "echo: streamed"


def test_session_expiry_reinitializes(request, oauth_dir):
    stub = _make_stub(request)
    mgr = _manager_for(stub)
    mgr.start_all()
    assert _wait(lambda: mgr.status()["remote"]["status"] == "ready")
    stub.sessions.clear()          # server side forgets us → 404 on next call
    assert mgr.call("remote", "echo", {"text": "back"}) == "echo: back"
    assert len(stub.sessions) == 1  # a fresh session was negotiated


def test_remote_401_parks_in_needs_auth(request, oauth_dir):
    stub = _make_stub(request, require_auth=True)
    mgr = _manager_for(stub)
    mgr.start_all()
    assert _wait(lambda: mgr.status()["remote"]["status"] == "needs_auth"), \
        mgr.status()["remote"]
    sp = mgr.servers["remote"]
    assert "resource_metadata" in (sp.auth_challenge or "")
    out = mgr.call("remote", "echo", {"text": "x"})
    assert "unavailable" in out


# ══════════════════════════════════════════════════════════════════════════
#  OAuth 2.1 — end-to-end flow, token reuse, refresh
# ══════════════════════════════════════════════════════════════════════════

def test_oauth_end_to_end(request, oauth_dir):
    stub = _make_stub(request, require_auth=True)
    mgr = _manager_for(stub)
    mgr.start_all()
    assert _wait(lambda: mgr.status()["remote"]["status"] == "needs_auth"), \
        mgr.status()["remote"]

    registered = {}
    result = mgr.authorize(
        "remote", open_browser=False,
        on_ready=lambda n, tools: registered.update({n: [t["name"] for t in tools]}))
    assert result["ok"], result
    assert result["auth_url"], "flow must surface the consent URL"

    # Simulate the browser: follow the authorize redirect to the loopback.
    with urllib.request.urlopen(result["auth_url"], timeout=10) as resp:
        assert b"connected" in resp.read().lower()

    assert _wait(lambda: mgr.status()["remote"]["status"] == "ready")
    assert _wait(lambda: registered.get("remote") == ["echo"])
    assert mgr.call("remote", "echo", {"text": "authed"}) == "echo: authed"

    # Spec compliance checks recorded by the stub.
    assert stub.register_body["token_endpoint_auth_method"] == "none"
    assert stub.authorize_params["code_challenge_method"] == "S256"
    assert stub.authorize_params["client_id"] == "dcr-client-1"
    assert stub.authorize_params["resource"] == mcp_oauth.canonical_resource(stub.url)
    assert stub.authorize_params["scope"] == "mcp.read"
    exchange = [t for t in stub.token_requests
                if t.get("grant_type") == "authorization_code"]
    assert exchange and exchange[0]["resource"] == mcp_oauth.canonical_resource(stub.url)
    assert stub.seen_bearer, "tool calls must carry the Bearer token"
    assert mcp_oauth.has_credentials("remote")
    assert mgr.auth_status("remote")["state"] == "done"


def test_stored_token_skips_flow(request, oauth_dir):
    stub = _make_stub(request, require_auth=True)
    stub.issued_tokens.add("seeded-token")   # pragma: allowlist secret
    mcp_oauth._write_record("remote", {
        "server_url": stub.url,
        "tokens": {"access_token": "seeded-token",   # pragma: allowlist secret
                   "expires_at": time.time() + 3600},
    })
    mgr = _manager_for(stub)
    mgr.start_all()
    assert _wait(lambda: mgr.status()["remote"]["status"] == "ready")
    assert mgr.call("remote", "echo", {"text": "warm"}) == "echo: warm"
    assert not stub.token_requests       # no OAuth round-trips happened


def test_refresh_grant_on_expiry(request, oauth_dir):
    stub = _make_stub(request, require_auth=True)
    mcp_oauth._write_record("remote", {
        "server_url": stub.url,
        "discovery": {"token_endpoint": stub.base + "/as/token",
                      "resource": mcp_oauth.canonical_resource(stub.url)},
        "client": {"client_id": "dcr-client-1"},
        "tokens": {"access_token": "stale",          # pragma: allowlist secret
                   "refresh_token": "rt-1",          # pragma: allowlist secret
                   "expires_at": time.time() - 10},
    })
    fresh = mcp_oauth.get_access_token("remote")
    assert fresh and fresh != "stale"
    assert fresh in stub.issued_tokens
    assert stub.token_requests[-1]["grant_type"] == "refresh_token"
    # The refreshed token was persisted for the next caller.
    assert mcp_oauth.get_access_token("remote") == fresh
    assert len([t for t in stub.token_requests
                if t["grant_type"] == "refresh_token"]) == 1


# ══════════════════════════════════════════════════════════════════════════
#  Extension security — plaintext-HTTP remote URLs are blocked
# ══════════════════════════════════════════════════════════════════════════

def test_extension_security_blocks_plain_http_remote():
    bad = extension_security.assess_server(
        "x", {"url": "http://mcp.example.com/mcp"})
    assert bad["verdict"] == "block"
    ok = extension_security.assess_server(
        "x", {"url": "https://mcp.example.com/mcp"})
    assert ok["verdict"] == "allow"
    local = extension_security.assess_server(
        "x", {"url": "http://127.0.0.1:8123/mcp"})
    assert local["verdict"] == "allow"

    gated = extension_security.gate_mcp_config({"servers": {
        "insecure": {"url": "http://mcp.example.com/mcp", "enabled": True},
    }})
    assert gated["servers"]["insecure"]["enabled"] is False

"""
mcp_client.py — A lightweight, dependency-free MCP (Model Context Protocol)
client for Friday.

Friday speaks the same protocol Claude Desktop and Claude Code use to talk to
MCP "connectors", over both transports the ecosystem uses:

  * **stdio** — a local subprocess exchanging newline-delimited JSON-RPC 2.0
    over stdin/stdout (MCPServerProcess). Config entries with a `command`.
  * **Streamable HTTP** — a remote server reached by POSTing JSON-RPC to a
    single URL, replies arriving as plain JSON or an SSE stream, with
    `Mcp-Session-Id` session tracking and OAuth 2.1 bearer auth handled by
    mcp_oauth.py (MCPServerHTTP). Config entries with a `url`. This is what
    the hosted connector ecosystem (Notion, Linear, Atlassian, …) speaks.
    (The pre-2025 "HTTP+SSE" dual-endpoint transport is not supported.)

Either way the module performs the `initialize` → `tools/list` handshake and
forwards `tools/call` requests on demand, handing discovered tools back to
server.py for registration into Friday's unified tool registry.

Design goals:
  * Pure stdlib (subprocess / threading / json / urllib) — the Python `mcp`
    SDK is not a hard dependency, so this runs on a vanilla install.
  * Thread-safe. Flask runs threaded=True, so several agent turns may call the
    same MCP server concurrently; JSON-RPC ids correlate request↔response and a
    per-process reader thread dispatches replies.
  * Non-blocking startup. start_all() launches each server in its own thread and
    fires an on_ready callback when (and if) the handshake completes. A server
    that never comes up just stays in the "error" state — it never wedges boot.
    A remote server answering 401 parks in "needs_auth" until the user approves
    it in a browser (see MCPManager.authorize).
  * Crash-resilient. A call against a dead process triggers a single restart
    attempt before failing; an expired HTTP session re-initializes once.

This is intentionally a *client*. It does not implement an MCP server.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Callable

# Spawn child processes without flashing a console window on Windows.
_CREATE_FLAGS = 0
if sys.platform == "win32":
    _CREATE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# MCP protocol revision we advertise in `initialize`. Servers negotiate down if
# they only speak an older revision; this is just our preferred version.
_PROTOCOL_VERSION = "2024-11-05"
# Remote servers get the current revision — it's the one the Streamable HTTP
# transport (and its MCP-Protocol-Version header) was specified in.
_HTTP_PROTOCOL_VERSION = "2025-06-18"

_DEFAULT_START_TIMEOUT = 30.0   # seconds to wait for initialize + tools/list
_DEFAULT_CALL_TIMEOUT = 120.0   # seconds to wait for a single tools/call reply


class _Pending:
    """A single outstanding JSON-RPC request awaiting its response."""

    __slots__ = ("event", "result", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: dict | None = None


class MCPServerProcess:
    """One MCP server subprocess and its stdio JSON-RPC channel."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self._log = log or (lambda _m: None)

        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []           # raw MCP tool dicts (inputSchema form)
        self.status = "stopped"               # stopped|starting|ready|error|crashed
        self.error: str | None = None
        self.server_info: dict = {}

        self._id = 0
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._lifecycle_lock = threading.Lock()  # serialize start/stop/restart
        self._stopping = False                    # suppress crash-relabel on stop

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _resolve_command(self) -> str:
        """Resolve `node`/`npx`/`python` to an absolute path so Windows .cmd
        shims (npx.cmd, etc.) launch without shell=True."""
        resolved = shutil.which(self.command)
        return resolved or self.command

    def _spawn(self) -> None:
        full_env = os.environ.copy()
        # self.env holds connector credentials ENCRYPTED (see
        # services/connector_secrets) so they are ciphertext everywhere they
        # can be observed — on disk, in this object, and in the raw-config
        # route. This is the one place they have to be real: the child process
        # reads them from its environment. A value that will not decrypt raises
        # here, where the cause is legible, rather than being handed to the
        # server as if it were the token.
        env = self.env
        try:
            from agent_friday.services import connector_secrets as _cse
            env = _cse.decrypt_env(env)
        except Exception as e:
            self._log(f"[mcp] {self.name}: credential could not be decrypted "
                      f"({e}) — reconnect this connector to re-enter it")
            raise
        full_env.update({k: str(v) for k, v in env.items()})
        cmd = [self._resolve_command(), *self.args]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            cwd=self.cwd or None,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,                       # line-buffered
            creationflags=_CREATE_FLAGS,
        )
        self._reader = threading.Thread(
            target=self._read_loop, name=f"mcp-{self.name}-reader", daemon=True
        )
        self._reader.start()
        threading.Thread(
            target=self._drain_stderr, name=f"mcp-{self.name}-stderr", daemon=True
        ).start()

    def start(self, timeout: float = _DEFAULT_START_TIMEOUT) -> bool:
        """Launch the process and run the MCP handshake. Returns True when the
        server is ready (tools discovered). Safe to call repeatedly."""
        with self._lifecycle_lock:
            if self.status == "ready" and self._alive():
                return True
            self._stopping = False
            self.status = "starting"
            self.error = None
            try:
                self._spawn()
            except Exception as e:  # noqa: BLE001 — surface any spawn failure
                self.status = "error"
                self.error = f"spawn failed: {e}"
                self._log(f"[mcp:{self.name}] spawn failed: {e}")
                return False

            try:
                init = self._request(
                    "initialize",
                    {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "friday", "version": "1.0"},
                    },
                    timeout=timeout,
                )
                self.server_info = (init or {}).get("serverInfo", {}) or {}
                # Per spec, follow the initialize result with this notification.
                self._notify("notifications/initialized", {})
                listed = self._request("tools/list", {}, timeout=timeout)
                self.tools = list((listed or {}).get("tools", []) or [])
                self.status = "ready"
                self._log(
                    f"[mcp:{self.name}] ready — {len(self.tools)} tool(s): "
                    + ", ".join(t.get("name", "?") for t in self.tools)
                )
                return True
            except Exception as e:  # noqa: BLE001
                self.status = "error"
                tail = " | ".join(list(self._stderr_tail)[-3:])
                self.error = f"{e}" + (f" (stderr: {tail})" if tail else "")
                self._log(f"[mcp:{self.name}] handshake failed: {self.error}")
                self.stop()
                return False

    def stop(self) -> None:
        self._stopping = True
        proc = self.proc
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception:
            pass
        finally:
            self.proc = None
            if self.status == "ready":
                self.status = "stopped"
            # Fail any in-flight requests.
            with self._pending_lock:
                pend = list(self._pending.values())
                self._pending.clear()
            for p in pend:
                p.error = {"message": "server stopped"}
                p.event.set()

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ── JSON-RPC plumbing ──────────────────────────────────────────────────────

    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    def _send(self, obj: dict) -> None:
        if not self._alive():
            raise RuntimeError("server process not running")
        line = json.dumps(obj, default=str) + "\n"
        with self._write_lock:
            assert self.proc and self.proc.stdin
            self.proc.stdin.write(line)
            self.proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict, timeout: float) -> Any:
        rid = self._next_id()
        pending = _Pending()
        with self._pending_lock:
            self._pending[rid] = pending
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not pending.event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"{method} timed out after {timeout}s")
        if pending.error is not None:
            raise RuntimeError(
                f"{method} error: {pending.error.get('message', pending.error)}"
            )
        return pending.result

    def _read_loop(self) -> None:
        """Drain stdout, parse JSON-RPC frames, and wake the matching waiter."""
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    # Some servers print non-JSON banners to stdout; ignore.
                    continue
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("id")
                if rid is None:
                    # A request/notification *from* the server. We don't expose
                    # sampling/roots, so nothing to do.
                    continue
                with self._pending_lock:
                    pending = self._pending.pop(rid, None)
                if pending is None:
                    continue
                if "error" in msg and msg["error"] is not None:
                    pending.error = msg["error"]
                else:
                    pending.result = msg.get("result")
                pending.event.set()
        except Exception:
            pass
        finally:
            # stdout closed → process is going away. Mark crashed only if this
            # wasn't a deliberate stop() (else it's just normal teardown).
            if self.status == "ready" and not self._stopping:
                self.status = "crashed"
                self._log(f"[mcp:{self.name}] stdout closed — marked crashed")

    def _drain_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                line = raw.rstrip()
                if line:
                    self._stderr_tail.append(line)
        except Exception:
            pass

    # ── tool invocation ────────────────────────────────────────────────────────

    def call_tool(
        self, tool_name: str, arguments: dict, timeout: float = _DEFAULT_CALL_TIMEOUT
    ) -> str:
        """Invoke a tool and return its text result. Attempts one restart if the
        process has died since the last call."""
        if not self._alive():
            self._log(f"[mcp:{self.name}] process dead — restarting before call")
            if not self.start():
                return f"[mcp:{self.name} unavailable] {self.error or 'server not running'}"
        try:
            result = self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments or {}},
                timeout=timeout,
            )
        except Exception as e:  # noqa: BLE001
            return f"[mcp:{self.name} error] {e}"
        return _flatten_tool_result(result)

    def info(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "alive": self._alive(),
            "error": self.error,
            "transport": "stdio",
            "command": " ".join([self.command, *self.args]),
            "tool_count": len(self.tools),
            "tools": [t.get("name", "?") for t in self.tools],
            "server_info": self.server_info,
        }


def _flatten_tool_result(result: Any) -> str:
    """Turn an MCP tools/call result into a plain string for the agent loop.

    The MCP shape is {"content": [{"type": "text", "text": ...}, ...],
    "isError": bool}. Text blocks are concatenated; structured blocks are
    JSON-dumped. An isError result is prefixed so the model knows it failed.
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return json.dumps(result, default=str)

    content = result.get("content")
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype in ("resource", "resource_link"):
                res = block.get("resource", block)
                parts.append(json.dumps(res, default=str))
            else:
                parts.append(json.dumps(block, default=str))
    else:
        parts.append(json.dumps(result, default=str))

    text = "\n".join(p for p in parts if p)
    if result.get("isError"):
        text = f"[tool error] {text}"
    # MCP servers can dump huge payloads (whole inboxes); cap to protect context.
    return text[:100_000]


class _AuthRequired(Exception):
    """Remote server answered 401 — OAuth authorization is needed."""

    def __init__(self, challenge: str | None) -> None:
        super().__init__("authorization required")
        self.challenge = challenge


class _SessionExpired(Exception):
    """Remote server answered 404 for our Mcp-Session-Id — re-initialize."""


def iter_sse_data(fp, deadline: float):
    """Yield the `data:` payload of each SSE event read from a stream.

    Multi-line data fields are joined with \n per the SSE spec; comment lines
    (`:` prefix) and other fields (event/id/retry) are skipped. Stops at EOF
    or when `deadline` (epoch seconds) passes.
    """
    buf: list[str] = []
    while time.time() < deadline:
        raw = fp.readline()
        if not raw:                      # EOF — server closed the stream
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":                   # blank line — event boundary
            if buf:
                yield "\n".join(buf)
                buf = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buf.append(line[5:].lstrip(" "))
    if buf:                              # stream closed mid-event — flush
        yield "\n".join(buf)


class MCPServerHTTP:
    """One remote MCP server reached over the Streamable HTTP transport.

    Mirrors MCPServerProcess's surface (start/stop/call_tool/info + .tools/
    .status/.error) so MCPManager treats both transports identically. Adds the
    auth states "needs_auth" (server wants OAuth, no flow running) and the
    begin_auth() hook that drives mcp_oauth's browser flow.
    """

    def __init__(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self._log = log or (lambda _m: None)

        self.tools: list[dict] = []
        self.status = "stopped"     # stopped|starting|ready|error|needs_auth|disabled
        self.error: str | None = None
        self.server_info: dict = {}

        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.auth_challenge: str | None = None   # WWW-Authenticate from last 401

        self._id = 0
        self._id_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()

    # ── auth plumbing ────────────────────────────────────────────────────────

    def _bearer(self) -> str | None:
        """Current access token (refreshed if needed) from mcp_oauth; None when
        the server is unauthenticated or public."""
        try:
            from agent_friday import mcp_oauth
            return mcp_oauth.get_access_token(self.name)
        except Exception:
            return None

    def begin_auth(self, *, open_browser: bool = True,
                   on_complete: Callable[[str, bool], None] | None = None) -> dict:
        """Kick off the OAuth 2.1 browser flow for this server."""
        from agent_friday import mcp_oauth
        return mcp_oauth.begin_authorization(
            self.name, self.url,
            www_authenticate=self.auth_challenge,
            open_browser=open_browser,
            on_complete=on_complete,
        )

    # ── HTTP + JSON-RPC plumbing ─────────────────────────────────────────────

    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    def _post(self, payload: dict, timeout: float, want_id: int | None):
        """POST one JSON-RPC message. Returns the matching response dict, or
        None for accepted notifications. Raises _AuthRequired / _SessionExpired
        for the two recoverable HTTP conditions."""
        body = json.dumps(payload, default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "friday-mcp/1.0",
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        bearer = self._bearer()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        headers.update(self.headers)

        req = urllib.request.Request(self.url, data=body, headers=headers,
                                     method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.auth_challenge = e.headers.get("WWW-Authenticate")
                raise _AuthRequired(self.auth_challenge) from None
            if e.code == 404 and self.session_id:
                raise _SessionExpired() from None
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {e.code} from {self.name}: "
                               f"{detail or e.reason}") from None

        with resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if resp.status == 202 or want_id is None:
                return None
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in ctype:
                deadline = time.time() + timeout
                for data in iter_sse_data(resp, deadline):
                    try:
                        msg = json.loads(data)
                    except Exception:
                        continue
                    # A batch is a list; scan it for our response.
                    for m in (msg if isinstance(msg, list) else [msg]):
                        if isinstance(m, dict) and m.get("id") == want_id:
                            return m
                    # Server-initiated requests/notifications are skipped —
                    # Friday exposes no sampling/roots/elicitation.
                raise TimeoutError(
                    f"SSE stream ended without a response to id {want_id}")
            raw = resp.read()
        if not raw:
            return None
        msg = json.loads(raw.decode("utf-8"))
        if isinstance(msg, list):
            for m in msg:
                if isinstance(m, dict) and m.get("id") == want_id:
                    return m
            return None
        return msg

    def _request(self, method: str, params: dict, timeout: float,
                 _retried: bool = False) -> Any:
        rid = self._next_id()
        payload = {"jsonrpc": "2.0", "id": rid, "method": method,
                   "params": params}
        try:
            msg = self._post(payload, timeout, want_id=rid)
        except _SessionExpired:
            if _retried or method == "initialize":
                raise RuntimeError("session expired and re-initialize failed")
            self._log(f"[mcp:{self.name}] session expired — re-initializing")
            self._handshake(timeout)
            return self._request(method, params, timeout, _retried=True)
        if msg is None:
            raise RuntimeError(f"{method}: no response from server")
        if msg.get("error") is not None:
            err = msg["error"]
            raise RuntimeError(
                f"{method} error: {err.get('message', err) if isinstance(err, dict) else err}")
        return msg.get("result")

    def _notify(self, method: str, params: dict, timeout: float = 15.0) -> None:
        try:
            self._post({"jsonrpc": "2.0", "method": method, "params": params},
                       timeout, want_id=None)
        except (_AuthRequired, _SessionExpired):
            raise
        except Exception:
            pass  # a lost notification is not fatal

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _handshake(self, timeout: float) -> None:
        self.session_id = None
        self.protocol_version = None
        init = self._request(
            "initialize",
            {
                "protocolVersion": _HTTP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "friday", "version": "1.0"},
            },
            timeout=timeout,
        )
        self.server_info = (init or {}).get("serverInfo", {}) or {}
        self.protocol_version = ((init or {}).get("protocolVersion")
                                 or _HTTP_PROTOCOL_VERSION)
        self._notify("notifications/initialized", {})
        listed = self._request("tools/list", {}, timeout=timeout)
        self.tools = list((listed or {}).get("tools", []) or [])

    def start(self, timeout: float = _DEFAULT_START_TIMEOUT) -> bool:
        """Run the handshake against the remote endpoint. A 401 parks the
        server in "needs_auth" rather than "error" so the UI can offer the
        one-click browser authorization."""
        with self._lifecycle_lock:
            if self.status == "ready":
                return True
            self.status = "starting"
            self.error = None
            try:
                self._handshake(timeout)
                self.status = "ready"
                self._log(
                    f"[mcp:{self.name}] ready — {len(self.tools)} tool(s): "
                    + ", ".join(t.get("name", "?") for t in self.tools)
                )
                return True
            except _AuthRequired:
                self.status = "needs_auth"
                self.error = ("authorization required — approve this "
                              "connector in your browser")
                self._log(f"[mcp:{self.name}] needs OAuth authorization")
                return False
            except Exception as e:  # noqa: BLE001
                self.status = "error"
                self.error = str(e)
                self._log(f"[mcp:{self.name}] handshake failed: {e}")
                return False

    def stop(self) -> None:
        """Best-effort session teardown (servers MAY support DELETE)."""
        sid = self.session_id
        if sid:
            try:
                req = urllib.request.Request(
                    self.url, method="DELETE",
                    headers={"Mcp-Session-Id": sid, **self.headers})
                urllib.request.urlopen(req, timeout=5).close()
            except Exception:
                pass
        self.session_id = None
        if self.status == "ready":
            self.status = "stopped"

    def _alive(self) -> bool:
        return self.status == "ready"

    # ── tool invocation ──────────────────────────────────────────────────────

    def call_tool(
        self, tool_name: str, arguments: dict, timeout: float = _DEFAULT_CALL_TIMEOUT
    ) -> str:
        if self.status != "ready" and not self.start():
            return f"[mcp:{self.name} unavailable] {self.error or 'server not ready'}"
        try:
            result = self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments or {}},
                timeout=timeout,
            )
        except _AuthRequired:
            # Token revoked/expired beyond refresh mid-session.
            self.status = "needs_auth"
            self.error = "authorization expired — reconnect this server"
            return (f"[mcp:{self.name} unavailable] authorization expired — "
                    f"re-authorize the connector and try again")
        except Exception as e:  # noqa: BLE001
            return f"[mcp:{self.name} error] {e}"
        return _flatten_tool_result(result)

    def info(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "alive": self._alive(),
            "error": self.error,
            "transport": "http",
            "url": self.url,
            "command": self.url,     # display-compat with stdio entries
            "needs_auth": self.status == "needs_auth",
            "tool_count": len(self.tools),
            "tools": [t.get("name", "?") for t in self.tools],
            "server_info": self.server_info,
        }


class MCPManager:
    """Owns the set of configured MCP servers and routes calls to them."""

    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self.servers: dict[str, MCPServerProcess] = {}
        self._log = log or (lambda _m: None)
        self._lock = threading.Lock()

    def load_config(self, config: dict) -> None:
        """Build (but do not start) server objects from an mcp_servers.json-style
        dict: {"servers": {name: {command, args, env, cwd, enabled}}}.

        Also accepts the flat Claude-Desktop shape {name: {...}} for convenience.
        """
        servers = config.get("servers", config) if isinstance(config, dict) else {}
        with self._lock:
            self.servers = {}
            for name, spec in (servers or {}).items():
                if not isinstance(spec, dict):
                    continue
                if spec.get("url"):
                    # Remote server over Streamable HTTP.
                    sp: MCPServerProcess | MCPServerHTTP = MCPServerHTTP(
                        name=name,
                        url=str(spec["url"]),
                        headers=spec.get("headers"),
                        log=self._log,
                    )
                else:
                    sp = MCPServerProcess(
                        name=name,
                        command=spec.get("command", ""),
                        args=spec.get("args", []),
                        env=spec.get("env", {}),
                        cwd=spec.get("cwd"),
                        log=self._log,
                    )
                if spec.get("enabled") is False:
                    # Keep a stopped placeholder so status() still lists it.
                    sp.status = "disabled"
                self.servers[name] = sp

    def start_all(self, on_ready: Callable[[str, list[dict]], None] | None = None) -> None:
        """Start every enabled server in its own thread. on_ready(name, tools)
        fires per-server as each handshake completes. Returns immediately."""
        for name, sp in list(self.servers.items()):
            if sp.status == "disabled":
                continue
            threading.Thread(
                target=self._start_one,
                args=(name, on_ready),
                name=f"mcp-{name}-start",
                daemon=True,
            ).start()

    def _start_one(self, name: str, on_ready) -> None:
        sp = self.servers.get(name)
        if sp is None:
            return
        if sp.start() and on_ready:
            try:
                on_ready(name, sp.tools)
            except Exception as e:  # noqa: BLE001
                self._log(f"[mcp:{name}] on_ready callback failed: {e}")

    def restart(self, name: str, on_ready=None) -> bool:
        sp = self.servers.get(name)
        if sp is None:
            return False
        sp.stop()
        ok = sp.start()
        if ok and on_ready:
            try:
                on_ready(name, sp.tools)
            except Exception:
                pass
        return ok

    def authorize(self, name: str, *, open_browser: bool = True,
                  on_ready: Callable[[str, list[dict]], None] | None = None) -> dict:
        """Start the OAuth browser flow for a remote server; once tokens land,
        the server restarts and on_ready fires with its tools (same callback
        contract as start_all). Only meaningful for HTTP servers."""
        sp = self.servers.get(name)
        if sp is None:
            return {"ok": False, "error": f"no such server: {name}"}
        if not isinstance(sp, MCPServerHTTP):
            return {"ok": False, "error": f"'{name}' is a local stdio server — "
                                          "it does not use OAuth"}
        if sp.status == "disabled":
            return {"ok": False, "error": f"server '{name}' is disabled"}

        def _done(server_name: str, success: bool) -> None:
            if not success:
                return
            self.restart(server_name, on_ready=on_ready)

        result = sp.begin_auth(open_browser=open_browser, on_complete=_done)
        return result

    def auth_status(self, name: str) -> dict:
        """Poll surface for an in-flight authorization (proxies mcp_oauth)."""
        sp = self.servers.get(name)
        base: dict = {"server_status": sp.status if sp else "missing"}
        try:
            from agent_friday import mcp_oauth
            base.update(mcp_oauth.authorization_status(name))
        except Exception as e:  # noqa: BLE001
            base.update({"state": "error", "error": str(e)})
        return base

    def stop_all(self) -> None:
        for sp in list(self.servers.values()):
            sp.stop()

    def call(self, server: str, tool: str, arguments: dict, timeout: float = _DEFAULT_CALL_TIMEOUT) -> str:
        sp = self.servers.get(server)
        if sp is None:
            return f"[mcp error] no such server: {server}"
        if sp.status == "disabled":
            return f"[mcp error] server '{server}' is disabled"
        return sp.call_tool(tool, arguments, timeout=timeout)

    def all_tools(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for name, sp in self.servers.items():
            for t in sp.tools:
                out.append((name, t))
        return out

    def status(self) -> dict:
        with self._lock:
            return {name: sp.info() for name, sp in self.servers.items()}

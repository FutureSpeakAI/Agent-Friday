"""
mcp_client.py — A lightweight, dependency-free MCP (Model Context Protocol)
client for Friday.

Friday speaks the same protocol Claude Desktop and Claude Code use to talk to
MCP "connectors": each server is a subprocess that exchanges newline-delimited
JSON-RPC 2.0 messages over stdio. This module spawns those subprocesses,
performs the `initialize` → `tools/list` handshake, and forwards `tools/call`
requests on demand. It then hands the discovered tools back to server.py so they
can be registered into Friday's unified tool registry alongside the native tools.

Design goals:
  * Pure stdlib (subprocess / threading / json) — the Python `mcp` SDK is not a
    hard dependency, so this runs on a vanilla install.
  * Thread-safe. Flask runs threaded=True, so several agent turns may call the
    same MCP server concurrently; JSON-RPC ids correlate request↔response and a
    per-process reader thread dispatches replies.
  * Non-blocking startup. start_all() launches each server in its own thread and
    fires an on_ready callback when (and if) the handshake completes. A server
    that never comes up just stays in the "error" state — it never wedges boot.
  * Crash-resilient. A call against a dead process triggers a single restart
    attempt before failing.

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
from collections import deque
from typing import Any, Callable

# Spawn child processes without flashing a console window on Windows.
_CREATE_FLAGS = 0
if sys.platform == "win32":
    _CREATE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# MCP protocol revision we advertise in `initialize`. Servers negotiate down if
# they only speak an older revision; this is just our preferred version.
_PROTOCOL_VERSION = "2024-11-05"

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
        full_env.update({k: str(v) for k, v in self.env.items()})
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
                if spec.get("enabled") is False:
                    # Keep a stopped placeholder so status() still lists it.
                    sp = MCPServerProcess(
                        name=name,
                        command=spec.get("command", ""),
                        args=spec.get("args", []),
                        env=spec.get("env", {}),
                        cwd=spec.get("cwd"),
                        log=self._log,
                    )
                    sp.status = "disabled"
                    self.servers[name] = sp
                    continue
                self.servers[name] = MCPServerProcess(
                    name=name,
                    command=spec.get("command", ""),
                    args=spec.get("args", []),
                    env=spec.get("env", {}),
                    cwd=spec.get("cwd"),
                    log=self._log,
                )

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

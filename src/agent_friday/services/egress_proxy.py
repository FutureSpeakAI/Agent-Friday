"""A loopback allow-list proxy for commands and connector servers (S3, option D).

Approved commands and MCP stdio servers run at the owner's integrity level, and Windows
gives a per-user app no way to block their network without an administrator step (see
ftv/program/DESIGN-network-control-commands-mcp.md). What this gives instead:

  * one HTTP/HTTPS forward proxy on 127.0.0.1, started on first use;
  * a SESSION per run or server: its own credential in the proxy URL (no other local
    process can ride a session), a label, a mode and a host allowlist;
      - "enforce": only listed hosts (exact, or "*.example.com" for subdomains);
        anything else is refused with 403;
      - "observe": everything passes, every host is recorded (connectors that predate
        allowlists keep working while the record shows what they reach);
      - "deny": nothing passes (read-only commands, which need no network);
  * a record of every connection: when, session label, host, port, allowed or not.
    Never a path, header or byte of content.

IT IS NOT A BOUNDARY. A program that ignores HTTP(S)_PROXY, or opens a raw socket,
goes around it. Every caller says so in its own words; the true block (a firewall rule on
a Friday shim) waits on the owner's answer and a code-signing certificate.
"""
from __future__ import annotations

import base64
import fnmatch
import hmac
import json
import os
import secrets
import select
import socket
import socketserver
import threading
import time
from collections import deque
from typing import Dict, Iterable, List, Optional

MODES = ("enforce", "observe", "deny")
#: Package registries and git hosts an approved command commonly needs (engineering
#: call S3: Settings `command_network.allow_hosts` replaces this list).
DEFAULT_COMMAND_HOSTS = (
    "pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "registry.yarnpkg.com",
    "github.com", "*.github.com", "objects.githubusercontent.com", "codeload.github.com",
    "raw.githubusercontent.com",
)
_MAX_HEADER = 64 * 1024
_IDLE_S = 300.0
_RECORD_MAX = 2000

_LOCK = threading.Lock()
_SERVER: Optional["_Server"] = None
_SESSIONS: Dict[str, dict] = {}
_RECORD: deque = deque(maxlen=_RECORD_MAX)


# ── sessions ─────────────────────────────────────────────────────────────────

def _norm_hosts(hosts: Iterable[str]) -> List[str]:
    out = []
    for h in hosts or ():
        h = str(h or "").strip().lower().rstrip(".")
        if h and all(c.isalnum() or c in "-.*:" for c in h) and h not in out:
            out.append(h)
    return out


def host_allowed(host: str, allow: Iterable[str]) -> bool:
    h = str(host or "").strip().lower().rstrip(".")
    if not h:
        return False
    for pat in allow or ():
        if pat == h:
            return True
        if pat.startswith("*.") and (h.endswith(pat[1:]) and h != pat[2:]):
            return True
        if "*" in pat and not pat.startswith("*.") and fnmatch.fnmatchcase(h, pat):
            return True
    return False


def open_session(label: str, mode: str = "enforce", allow_hosts: Iterable[str] = ()) -> dict:
    """Start (if needed) the proxy and register a session. Returns {"token", "url",
    "env"}: `env` is the variables a child process should be given."""
    if mode not in MODES:
        raise ValueError("unknown proxy mode %r" % mode)
    srv = _ensure_server()
    token = secrets.token_urlsafe(24)
    with _LOCK:
        _SESSIONS[token] = {"label": str(label)[:120], "mode": mode,
                            "allow": _norm_hosts(allow_hosts), "opened": time.time()}
    url = "http://friday:%s@127.0.0.1:%d" % (token, srv.server_address[1])
    env = {"HTTP_PROXY": url, "HTTPS_PROXY": url, "ALL_PROXY": url, "NO_PROXY": "",
           # npm reads its own variables as well (git, pip and curl read the above).
           "npm_config_proxy": url, "npm_config_https_proxy": url}
    if os.name != "nt":
        # POSIX names are case-sensitive and some tools read only the lower-case ones.
        # On Windows one spelling is the variable; two spellings in one environment
        # block are duplicates that break programs (PowerShell's Env: drive, for one).
        env.update({"http_proxy": url, "https_proxy": url, "all_proxy": url, "no_proxy": ""})
    return {"token": token, "url": url, "env": env}


def apply_env(env: dict, proxy_env: dict) -> None:
    """Put a session's proxy variables into `env`, replacing any value already there in
    any spelling (on Windows a name differing only in case is the same variable, and a
    server's own value must not win)."""
    if os.name == "nt":
        names = {k.upper() for k in proxy_env}
        for k in [k for k in env if k.upper() in names]:
            del env[k]
    env.update(proxy_env)


def close_session(token: str) -> None:
    with _LOCK:
        _SESSIONS.pop(token, None)


def record(limit: int = 200) -> List[dict]:
    with _LOCK:
        return list(_RECORD)[-int(limit):]


_UNKNOWN_CALLERS = {"count": 0}
_FILE_LOCK = threading.Lock()
_FILE_MAX = 5 * 1024 * 1024


def _note(sess: Optional[dict], host: str, port: int, allowed: bool, why: str) -> None:
    if sess is None:
        # A caller with no session (any local process) is counted, never recorded:
        # it must not be able to flood the record or the disk.
        with _LOCK:
            _UNKNOWN_CALLERS["count"] += 1
        return
    row = {"t": round(time.time(), 3), "session": sess.get("label", "?"),
           "mode": sess.get("mode", "?"), "host": str(host)[:253], "port": int(port or 0),
           "allowed": bool(allowed), "why": why}
    with _LOCK:
        _RECORD.append(row)
    try:
        import agent_friday.core as core
        d = core.FRIDAY_DIR / "egress"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "proxy.jsonl"
        with _FILE_LOCK:
            if path.exists() and path.stat().st_size > _FILE_MAX:
                os.replace(path, d / "proxy.1.jsonl")          # one older file kept
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
    except Exception:
        pass


# ── the proxy ────────────────────────────────────────────────────────────────

def _session_for(headers: Dict[str, str]) -> Optional[dict]:
    auth = headers.get("proxy-authorization", "")
    if not auth.lower().startswith("basic "):
        return None
    try:
        user, _, token = base64.b64decode(auth[6:].strip()).decode("utf-8").partition(":")
    except Exception:
        return None
    with _LOCK:
        for t, s in _SESSIONS.items():
            if hmac.compare_digest(t, token):
                return s
    return None


def _port_allowed(host: str, port: int, allow: Iterable[str]) -> bool:
    """80 and 443 for an allowed host; another port only when listed as host:port."""
    if port in (80, 443):
        return True
    return ("%s:%d" % (str(host).lower().rstrip("."), port)) in set(allow or ())


def _private_target(host: str, port: int) -> Optional[str]:
    """Why a name resolves somewhere an allowlist entry should never lead (this PC,
    the LAN, link-local), or None. A listed IP literal is the owner's explicit choice."""
    import ipaddress
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None                         # the connect will fail on its own
    first = None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_reserved:
            return "it resolves to an address on this PC or the local network"
        first = first or info[4][0]
    # The address that was checked is the one connected to (no second lookup that
    # could answer differently).
    _CHECKED[threading.get_ident()] = first
    return None


_CHECKED: Dict[int, Optional[str]] = {}
_SLOTS = threading.BoundedSemaphore(64)


def _decide(sess: Optional[dict], host: str, port: int = 443) -> tuple:
    if sess is None:
        return False, "no session (unknown caller)"
    if sess["mode"] == "deny":
        return False, "this run has no network"
    if sess["mode"] == "observe":
        return True, "observed"
    allow = sess["allow"]
    if not (host_allowed(host, allow) or host_allowed("%s:%d" % (host, port), allow)):
        return False, "not on the allowlist"
    if not _port_allowed(host, port, allow):
        return False, "port %d is not allowed for it" % port
    import ipaddress
    try:
        ipaddress.ip_address(host.strip("[]"))
        literal = True
    except ValueError:
        literal = False
    if not literal:
        why = _private_target(host, port)
        if why:
            return False, why
    return True, "on the allowlist"


def _read_head(conn: socket.socket) -> Optional[bytes]:
    """The request head, within one 30-second deadline for the whole of it."""
    buf = b""
    deadline = time.monotonic() + 30
    while b"\r\n\r\n" not in buf:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        conn.settimeout(left)
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
        if len(buf) > _MAX_HEADER:
            return None
    return buf


def _split_host_port(hostport: str, default: int) -> tuple:
    hostport = hostport.strip()
    if hostport.startswith("["):
        host, _, rest = hostport[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = hostport.rpartition(":") if hostport.count(":") == 1 else (hostport, "", "")
        if not host:
            host, port = hostport, ""
    try:
        return host, int(port) if port else default
    except ValueError:
        return host, default


def _pipe(a: socket.socket, b: socket.socket) -> None:
    socks = [a, b]
    for s in socks:
        s.settimeout(_IDLE_S)            # a blocked send gives up too
    while True:
        r, _, x = select.select(socks, [], socks, _IDLE_S)
        if x or not r:
            return
        for s in r:
            try:
                data = s.recv(65536)
            except OSError:
                return
            if not data:
                return
            (b if s is a else a).sendall(data)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):  # noqa: C901 - one small protocol
        conn = self.request
        if not _SLOTS.acquire(blocking=False):
            try:
                conn.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            return
        try:
            self._handle(conn)
        finally:
            _SLOTS.release()
            _CHECKED.pop(threading.get_ident(), None)

    def _handle(self, conn):  # noqa: C901 - one small protocol
        try:
            head = _read_head(conn)
            if not head:
                return
            text, _, rest = head.partition(b"\r\n\r\n")
            lines = text.decode("latin-1").split("\r\n")
            method, target, _ver = (lines[0].split(" ") + ["", "", ""])[:3]
            headers = {}
            for ln in lines[1:]:
                k, _, v = ln.partition(":")
                headers[k.strip().lower()] = v.strip()
            sess = _session_for(headers)
            if method.upper() == "CONNECT":
                host, port = _split_host_port(target, 443)
            else:
                from urllib.parse import urlsplit
                u = urlsplit(target)
                if u.scheme != "http" or not u.hostname:
                    conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                    return
                host, port = u.hostname, u.port or 80
            if sess is None:
                _note(None, host, port, False, "no session (unknown caller)")
                conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                             b"Proxy-Authenticate: Basic realm=\"friday\"\r\nContent-Length: 0\r\n\r\n")
                return
            _CHECKED.pop(threading.get_ident(), None)
            ok, why = _decide(sess, host, port)
            _note(sess, host, port, ok, why)
            if not ok:
                body = ("Blocked by Friday: %s is %s for this run.\n" % (host, why)).encode()
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\n"
                             b"Content-Length: %d\r\n\r\n" % len(body) + body)
                return
            try:
                target_ip = _CHECKED.get(threading.get_ident())
                up = socket.create_connection((target_ip or host, port), timeout=30)
            except OSError:
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                return
            with up:
                if method.upper() == "CONNECT":
                    conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    if rest:
                        up.sendall(rest)
                else:
                    from urllib.parse import urlsplit
                    u = urlsplit(target)
                    path = (u.path or "/") + (("?" + u.query) if u.query else "")
                    out = ["%s %s HTTP/1.1" % (method, path)]
                    for ln in lines[1:]:
                        k = ln.partition(":")[0].strip().lower()
                        if k.startswith("proxy-"):
                            continue
                        out.append(ln)
                    up.sendall("\r\n".join(out).encode("latin-1") + b"\r\n\r\n" + rest)
                _pipe(conn, up)
        except Exception:
            return


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


def _ensure_server() -> _Server:
    global _SERVER
    with _LOCK:
        if _SERVER is not None:
            return _SERVER
        srv = _Server(("127.0.0.1", 0), _Handler)
        t = threading.Thread(target=srv.serve_forever, name="friday-egress-proxy", daemon=True)
        t.start()
        srv._friday_thread = t
        _SERVER = srv
        return srv


def _shutdown_for_tests() -> None:
    global _SERVER
    with _LOCK:
        srv, _SERVER = _SERVER, None
        _SESSIONS.clear()
        _RECORD.clear()
    if srv is not None:
        t = getattr(srv, "_friday_thread", None)
        if isinstance(t, threading.Thread) and t.is_alive():
            srv.shutdown()          # waits for serve_forever, so only when it is running
        srv.server_close()


# ── policy for the two callers ───────────────────────────────────────────────

def command_hosts() -> List[str]:
    try:
        import agent_friday.core as core
        cfg = (core._load_settings() or {}).get("command_network") or {}
        hosts = cfg.get("allow_hosts")
        if isinstance(hosts, list):
            return _norm_hosts(hosts)
    except Exception:
        pass
    return list(DEFAULT_COMMAND_HOSTS)


def mcp_policy(server_name: str, config: Optional[dict]) -> tuple:
    """(mode, hosts) for an MCP stdio server: its own allow_hosts enforce; without
    them it is observed unless Settings says mcp_network_default = "enforce"."""
    cfg = config or {}
    hosts = cfg.get("allow_hosts")
    if isinstance(hosts, list):
        return "enforce", _norm_hosts(hosts)
    try:
        import agent_friday.core as core
        if str((core._load_settings() or {}).get("mcp_network_default") or "").lower() == "enforce":
            return "enforce", []
    except Exception:
        pass
    return "observe", []

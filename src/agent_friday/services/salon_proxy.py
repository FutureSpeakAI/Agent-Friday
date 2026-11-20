"""The salon's key-injecting proxy (docs/design/active/vibe-coding-salon.md
§4.7 "The proxy injects the key").

A coding agent running in the box is pointed at this proxy and given a dummy
key. The proxy swaps the dummy for the real key header, only on requests to
the provider's API, relays the answer as it arrives (streamed or not), and
records host, path, method, status and size of every request. It records no
body and no header value, so nothing here can leak a key or a prompt.

Invariants:

- **Loopback only.** The listener binds 127.0.0.1 on an ephemeral port and
  refuses anything else at construction; a public bind would make the proxy a
  key oracle for the LAN.
- **Provider API only.** Only paths under the allowed prefixes (``/v1/``) with
  the allowed methods reach upstream. Everything else is refused with 403 and
  recorded as refused; upstream never sees it.
- **The key never leaves the process.** It is read from ``key_provider`` at
  request time, placed in the outgoing header, and appears nowhere else. With
  no key available the request is refused with 401 and nothing is sent.
- **Standard library only**: this is FA2 made structural, and it must not
  depend on a package that could change what it forwards.
"""
from __future__ import annotations

import http.client
import http.server
import json
import logging
import socketserver
import threading
import time
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

_log = logging.getLogger(__name__)

DEFAULT_UPSTREAM = "https://api.anthropic.com"
DUMMY_KEY = "friday-proxy-dummy"
#: Request headers never forwarded: the client's own credentials, and the
#: hop-by-hop fields that belong to each connection, not the message.
_STRIP_REQUEST = {"host", "connection", "keep-alive", "proxy-authorization", "proxy-connection", "te", "trailer",
                  "transfer-encoding", "upgrade", "x-api-key", "authorization", "content-length"}
_STRIP_RESPONSE = {"connection", "keep-alive", "transfer-encoding", "content-length", "trailer", "upgrade"}
_CHUNK = 8192
#: The record keeps the newest entries only: a listener that lives as long as the app must not grow without bound.
CAPTURE_CAP = 1000
#: Sent on the proxy's own "no key" refusal so a caller can tell it from the provider's 401.
NO_KEY_HEADER = "X-Friday-Gateway"


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _escapes_its_path(path: str) -> bool:
    """True for a path that could climb out of its prefix once the provider normalises it: a ``.`` or ``..`` segment
    (raw or percent-encoded, even twice), a backslash or a ``;``. Guarded by tests/unit/test_key_gateway_descriptor.py."""
    decoded = path
    for _ in range(3):
        decoded = unquote(decoded)
    return (any(part in (".", "..") for part in decoded.split("/")) or "\\" in decoded or ";" in decoded)


class KeyProxy:
    def __init__(self, upstream: str = DEFAULT_UPSTREAM, key_provider: Optional[Callable[[], Optional[str]]] = None,
                 allowed_prefixes=("/v1/",), allowed_methods=("POST", "GET"), host: str = "127.0.0.1",
                 key_header: str = "x-api-key", key_format: str = "{key}", upstream_timeout: int = 600):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("the salon proxy listens on loopback only")
        u = urlsplit(upstream)
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError("upstream must be an http(s) URL")
        self.upstream = upstream.rstrip("/")
        self._scheme, self._netloc = u.scheme, u.netloc
        self.key_provider = key_provider or (lambda: None)
        self.allowed_prefixes = tuple(allowed_prefixes)
        self.allowed_methods = tuple(m.upper() for m in allowed_methods)
        #: Where the key goes on the way out (``key_format`` is filled with the key). Whatever the caller sent under
        #: this name is dropped first. Guarded by tests/unit/test_key_gateway.py.
        self.key_header, self.key_format = key_header, key_format
        self.upstream_timeout = upstream_timeout
        self._host = "127.0.0.1"
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None
        self._capture: list = []
        self._lock = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> "KeyProxy":
        if self._server is not None:
            return self
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):          # nothing about requests reaches a log
                pass

            def _handle(self):
                proxy._relay(self)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle

        self._server = _Server((self._host, 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        srv, self._server = self._server, None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass

    @property
    def address(self) -> tuple:
        if self._server is None:
            raise RuntimeError("the proxy is not running")
        return self._server.server_address[0], self._server.server_address[1]

    @property
    def url(self) -> str:
        host, port = self.address
        return "http://%s:%d" % (host, port)

    # ── the record ───────────────────────────────────────────────────────────
    def _record(self, **entry) -> None:
        with self._lock:
            self._capture.append(entry)
            del self._capture[:-CAPTURE_CAP]

    def capture(self) -> list:
        with self._lock:
            return [dict(e) for e in self._capture]

    def summary(self) -> dict:
        """Hosts and paths reached, with counts; refusals counted apart."""
        cap = self.capture()
        by: dict = {}
        for e in cap:
            k = (e["host"], e["path"])
            row = by.setdefault(k, {"host": e["host"], "path": e["path"], "count": 0, "refused": 0})
            if e.get("refused"):
                row["refused"] += 1
            else:
                row["count"] += 1
        hosts = sorted(by.values(), key=lambda r: (r["host"], r["path"]))
        return {"requests": len(cap), "refused": sum(1 for e in cap if e.get("refused")), "hosts": hosts}

    # ── the relay ────────────────────────────────────────────────────────────
    # A refused request's body is read and dropped (up to this many bytes) before
    # the answer goes out. Closing a socket with unread bytes still queued makes
    # Windows reset the connection, and the client then sees "connection aborted"
    # instead of the refusal.
    _DRAIN_LIMIT = 1 << 20

    def _drain(self, h) -> None:
        try:
            left = min(int(h.headers.get("Content-Length") or 0), self._DRAIN_LIMIT)
        except ValueError:
            left = 0
        try:
            while left > 0:
                chunk = h.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass

    def _refuse(self, h, status: int, text: str, path: str, method: str, t0: float, extra: Optional[dict] = None) -> None:
        self._drain(h)
        body = json.dumps({"error": text}).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            h.send_header(name, value)
        h.send_header("Connection", "close")
        h.end_headers()
        h.wfile.write(body)
        h.close_connection = True
        self._record(ts=time.time(), method=method, path=path, host=self._netloc, status=status, bytes_in=0, bytes_out=len(body),
                     ms=int((time.time() - t0) * 1000), refused=True)

    def _relay(self, h) -> None:
        t0 = time.time()
        method = h.command.upper()
        path = h.path.split("?", 1)[0]
        if (method not in self.allowed_methods or not any(path.startswith(p) for p in self.allowed_prefixes)
                or _escapes_its_path(path)):
            self._refuse(h, 403, "refused: the salon proxy forwards only the provider API (%s)" % ", ".join(self.allowed_prefixes),
                         path, method, t0)
            return
        key = None
        try:
            key = self.key_provider()
        except Exception:
            key = None
        if not key:
            self._refuse(h, 401, "no key available for this codebase; nothing was sent", path, method, t0,
                         extra={NO_KEY_HEADER: "no-key"})
            return
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        headers = {k: v for k, v in h.headers.items()
                   if k.lower() not in _STRIP_REQUEST and k.lower() != self.key_header.lower()}
        headers["Host"] = self._netloc
        headers[self.key_header] = self.key_format.format(key=key)
        headers["Connection"] = "close"
        if body:
            headers["Content-Length"] = str(len(body))
        conn_cls = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(self._netloc, timeout=self.upstream_timeout)
        bytes_out = 0
        status = 0
        try:
            conn.request(method, h.path, body=body or None, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            h.send_response(status)
            streamed = resp.getheader("Content-Length") is None
            for k, v in resp.getheaders():
                if k.lower() in _STRIP_RESPONSE:
                    continue
                h.send_header(k, v)
            if streamed:
                h.send_header("Transfer-Encoding", "chunked")
            else:
                h.send_header("Content-Length", resp.getheader("Content-Length"))
            h.send_header("Connection", "close")
            h.end_headers()
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                bytes_out += len(chunk)
                if streamed:
                    h.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                else:
                    h.wfile.write(chunk)
                h.wfile.flush()
            if streamed:
                h.wfile.write(b"0\r\n\r\n")
                h.wfile.flush()
        except Exception as e:
            if not status:
                try:
                    self._refuse(h, 502, "the provider could not be reached", path, method, t0)
                except Exception:
                    pass
                return
            _log.debug("relay ended early: %s", type(e).__name__)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            h.close_connection = True
        self._record(ts=time.time(), method=method, path=path, host=self._netloc, status=status, bytes_in=len(body),
                     bytes_out=bytes_out, ms=int((time.time() - t0) * 1000), refused=False)

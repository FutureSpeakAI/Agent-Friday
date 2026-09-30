"""A WSGI server whose accept loop never creates a thread.

werkzeug's threaded server starts one thread per connection, and the accept
loop waits for that thread to signal that it has started. When the operating
system cannot finish creating a thread (loader lock held by a native thread,
native pool exhaustion), the accept loop waits forever: the port stays in
LISTEN, connections queue in the kernel, and nothing is ever served.

This server pre-starts a fixed set of worker threads at construction. The
accept loop only accepts, then hands the connection to a bounded queue; it
never starts a thread, never waits on a worker, and never blocks on a client.
A full queue is answered with an immediate 503.

Long-lived streams (SSE, WebSocket) each pin a worker for their whole life.
They are admitted against a cap that leaves a floor of workers for ordinary
requests, so any number of open streams cannot starve them; a stream over the
cap gets a 503 with Retry-After, which EventSource clients retry by themselves.

Connections are HTTP/1.0 (one request per connection): an idle keep-alive
connection would otherwise pin a worker for nothing.
"""
from __future__ import annotations

import logging
import os
import queue
import socket
import threading
import time
from typing import Callable

from werkzeug.serving import BaseWSGIServer, WSGIRequestHandler

_log = logging.getLogger("friday.pooled_server")

DEFAULT_WORKERS = 64
DEFAULT_RESERVED_FOR_REQUESTS = 16
DEFAULT_BACKLOG_QUEUE = 256
# A client that sends nothing for this long is dropped, so a stalled peer
# cannot pin a worker. Streams clear it once admitted.
REQUEST_HEAD_TIMEOUT_S = 60.0

_BUSY_503 = (b"HTTP/1.0 503 Service Unavailable\r\n"
             b"Retry-After: 2\r\nConnection: close\r\n"
             b"Content-Type: text/plain\r\nContent-Length: 27\r\n\r\n"
             b"Friday is busy. Try again.\n")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


class StreamAdmission:
    """WSGI middleware: the cap on requests that hold a worker indefinitely."""

    def __init__(self, app, limit: int, counters: "PoolStats"):
        self.app = app
        self.limit = limit
        self.stats = counters

    @staticmethod
    def is_stream_request(environ) -> bool:
        if "websocket" in (environ.get("HTTP_UPGRADE") or "").lower():
            return True
        return "text/event-stream" in (environ.get("HTTP_ACCEPT") or "")

    def __call__(self, environ, start_response):
        counted = [False]

        def release():
            if counted[0]:
                counted[0] = False
                self.stats.stream_done()

        if self.is_stream_request(environ):
            if not self.stats.stream_start(self.limit):
                start_response("503 Service Unavailable", [
                    ("Retry-After", "5"), ("Content-Type", "text/plain")])
                return [b"Too many open streams.\n"]
            counted[0] = True
            sock = environ.get("werkzeug.socket")
            if sock is not None:
                try:
                    sock.settimeout(None)
                except Exception:
                    pass

        def sr(status, headers, exc_info=None):
            ctype = next((v for k, v in headers if k.lower() == "content-type"), "")
            if not counted[0] and "text/event-stream" in ctype:
                # A stream that announced itself only in its reply (a POST
                # that streams): counted, and released the same way.
                self.stats.stream_start(None)
                counted[0] = True
                s = environ.get("werkzeug.socket")
                if s is not None:
                    try:
                        s.settimeout(None)
                    except Exception:
                        pass
            return start_response(status, headers, exc_info)

        try:
            result = self.app(environ, sr)
        except BaseException:
            release()
            raise
        return _Closing(result, release)


class _Closing:
    def __init__(self, inner, on_close: Callable[[], None]):
        self._inner = inner
        self._on_close = on_close

    def __iter__(self):
        # Released when the body ends, raises, or is abandoned: the server's
        # own close() call is skipped when its cleanup before it raises (a
        # client that vanished), and a slot must not leak with it.
        try:
            yield from self._inner
        finally:
            self._on_close()

    def close(self):
        try:
            close = getattr(self._inner, "close", None)
            if close:
                close()
        finally:
            self._on_close()


class PoolStats:
    """Counters the watchdog and /api/health read. Lock-protected, no threads."""

    def __init__(self, workers: int):
        self._lock = threading.Lock()
        self.workers = workers
        self.busy = 0
        self.completed = 0
        self.rejected = 0
        self.streams_open = 0
        self.accept_tick = time.monotonic()

    def stream_start(self, limit) -> bool:
        with self._lock:
            if limit is not None and self.streams_open >= limit:
                self.rejected += 1
                return False
            self.streams_open += 1
            return True

    def stream_done(self):
        with self._lock:
            self.streams_open = max(0, self.streams_open - 1)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "workers": self.workers, "busy": self.busy,
                "completed": self.completed, "rejected": self.rejected,
                "streams_open": self.streams_open,
                "accept_loop_age_s": round(time.monotonic() - self.accept_tick, 2),
            }


class _OneRequestHandler(WSGIRequestHandler):
    """One request per connection: an idle keep-alive connection would pin a
    worker."""
    protocol_version = "HTTP/1.0"


class PooledWSGIServer(BaseWSGIServer):
    """BaseWSGIServer (single-threaded) with a fixed pre-started worker pool."""

    def __init__(self, host, port, app, *, workers: int | None = None,
                 reserved: int | None = None, backlog_queue: int | None = None,
                 ssl_context=None, handler=None, fd=None):
        self.n_workers = workers or _env_int("FRIDAY_HTTP_WORKERS", DEFAULT_WORKERS)
        reserved = DEFAULT_RESERVED_FOR_REQUESTS if reserved is None else reserved
        reserved = min(reserved, max(1, self.n_workers // 2))
        self.stats = PoolStats(self.n_workers)
        self.stream_limit = max(1, self.n_workers - reserved)
        wrapped = StreamAdmission(app, self.stream_limit, self.stats)
        super().__init__(host, port, wrapped, handler=handler or _OneRequestHandler,
                         ssl_context=ssl_context, fd=fd)
        # Any TLS handshake happens in a worker, on first read, under the
        # request-head timeout: a peer that stalls mid-handshake must not stall
        # the accept loop.
        if ssl_context is not None and hasattr(self.socket, "do_handshake_on_connect"):
            self.socket.do_handshake_on_connect = False
        self._jobs: queue.Queue = queue.Queue(
            maxsize=backlog_queue or DEFAULT_BACKLOG_QUEUE)
        self._workers: list[threading.Thread] = []
        for i in range(self.n_workers):
            t = threading.Thread(target=self._work, name=f"http-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    # -- accept side: no thread creation, no blocking -----------------------
    def service_actions(self):
        self.stats.accept_tick = time.monotonic()

    def process_request(self, request, client_address):
        try:
            self._jobs.put_nowait((request, client_address))
        except queue.Full:
            self.stats.rejected += 1
            try:
                request.settimeout(0.2)
                request.sendall(_BUSY_503)
                # Read what the client already sent before closing: closing a
                # socket with unread data resets it, and the reset can destroy
                # the 503 before the client reads it.
                request.shutdown(socket.SHUT_WR)
                request.settimeout(0.05)
                request.recv(65536)
            except Exception:
                pass
            self.shutdown_request(request)

    # -- worker side --------------------------------------------------------
    def _work(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            request, client_address = job
            with self.stats._lock:
                self.stats.busy += 1
            try:
                request.settimeout(REQUEST_HEAD_TIMEOUT_S)
                self.finish_request(request, client_address)
            except Exception:
                try:
                    self.handle_error(request, client_address)
                except Exception:
                    pass
            finally:
                try:
                    self.shutdown_request(request)
                except Exception:
                    pass
                with self.stats._lock:
                    self.stats.busy -= 1
                    self.stats.completed += 1

    def server_close(self):
        super().server_close()
        for _ in self._workers:
            try:
                self._jobs.put_nowait(None)
            except queue.Full:
                break


def make_pooled_server(host, port, app, **kw) -> PooledWSGIServer:
    return PooledWSGIServer(host, port, app, **kw)


_current: PooledWSGIServer | None = None


def stats() -> dict | None:
    """Pool counters for the running server, or None when it is not pooled."""
    return _current.stats.snapshot() if _current is not None else None


def completed_count() -> int:
    return _current.stats.completed if _current is not None else 0


def serve(host, port, app, *, ssl_context=None, **kw) -> None:
    """Serve until interrupted. Raises OSError when the port cannot be bound."""
    global _current
    srv = make_pooled_server(host, port, app, ssl_context=ssl_context, **kw)
    _current = srv
    scheme = "https" if ssl_context else "http"
    _log.info("serving %s://%s:%s with %d pre-started workers (streams capped at %d)",
              scheme, host, port, srv.n_workers, srv.stream_limit)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()

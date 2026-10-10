"""Friday's listeners (services/local_proxy) stay served, or say they are not.

All four listening sockets share one event loop on one thread. When that loop
stops dispatching -- stuck in a blocking call, or its thread ended -- the
sockets stay bound and the operating system keeps completing handshakes, so
every client is accepted and never answered, on http and https at once, while
status() went on reporting "listening". These tests stop the loop on purpose,
check that status() tells the truth, and that ensure_alive() puts a fresh loop
in its place. They run on ephemeral loopback ports; nothing touches 80 or 443.
"""
from __future__ import annotations

import http.server
import socket
import ssl
import struct
import sys
import threading
import time

import pytest

from agent_friday.services import local_ca, local_proxy
from agent_friday.services import local_address as la

HOST = "agent.pwtest"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"FRIDAY"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def proxy(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    local_ca.ensure(tmp_path, HOST)
    p = local_proxy.LocalProxy(
        host=HOST, upstream_port=srv.server_address[1],
        https_port=_free_port(), http_port=_free_port(),
        cert_file=str(tmp_path / local_ca.LEAF_CERT), key_file=str(tmp_path / local_ca.LEAF_KEY),
        addrs=("127.0.0.1",))
    p.start()
    gates = []
    yield p, gates
    for g in gates:
        g.set()
    p.stop()
    srv.shutdown()


def _http(p, timeout=3.0) -> str:
    """The status line for GET / over plain http, or the error's name."""
    try:
        with socket.create_connection(("127.0.0.1", p.http_port), timeout=timeout) as c:
            c.settimeout(timeout)
            c.sendall(f"GET / HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode())
            return c.recv(200).split(b"\r\n", 1)[0].decode()
    except Exception as e:
        return type(e).__name__


def _https(p, timeout=3.0) -> str:
    ctx = ssl._create_unverified_context()
    try:
        with socket.create_connection(("127.0.0.1", p.https_port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=HOST) as c:
                c.settimeout(timeout)
                c.sendall(f"GET / HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode())
                return c.recv(200).split(b"\r\n", 1)[0].decode()
    except Exception as e:
        return type(e).__name__


def _block_loop(p, gates) -> None:
    gate = threading.Event()
    gates.append(gate)
    p._loop.call_soon_threadsafe(gate.wait)        # a blocking call on the loop thread
    time.sleep(0.2)


def _end_loop_thread(p) -> None:
    def die():
        raise SystemExit                           # escapes run_forever; the thread ends quietly
    thread = p._thread
    p._loop.call_soon_threadsafe(die)
    thread.join(5)
    assert not thread.is_alive()


def test_serving_answers_both_listeners(proxy):
    p, _ = proxy
    assert _http(p).startswith("HTTP/1.") and " 200" in _http(p)
    assert " 200" in _https(p)
    st = p.status()
    assert st["http"]["listening"] and st["https"]["listening"]


def test_a_stuck_loop_is_not_reported_as_listening_and_is_replaced(proxy):
    p, gates = proxy
    _block_loop(p, gates)
    # what a client sees: connected, then nothing (both schemes at once)
    assert _http(p, timeout=1.5) == "TimeoutError"
    assert _https(p, timeout=1.5) == "TimeoutError"
    st = p.status()
    assert not st["http"]["listening"], "a stalled loop was reported as listening"
    assert not st["https"]["listening"]
    h = p.ensure_alive(timeout=1.0)
    assert h["restarted"] and h["ok"]
    assert " 200" in _http(p)
    assert " 200" in _https(p)
    assert p.status()["loop"]["restarts"] == 1


def test_a_dead_loop_thread_is_not_reported_as_listening_and_is_replaced(proxy):
    p, _ = proxy
    _end_loop_thread(p)
    assert _http(p, timeout=1.5) in ("TimeoutError", "ConnectionRefusedError")
    st = p.status()
    assert not st["http"]["listening"], "a dead loop was reported as listening"
    h = p.ensure_alive(timeout=1.0)
    assert h["restarted"] and h["ok"]
    assert " 200" in _http(p)
    assert " 200" in _https(p)


def test_a_healthy_loop_is_left_alone(proxy):
    p, _ = proxy
    gen = p.health()["generation"]
    h = p.ensure_alive()
    assert h["ok"] and not h["restarted"] and h["generation"] == gen


def test_a_stopped_proxy_is_not_brought_back(proxy):
    p, _ = proxy
    p.stop()
    h = p.ensure_alive(timeout=0.5)
    assert not h["restarted"] and p.status()["http"]["listening"] is False
    # Windows retries a refused loopback connect for about two seconds
    assert _http(p, timeout=5.0) == "ConnectionRefusedError"


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows accept path (AcceptEx)")
def test_a_client_that_resets_before_its_accept_costs_only_itself(proxy):
    """asyncio's own accept loop closes the listening socket on the first
    failed accept; a client that resets while the loop is busy is one."""
    p, gates = proxy
    gate = threading.Event()
    p._loop.call_soon_threadsafe(gate.wait)
    time.sleep(0.2)
    for _ in range(3):
        c = socket.create_connection(("127.0.0.1", p.http_port))
        c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        c.close()                                  # reset before the accept is taken
    time.sleep(0.3)
    gate.set()
    time.sleep(0.5)
    assert " 200" in _http(p), "one reset client closed the listener for everyone"
    assert p.status()["http"]["listening"]


def test_the_redirect_check_never_waits_on_the_status_lock():
    """_redirect_http runs on the listeners' loop thread: if it waited for a
    lock, whoever held the lock would stall every listener."""
    done = threading.Event()
    with la._STATUS_LOCK:
        t = threading.Thread(target=lambda: (la._redirect_http(), done.set()), daemon=True)
        t.start()
        finished = done.wait(1.0)
    t.join(2)
    assert finished, "_redirect_http blocked on _STATUS_LOCK"

"""The accept path never depends on creating a thread.

A listener whose accept loop waits for a freshly created per-connection thread
stops serving the moment the OS cannot finish creating one. These tests block
thread creation outright and require the pooled server to keep answering, and
prove the block is real by showing the per-connection-thread server wedges
under it.
"""
from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from agent_friday.services import pooled_server


def _get(port, path="/", accept=None, timeout=5.0):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        hdr = f"GET {path} HTTP/1.0\r\nHost: x\r\n"
        if accept:
            hdr += f"Accept: {accept}\r\n"
        s.sendall((hdr + "\r\n").encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return data
    finally:
        s.close()


def _status(port, **kw):
    return _get(port, **kw).split(b"\r\n", 1)[0]


def _start(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


class _BlockThreadStart:
    """Any threading.Thread.start() blocks until released, as when the OS
    never finishes creating the thread."""

    def __init__(self, monkeypatch):
        self.release = threading.Event()
        real = threading.Thread.start
        release = self.release

        def blocked(thread_self):
            release.wait(30)
            return real(thread_self)

        monkeypatch.setattr(threading.Thread, "start", blocked)


def _ok_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]


def test_per_connection_thread_server_wedges_when_thread_start_blocks(monkeypatch):
    """Control: the model being replaced hangs under the simulated fault, so
    the passing test below is not vacuous."""
    srv = make_server("127.0.0.1", 0, _ok_app, threaded=True)
    port = srv.server_port
    _start(srv)
    time.sleep(0.2)
    block = _BlockThreadStart(monkeypatch)
    try:
        with pytest.raises(OSError):  # socket.timeout: never answered
            _get(port, timeout=1.5)
    finally:
        block.release.set()
        srv.shutdown()
        srv.server_close()


def test_pooled_server_keeps_answering_when_thread_start_blocks(monkeypatch):
    srv = pooled_server.make_pooled_server("127.0.0.1", 0, _ok_app, workers=4)
    port = srv.server_port
    _start(srv)
    time.sleep(0.2)
    block = _BlockThreadStart(monkeypatch)
    try:
        for _ in range(25):
            assert _status(port) == b"HTTP/1.0 200 OK"
    finally:
        block.release.set()
        srv.shutdown()
        srv.server_close()


def test_open_streams_cannot_starve_ordinary_requests():
    stop = threading.Event()

    def app(environ, start_response):
        if environ["PATH_INFO"] == "/events":
            start_response("200 OK", [("Content-Type", "text/event-stream")])

            def gen():
                yield b": hello\n\n"
                while not stop.wait(0.05):
                    pass
            return gen()
        return _ok_app(environ, start_response)

    srv = pooled_server.make_pooled_server(
        "127.0.0.1", 0, app, workers=8, reserved=4)
    port = srv.server_port
    _start(srv)
    time.sleep(0.2)
    held = []
    try:
        results = []
        for _ in range(7):  # cap is 4; three more must be turned away
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.sendall(b"GET /events HTTP/1.0\r\nHost: x\r\n"
                      b"Accept: text/event-stream\r\n\r\n")
            head = s.recv(4096).split(b"\r\n", 1)[0]
            results.append(head)
            held.append(s)
        assert results.count(b"HTTP/1.0 200 OK") == 4, results
        assert results.count(b"HTTP/1.0 503 Service Unavailable") == 3, results
        for _ in range(20):
            assert _status(port) == b"HTTP/1.0 200 OK"
        assert srv.stats.snapshot()["streams_open"] == 4
    finally:
        stop.set()
        for s in held:
            s.close()
        srv.shutdown()
        srv.server_close()


def test_a_finished_stream_frees_its_slot():
    stop = threading.Event()

    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/event-stream")])

        def gen():
            yield b": hello\n\n"
            stop.wait(5)
        return gen()

    srv = pooled_server.make_pooled_server(
        "127.0.0.1", 0, app, workers=4, reserved=2)  # stream cap 2
    port = srv.server_port
    _start(srv)
    try:
        def open_stream():
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.sendall(b"GET /e HTTP/1.0\r\nHost: x\r\n"
                      b"Accept: text/event-stream\r\n\r\n")
            return s, s.recv(4096).split(b"\r\n", 1)[0]

        a, ra = open_stream()
        b, rb = open_stream()
        c, rc = open_stream()
        assert (ra, rb, rc) == (b"HTTP/1.0 200 OK", b"HTTP/1.0 200 OK",
                                b"HTTP/1.0 503 Service Unavailable")
        a.close()
        deadline = time.time() + 5
        # The slot frees when the worker notices the client left (next write).
        stop.set()
        while time.time() < deadline and srv.stats.snapshot()["streams_open"] > 0:
            time.sleep(0.05)
        assert srv.stats.snapshot()["streams_open"] == 0
        b.close()
        c.close()
    finally:
        stop.set()
        srv.shutdown()
        srv.server_close()


def test_a_full_queue_is_answered_with_503_by_the_accept_loop():
    gate = threading.Event()

    def app(environ, start_response):
        gate.wait(10)
        return _ok_app(environ, start_response)

    srv = pooled_server.make_pooled_server(
        "127.0.0.1", 0, app, workers=1, reserved=1, backlog_queue=1)
    port = srv.server_port
    _start(srv)
    socks = []
    try:
        def send():
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            s.sendall(b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
            socks.append(s)
            return s
        send()
        deadline = time.time() + 5
        while srv.stats.snapshot()["busy"] < 1 and time.time() < deadline:
            time.sleep(0.02)
        send()  # queued
        time.sleep(0.3)
        third = send()  # queue full
        assert third.recv(4096).startswith(b"HTTP/1.0 503")
    finally:
        gate.set()
        for s in socks:
            s.close()
        srv.shutdown()
        srv.server_close()


def test_workers_are_started_before_the_first_connection():
    before = {t.name for t in threading.enumerate()}
    srv = pooled_server.make_pooled_server("127.0.0.1", 0, _ok_app, workers=3)
    try:
        started = {t.name for t in threading.enumerate()} - before
        assert {"http-worker-0", "http-worker-1", "http-worker-2"} <= started
    finally:
        srv.server_close()


def test_boot_serves_from_the_pool_not_a_thread_per_connection():
    src = (Path(__file__).resolve().parents[2]
           / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "_pooled.serve(" in src
    assert not re.search(r"app\.run\(\s*host", src)
    assert "threaded=True" not in src

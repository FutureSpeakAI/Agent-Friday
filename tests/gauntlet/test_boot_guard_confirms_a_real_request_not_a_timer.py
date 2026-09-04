"""Gauntlet finding F55 (claim-corpus sweep, 2026-09-04): boot_guard.py's
own module docstring says a known-good state is one that has "actually
completed a startup and then served a request" -- but server.py's
_confirm_boot() thread only ever slept 20 seconds and then called
mark_boot_succeeded() unconditionally, with no check that anything was
ever served. A slow-starting or silently-broken process (every route
500s, startup work still running past the timer) got marked known-good
anyway.

boot_guard.wait_for_health() is the extracted, testable fix: a real
HTTP self-check with retry/backoff, used by server.py's boot-confirmation
thread instead of a bare sleep-then-promote.
"""
from __future__ import annotations

import http.server
import socketserver
import threading
import time

import agent_friday.services.boot_guard as boot_guard


def _serve_once(status_code: int, port_holder: dict):
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status_code)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass  # quiet

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port_holder["port"] = httpd.server_address[1]
    thread = threading.Thread(target=httpd.handle_request, daemon=True)
    thread.start()
    return httpd, thread


class TestBootGuardWaitForHealth:
    def test_a_real_2xx_response_returns_true_immediately(self):
        holder = {}
        httpd, thread = _serve_once(200, holder)
        try:
            ok = boot_guard.wait_for_health(
                f"http://127.0.0.1:{holder['port']}/health",
                attempts=3, initial_delay=0.05, timeout=2)
            assert ok is True
        finally:
            thread.join(timeout=2)
            httpd.server_close()

    def test_connection_refused_every_time_returns_false(self):
        # Nothing is listening on this port.
        ok = boot_guard.wait_for_health(
            "http://127.0.0.1:1/health", attempts=2, initial_delay=0.05, timeout=0.5)
        assert ok is False, (
            "wait_for_health() returned True with nothing listening at all -- "
            "this is the exact 'marked known-good regardless' bug (F55)"
        )

    def test_a_500_response_does_not_count_as_served(self):
        holder = {}
        httpd, thread = _serve_once(500, holder)
        try:
            ok = boot_guard.wait_for_health(
                f"http://127.0.0.1:{holder['port']}/health",
                attempts=1, initial_delay=0.05, timeout=2)
            assert ok is False, (
                "a 500 response was treated as a served request -- a process "
                "where every route errors must not be marked known-good"
            )
        finally:
            thread.join(timeout=2)
            httpd.server_close()

    def test_retries_before_giving_up(self):
        """The retry loop must actually make multiple attempts, not just
        one -- a route registered a beat late (the real-world race this
        exists for) must still succeed within a few tries."""
        calls = {"n": 0}
        real_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen

        def _flaky_urlopen(url, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("not ready yet")
            return real_urlopen(url, timeout=timeout)

        holder = {}
        httpd, thread = _serve_once(200, holder)
        try:
            import urllib.request as ureq
            orig = ureq.urlopen
            ureq.urlopen = _flaky_urlopen
            try:
                ok = boot_guard.wait_for_health(
                    f"http://127.0.0.1:{holder['port']}/health",
                    attempts=5, initial_delay=0.01, timeout=2)
            finally:
                ureq.urlopen = orig
            assert ok is True
            assert calls["n"] >= 3
        finally:
            thread.join(timeout=2)
            httpd.server_close()

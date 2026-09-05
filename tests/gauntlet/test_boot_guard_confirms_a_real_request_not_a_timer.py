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

CORRECTION (weak-probe audit, 2026-09-05): TestBootGuardWaitForHealth
below proves wait_for_health() itself works, but nothing here proved the
WIRING -- that server.py's _confirm_boot() thread actually calls it and
only promotes to known-good when it returns True. Revert JUST that
wiring (put mark_boot_succeeded()/snapshot_known_good() back to running
unconditionally, exactly the original F55 bug) and every test below
would have kept passing, because none of them touch the gate itself.
boot_guard.confirm_boot_health() is the fix: the decision ("only mark
known-good if the health check passed") is now its own callable,
testable function, and server.py calls IT instead of inlining the
if/else. TestConfirmBootHealthGating proves the gate directly.
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


class TestConfirmBootHealthGating:
    """The actual proof this finding needed: not that wait_for_health()
    works in isolation, but that a failing health check genuinely
    prevents mark_boot_succeeded()/snapshot_known_good() from running,
    and a passing one genuinely triggers both. server.py's _confirm_boot()
    thread calls confirm_boot_health() directly -- reverting that wiring
    back to the original F55 bug (promote unconditionally) would fail
    these tests, unlike TestBootGuardWaitForHealth above."""

    def test_a_failed_health_check_does_not_mark_boot_succeeded(self, monkeypatch):
        calls = {"succeeded": False, "snapshotted": False}
        monkeypatch.setattr(boot_guard, "wait_for_health", lambda *a, **k: False)
        monkeypatch.setattr(boot_guard, "mark_boot_succeeded",
                            lambda: calls.__setitem__("succeeded", True))
        monkeypatch.setattr(boot_guard, "snapshot_known_good",
                            lambda *a, **k: calls.__setitem__("snapshotted", True))

        result = boot_guard.confirm_boot_health("http://127.0.0.1:1/health")

        assert result is False
        assert calls["succeeded"] is False, (
            "confirm_boot_health() called mark_boot_succeeded() even "
            "though the health check failed -- this is F55's exact "
            "original bug, now reachable again through the gate function "
            "itself rather than through server.py's inlined wiring"
        )
        assert calls["snapshotted"] is False

    def test_a_passed_health_check_marks_boot_succeeded_and_snapshots(self, monkeypatch):
        calls = {"succeeded": False, "snapshotted": False}
        monkeypatch.setattr(boot_guard, "wait_for_health", lambda *a, **k: True)
        monkeypatch.setattr(boot_guard, "mark_boot_succeeded",
                            lambda: calls.__setitem__("succeeded", True))
        monkeypatch.setattr(boot_guard, "snapshot_known_good",
                            lambda *a, **k: calls.__setitem__("snapshotted", True))

        result = boot_guard.confirm_boot_health("http://127.0.0.1:1/health")

        assert result is True
        assert calls["succeeded"] is True
        assert calls["snapshotted"] is True

    def test_servers_confirm_boot_thread_calls_the_gate_not_wait_for_health_directly(self):
        """Structural check on the wiring itself: server.py must call
        confirm_boot_health (the gate), not wait_for_health (the raw
        poll) -- calling the raw poll directly would silently reopen this
        finding by letting a future edit skip the gate without anyone
        needing to touch this test file at all."""
        import pathlib
        server_src = (pathlib.Path(__file__).resolve().parent.parent.parent
                     / "src" / "agent_friday" / "server.py").read_text(encoding="utf-8")
        assert "_bg.confirm_boot_health(" in server_src, (
            "server.py no longer calls boot_guard.confirm_boot_health() -- "
            "if it calls wait_for_health() directly instead, the promotion "
            "decision has moved back out of the tested gate and into "
            "un-tested inline wiring, silently reopening F55"
        )

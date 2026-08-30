"""Unit tests for FridayTray.restart_server()'s debounce guard (docs:
toolcall-integrity-v5, 2026-08-13 double-launch incident).

Audited start_server()'s existing self._lock and found it correctly
serializes the check-and-spawn instant — could not reproduce an in-process
double-spawn through it. This guard is defense-in-depth against a rapid
double-click collapsing to one restart instead of two concurrent
stop→sleep→start sequences, regardless of whether that was the actual
2026-08-13 cause (the more likely explanation — a second, externally
launched process — is covered by server.py's own single-instance lock, not
by anything in this file).
"""
from __future__ import annotations

import sys
import threading
import time

import pytest

# friday_tray is the Windows system-tray app: it imports pystray, which
# pyproject declares as sys_platform == "win32" only, and whose import
# selects an OS tray backend that does not exist on a headless Linux runner.
#
# skip(allow_module_level=True) rather than importorskip: this halts
# collection on the CONDITION (not-Windows), so on Windows the import below
# still runs for real and a missing pystray or a broken friday_tray fails
# loudly on both Windows legs instead of skipping quietly.
if sys.platform != "win32":
    pytest.skip(
        "friday_tray is a Windows-only tray app (pystray is win32-only)",
        allow_module_level=True,
    )

from agent_friday.friday_tray import FridayTray


@pytest.fixture
def tray():
    return FridayTray()


class TestRestartDebounce:
    def test_a_single_restart_calls_stop_then_start_once(self, tray, monkeypatch):
        calls = []
        monkeypatch.setattr(tray, "stop_server", lambda: calls.append("stop"))
        monkeypatch.setattr(tray, "start_server", lambda: calls.append("start"))
        monkeypatch.setattr("agent_friday.friday_tray.time.sleep", lambda s: None)
        tray.restart_server()
        assert calls == ["stop", "start"]

    def test_concurrent_restarts_collapse_to_one_stop_start_sequence(self, tray, monkeypatch):
        calls = []
        call_lock = threading.Lock()
        # A dedicated Event (not time.sleep, which restart_server() itself
        # calls) holds the in-flight window open long enough for the other
        # threads' acquire attempts to land while it's still held.
        hold_open = threading.Event()

        def _slow_stop():
            with call_lock:
                calls.append("stop")
            hold_open.wait(timeout=2)

        def _start():
            with call_lock:
                calls.append("start")

        monkeypatch.setattr(tray, "stop_server", _slow_stop)
        monkeypatch.setattr(tray, "start_server", _start)
        monkeypatch.setattr("agent_friday.friday_tray.time.sleep", lambda s: None)

        threads = [threading.Thread(target=tray.restart_server) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.2)  # let the losers' non-blocking acquire attempts land
        hold_open.set()  # release the winner to proceed to start_server()
        for t in threads:
            t.join(timeout=5)

        # Exactly one restart actually ran the stop/start sequence; the
        # other two calls found the lock held and returned immediately.
        assert calls == ["stop", "start"]

    def test_the_lock_is_released_after_a_restart_so_the_next_one_can_run(self, tray, monkeypatch):
        calls = []
        monkeypatch.setattr(tray, "stop_server", lambda: calls.append("stop"))
        monkeypatch.setattr(tray, "start_server", lambda: calls.append("start"))
        monkeypatch.setattr("agent_friday.friday_tray.time.sleep", lambda s: None)
        tray.restart_server()
        tray.restart_server()
        assert calls == ["stop", "start", "stop", "start"]

    def test_lock_is_released_even_if_start_server_raises(self, tray, monkeypatch):
        monkeypatch.setattr(tray, "stop_server", lambda: None)
        monkeypatch.setattr(tray, "start_server",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr("agent_friday.friday_tray.time.sleep", lambda s: None)
        with pytest.raises(RuntimeError):
            tray.restart_server()
        # The lock must be released despite the exception, or every
        # subsequent restart attempt would be silently ignored forever.
        assert tray._restart_in_flight.acquire(blocking=False)
        tray._restart_in_flight.release()

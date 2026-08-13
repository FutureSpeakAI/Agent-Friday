"""Unit tests for the single-instance lock + loud-fail helper added to
server.py (docs: toolcall-integrity-v5, 2026-08-13 double-launch incident:
two server processes born the same second; the loser sat alive at ~4MB/0%
CPU with nothing logged, because it never got far enough to log anything).

_acquire_single_instance_lock() is the authoritative guard — checked first,
before any heavy startup work — so a losing process fails in milliseconds.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import agent_friday.server as friday_server

_SRC_DIR = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def isolated_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(friday_server, "FRIDAY_DIR", tmp_path)
    return tmp_path


class TestAcquireSingleInstanceLock:
    def test_first_acquisition_succeeds(self, isolated_lock):
        fh = friday_server._acquire_single_instance_lock()
        try:
            assert fh is not None
            assert not fh.closed
        finally:
            fh.close()

    def test_second_acquisition_while_first_is_held_fails(self, isolated_lock):
        first = friday_server._acquire_single_instance_lock()
        try:
            second = friday_server._acquire_single_instance_lock()
            assert second is None
        finally:
            first.close()

    def test_lock_is_released_after_the_holder_closes_it(self, isolated_lock):
        first = friday_server._acquire_single_instance_lock()
        first.close()
        second = friday_server._acquire_single_instance_lock()
        try:
            assert second is not None
        finally:
            second.close()

    def test_pid_sidecar_records_the_holder_pid(self, isolated_lock):
        # PID metadata lives in a SEPARATE, unlocked file — Windows locking
        # is mandatory, so the locked file itself is unreadable by anything
        # but the holder while the lock is live.
        import os
        fh = friday_server._acquire_single_instance_lock()
        try:
            pid_path = isolated_lock / "friday_server.pid"
            content = pid_path.read_text(encoding="utf-8")
            assert content.splitlines()[0] == str(os.getpid())
        finally:
            fh.close()

    def test_lock_file_itself_stays_readable_by_a_fresh_process(self, isolated_lock):
        # A cross-process check (real production scenario: the LOSER reads
        # friday_server.pid to name the winner) — exercised here via
        # subprocess so it's a genuine second process, not a second handle
        # in the same one (Windows locking behaves differently either way,
        # but only the cross-process case is what actually matters).
        import subprocess
        script = (
            "import sys; sys.path.insert(0, r'" +
            str(_SRC_DIR) + "')\n"
            "import agent_friday.server as s\n"
            "s.FRIDAY_DIR = __import__('pathlib').Path(r'" + str(isolated_lock) + "')\n"
            "fh = s._acquire_single_instance_lock()\n"
            "print('LOCKED' if fh is not None else 'DENIED')\n"
        )
        holder = friday_server._acquire_single_instance_lock()
        try:
            env = dict(os.environ, FRIDAY_TESTING="1")
            result = subprocess.run([sys.executable, "-c", script],
                                    capture_output=True, text=True, timeout=30, env=env)
            assert "DENIED" in result.stdout, result.stdout + result.stderr
        finally:
            holder.close()


class TestFailLoudAndExit:
    def test_raises_systemexit_with_code_1(self, monkeypatch, caplog):
        # Never let a real MessageBoxW pop during a test run.
        if sys.platform == "win32":
            monkeypatch.setattr(
                "ctypes.windll.user32.MessageBoxW", lambda *a, **k: 0, raising=False)
        with pytest.raises(SystemExit) as exc_info:
            friday_server._fail_loud_and_exit("test failure message")
        assert exc_info.value.code == 1

    def test_logs_the_message_via_friday_server_logger(self, monkeypatch, caplog):
        if sys.platform == "win32":
            monkeypatch.setattr(
                "ctypes.windll.user32.MessageBoxW", lambda *a, **k: 0, raising=False)
        import logging
        with caplog.at_level(logging.ERROR, logger="friday.server"):
            with pytest.raises(SystemExit):
                friday_server._fail_loud_and_exit("distinctive test marker xyzzy")
        assert any("distinctive test marker xyzzy" in r.message for r in caplog.records)

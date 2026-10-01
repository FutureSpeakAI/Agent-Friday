"""server_stderr.log has a size cap: past it, the log rotates into numbered
backups and the live file starts again empty, while the server keeps writing.

The server's stdout and stderr go to a file the tray opens and the child
inherits. On Windows a file held open by another process cannot be renamed,
so the live file is rotated by copy-then-truncate, and the shared handle is
append-only at the OS level: every write lands at the current end of the file,
so a truncated log never fills with zeros up to the writer's old offset.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest


def _wait_for(pred, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_a_writer_over_the_cap_rotates_and_keeps_appending(tmp_path):
    from agent_friday.services import capped_log
    log = tmp_path / "server_stderr.log"
    f = capped_log.open_shared_append(log)
    try:
        f.write(b"A" * 300)
        assert capped_log.rotate_if_over(log, max_bytes=100, backups=3) is True
        f.write(b"B" * 10)
    finally:
        f.close()
    assert log.read_bytes() == b"B" * 10
    assert (tmp_path / "server_stderr.log.1").read_bytes() == b"A" * 300


def test_under_the_cap_nothing_rotates(tmp_path):
    from agent_friday.services import capped_log
    log = tmp_path / "server_stderr.log"
    log.write_bytes(b"x" * 50)
    assert capped_log.rotate_if_over(log, max_bytes=100, backups=3) is False
    assert log.read_bytes() == b"x" * 50
    assert not (tmp_path / "server_stderr.log.1").exists()


def test_backups_are_numbered_and_capped(tmp_path):
    from agent_friday.services import capped_log
    log = tmp_path / "server_stderr.log"
    for gen in "abcde":
        log.write_bytes(gen.encode() * 200)
        assert capped_log.rotate_if_over(log, max_bytes=100, backups=3)
    assert (tmp_path / "server_stderr.log.1").read_bytes() == b"e" * 200
    assert (tmp_path / "server_stderr.log.2").read_bytes() == b"d" * 200
    assert (tmp_path / "server_stderr.log.3").read_bytes() == b"c" * 200
    assert not (tmp_path / "server_stderr.log.4").exists()
    assert log.read_bytes() == b""


_CHILD = r"""
import os, sys, time
flag = sys.argv[1]
sys.stdout.buffer.write(b"X" * 400); sys.stdout.buffer.flush()
deadline = time.monotonic() + 20
while not os.path.exists(flag) and time.monotonic() < deadline:
    time.sleep(0.02)
sys.stdout.buffer.write(b"Y" * 10); sys.stdout.buffer.flush()
"""


def test_a_child_writing_through_the_inherited_handle_survives_a_rotation(tmp_path):
    """The tray's case: the server inherits the handle and keeps writing."""
    from agent_friday.services import capped_log
    log = tmp_path / "server_stderr.log"
    flag = tmp_path / "go"
    f = capped_log.open_shared_append(log)
    try:
        proc = subprocess.Popen([sys.executable, "-c", _CHILD, str(flag)],
                                stdout=f, stderr=subprocess.STDOUT)
        try:
            assert _wait_for(lambda: log.stat().st_size >= 400)
            assert capped_log.rotate_if_over(log, max_bytes=100, backups=3)
            flag.write_text("go")
            assert proc.wait(30) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
    finally:
        f.close()
    assert log.read_bytes() == b"Y" * 10, "the live log after rotation: %r" % log.read_bytes()[:40]
    assert (tmp_path / "server_stderr.log.1").read_bytes() == b"X" * 400


@pytest.mark.skipif(sys.platform != "win32", reason="the tray is Windows-only")
def test_the_tray_rotates_an_oversized_log_before_starting_the_server(tmp_path, monkeypatch):
    import agent_friday.friday_tray as ft

    log = tmp_path / "server_stderr.log"
    log.write_bytes(b"old" * 1000)
    monkeypatch.setattr(ft, "SERVER_STDERR_LOG", log)
    monkeypatch.setattr(ft, "SERVER_STDERR_LOG_MAX_BYTES", 1000, raising=False)
    monkeypatch.setattr(ft, "_port_in_use", lambda _p: False)
    monkeypatch.setattr(ft, "clear_server_port", lambda: None)
    monkeypatch.setattr(ft, "_wait_for_health", lambda proc=None: (True, "healthy"))

    class _Proc:
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(ft.subprocess, "Popen", lambda *a, **k: _Proc())
    tray = ft.FridayTray()
    monkeypatch.setattr(tray, "_refresh_menu", lambda: None)
    tray.start_server()
    try:
        assert (tmp_path / "server_stderr.log.1").exists(), \
            "an oversized server_stderr.log was not rotated at server start"
        assert log.stat().st_size < 1000
    finally:
        if tray._child_err is not None:
            tray._child_err.close()


@pytest.mark.skipif(sys.platform != "win32", reason="the tray is Windows-only")
def test_the_tray_watchdog_rotates_the_live_log(tmp_path, monkeypatch):
    import agent_friday.friday_tray as ft

    log = tmp_path / "server_stderr.log"
    log.write_bytes(b"live" * 1000)
    monkeypatch.setattr(ft, "SERVER_STDERR_LOG", log)
    monkeypatch.setattr(ft, "SERVER_STDERR_LOG_MAX_BYTES", 1000, raising=False)
    monkeypatch.setattr(ft, "_port_in_use", lambda _p: True)

    class _Stop(Exception):
        pass

    ticks = {"n": 0}

    def _sleep(_s):
        ticks["n"] += 1
        if ticks["n"] > 1:
            raise _Stop()

    monkeypatch.setattr(ft.time, "sleep", _sleep)
    tray = ft.FridayTray()
    tray.running = True
    # The server's handle is the append-only one start_server opens.
    tray._log_rotatable = True
    monkeypatch.setattr(tray, "_update_meeting_title", lambda: None)
    with pytest.raises(_Stop):
        tray._watchdog()
    assert (tmp_path / "server_stderr.log.1").exists(), \
        "the watchdog left an oversized live log unrotated"

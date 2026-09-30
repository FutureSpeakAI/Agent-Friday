"""The watchdog notices a server that is listening but not answering, and heals
it once, with a receipt."""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from agent_friday.services import hang_watchdog as hw
from agent_friday.services import pooled_server, thread_caps


@pytest.fixture
def wedged_port():
    """LISTEN with a backlog and no accept loop: connections complete in the
    kernel, nothing ever answers."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(16)
    yield s.getsockname()[1]
    s.close()


def _stub_forensics(logs_dir, port, reason):
    p = logs_dir / "wedge-forensics-stub.txt"
    p.write_text(reason, encoding="utf-8")
    return p


def test_probe_once_fails_on_a_listener_that_never_answers(wedged_port):
    t0 = time.monotonic()
    ok, detail = hw.probe_once("127.0.0.1", wedged_port, 0.4)
    assert not ok and "no answer" in detail
    assert time.monotonic() - t0 < 2


def test_probe_once_passes_against_a_serving_server():
    srv = pooled_server.make_pooled_server(
        "127.0.0.1", 0, lambda e, sr: (sr("200 OK", []), [b"x"])[1], workers=2)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ok, detail = hw.probe_once("127.0.0.1", srv.server_port, 2)
        assert ok and detail.startswith("HTTP/1.0 200")
    finally:
        srv.shutdown()
        srv.server_close()


def test_wedged_accept_is_detected_within_the_window_and_heals_exactly_once(
        wedged_port, tmp_path):
    healed = []
    notes = []
    probe = hw.AcceptProbe(
        wedged_port, interval_s=0.05, timeout_s=0.2, failures_to_heal=4,
        heal=healed.append, notify=notes.append, logs_dir=tmp_path,
        forensics=_stub_forensics, max_heals_per_hour=1)
    t0 = time.monotonic()
    probe.start()
    try:
        while not healed and time.monotonic() - t0 < 10:
            time.sleep(0.02)
        elapsed = time.monotonic() - t0
        # 4 misses at (0.05 wait + 0.2 timeout) each, plus slack.
        assert healed, "wedged accept was never detected"
        assert elapsed < 4 * (0.05 + 0.2) + 2
        time.sleep(1.0)  # a still-wedged listener must not be healed again
        assert len(healed) == 1
        assert probe.heals == 1
    finally:
        probe.stop()
    receipt = json.loads(next(tmp_path.glob("wedge-heal-*.json")).read_text("utf-8"))
    assert receipt["kind"] == "wedged-server-heal"
    assert receipt["port"] == wedged_port
    assert "4 consecutive" in receipt["reason"]
    assert receipt["forensics"].endswith("wedge-forensics-stub.txt")
    assert "llama_server" in receipt and "left running" in receipt["llama_server"]
    assert healed[0]["receipt_path"].endswith(".json")
    assert len(notes) == 1 and "restarting" in notes[0]


def test_a_healthy_server_is_never_healed(tmp_path):
    healed = []
    probe = hw.AcceptProbe(1, probe=lambda: (True, "HTTP/1.0 200 OK"),
                           failures_to_heal=2, heal=healed.append,
                           logs_dir=tmp_path)
    for _ in range(10):
        assert probe.check_once() is False
    assert healed == [] and not list(tmp_path.glob("wedge-heal-*"))


def test_a_slow_server_that_is_still_completing_requests_is_not_healed(tmp_path):
    done = [0]
    healed = []

    def progress():
        done[0] += 1
        return done[0]

    probe = hw.AcceptProbe(1, probe=lambda: (False, "timed out"),
                           progress_fn=progress, failures_to_heal=2,
                           heal=healed.append, logs_dir=tmp_path,
                           forensics=_stub_forensics)
    for _ in range(10):
        probe.check_once()
    assert healed == []


def test_misses_without_progress_heal(tmp_path):
    healed = []
    probe = hw.AcceptProbe(1, probe=lambda: (False, "timed out"),
                           progress_fn=lambda: 7, failures_to_heal=3,
                           heal=healed.append, logs_dir=tmp_path,
                           forensics=_stub_forensics)
    results = [probe.check_once() for _ in range(4)]
    # first call has no baseline yet, so it counts; the 3rd miss heals.
    assert results == [False, False, True, False]
    assert len(healed) == 1


def test_heals_are_capped_per_hour(tmp_path):
    healed = []
    probe = hw.AcceptProbe(1, probe=lambda: (False, "x"), failures_to_heal=1,
                           heal=healed.append, logs_dir=tmp_path,
                           forensics=_stub_forensics, max_heals_per_hour=2)
    for _ in range(6):
        probe.check_once()
    assert len(healed) == 2


def test_default_heal_exits_with_the_code_the_tray_restarts_on(monkeypatch):
    import os
    codes = []
    monkeypatch.setattr(os, "_exit", codes.append)
    hw.AcceptProbe._exit_for_restart({})
    assert codes == [75] == [hw.EXIT_WEDGED]


def test_forensics_records_threads_ports_and_native_stack_status(tmp_path, monkeypatch, wedged_port):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    p = hw.capture_forensics(tmp_path, wedged_port, "unit test")
    text = p.read_text(encoding="utf-8")
    assert "python_threads:" in text and "os_threads:" in text
    assert "== port table ==" in text and str(wedged_port) in text
    assert "== python stacks ==" in text
    assert "py-spy dump --native" in text and "not installed" in text


def test_forensics_runs_py_spy_native_when_available(tmp_path, monkeypatch):
    import shutil
    import subprocess
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "NATIVE-STACK-MARK", "")

    monkeypatch.setattr(shutil, "which", lambda _n: "py-spy")
    monkeypatch.setattr(subprocess, "run", fake_run)
    p = hw.capture_forensics(tmp_path, 1, "unit test")
    assert seen and seen[0][:3] == ["py-spy", "dump", "--native"]
    assert "NATIVE-STACK-MARK" in p.read_text(encoding="utf-8")


def test_status_reports_os_and_python_thread_counts():
    st = hw.HangWatchdog(logs_dir=None).status()
    assert isinstance(st["python_threads"], int)
    assert st["os_threads"] is None or st["os_threads"] >= st["python_threads"]


def test_thread_caps_set_only_what_is_unset():
    env = {"OMP_NUM_THREADS": "3"}
    set_now = thread_caps.apply(env)
    assert env["OMP_NUM_THREADS"] == "3" and "OMP_NUM_THREADS" not in set_now
    for name in thread_caps.CAPPED_VARS:
        assert 1 <= int(env[name]) <= 8
    assert thread_caps.apply(env) == {}


def test_thread_caps_run_before_the_heavy_imports_at_boot():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday"
           / "server.py").read_text(encoding="utf-8")
    assert src.index("_thread_caps.apply()") < src.index("import agent_friday.core as core")


def test_tray_restarts_only_on_the_wedge_exit_code(monkeypatch):
    pytest.importorskip("pystray")
    from agent_friday import friday_tray as ft
    assert ft._is_wedge_exit(hw.EXIT_WEDGED)
    assert not ft._is_wedge_exit(1) and not ft._is_wedge_exit(None)
    tray = ft.FridayTray.__new__(ft.FridayTray)
    tray.icon = None
    restarts = []
    monkeypatch.setattr(tray, "restart_server", lambda: restarts.append(1))
    for _ in range(ft.WEDGE_HEALS_PER_HOUR + 2):
        tray._heal_wedged(hw.EXIT_WEDGED)
    deadline = time.time() + 3
    while len(restarts) < ft.WEDGE_HEALS_PER_HOUR and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    assert len(restarts) == ft.WEDGE_HEALS_PER_HOUR

"""The tray finds the server on the port the server uses, not on a fixed 3000.

The server honours FRIDAY_PORT and falls back to the next free port when the
requested one is busy, and records the port it bound. The tray's health
check, "Open Friday Desktop", watchdog and push-to-transcribe must all follow
that port.
"""
from __future__ import annotations

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("friday_tray imports pystray, a Windows-only dependency",
                allow_module_level=True)

import agent_friday.friday_tray as ft
import agent_friday.paths as paths


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.delenv("FRIDAY_PORT", raising=False)
    return tmp_path


def test_default_is_3000(home):
    assert ft._port() == 3000
    assert ft._health_url() == "http://localhost:3000/api/health"


def test_friday_port_is_honoured(home, monkeypatch):
    monkeypatch.setenv("FRIDAY_PORT", "3456")
    assert ft._port() == 3456
    assert ft.PushToTranscribe().server_url == "http://localhost:3456"


def test_the_port_the_server_bound_wins(home, monkeypatch):
    monkeypatch.setenv("FRIDAY_PORT", "3000")
    paths.write_server_port(3002)
    assert (home / "friday_server.port").read_text(encoding="utf-8").strip() == "3002"
    assert ft._port() == 3002
    assert ft._health_url() == "http://localhost:3002/api/health"


def test_a_corrupt_port_file_falls_back_to_the_configured_port(home, monkeypatch):
    monkeypatch.setenv("FRIDAY_PORT", "3100")
    (home / "friday_server.port").write_text("not a port", encoding="utf-8")
    assert ft._port() == 3100


def test_watchdog_probes_the_resolved_port(home, monkeypatch):
    paths.write_server_port(3007)
    probed = []

    def fake_in_use(port):
        probed.append(port)
        raise SystemExit  # stop the loop after one probe

    monkeypatch.setattr(ft, "_port_in_use", fake_in_use)
    monkeypatch.setattr(ft.time, "sleep", lambda _s: None)
    tray = ft.FridayTray()
    tray.server_proc = None
    with pytest.raises(SystemExit):
        tray._watchdog()
    assert probed == [3007]


def test_the_server_resolves_its_port_from_the_same_source():
    """server._resolve_bind_port reads FRIDAY_PORT through paths, like the tray."""
    from pathlib import Path
    src = (Path(ft.__file__).parent / "server.py").read_text(encoding="utf-8")
    assert "configured_server_port()" in src
    assert "write_server_port(_port)" in src

"""No process under the test run may start a tunnel or bind a public host.

A unit test once approved a publish card, and the code it exercised launched
a real static server and a real cloudflared quick tunnel from every test
worker, exposing temporary folders on public addresses with no approval.
The suite's root conftest now installs two guards in every test process:
process creation refuses cloudflared (and any other tunnel binary named
below), and a socket refuses to bind to anything but loopback. These tests
prove the guards are live in this process, so the class of bug cannot come
back quietly.
"""
from __future__ import annotations

import socket
import subprocess

import pytest

from tests import exposure_guard


def test_the_guards_are_installed_in_this_process():
    assert getattr(subprocess.Popen.__init__, "__friday_no_tunnel__", False), \
        "conftest did not wrap subprocess.Popen"
    assert getattr(socket.socket.bind, "__friday_loopback_only__", False), \
        "conftest did not wrap socket.bind"


@pytest.mark.parametrize("argv", [
    ["cloudflared", "tunnel", "--url", "http://127.0.0.1:1"],
    [r"C:\Program Files (x86)\cloudflared\cloudflared.exe", "tunnel"],
    ["/usr/local/bin/cloudflared", "--version"],
    ["ngrok", "http", "80"],
    ["caddy", "run"],
    ["tailscale", "funnel", "80"],
])
def test_no_tunnel_binary_can_be_started(argv):
    with pytest.raises(exposure_guard.PublicExposureRefused):
        subprocess.Popen(argv)


def test_a_shell_command_naming_a_tunnel_is_refused_too():
    with pytest.raises(exposure_guard.PublicExposureRefused):
        subprocess.Popen("cloudflared tunnel --url http://127.0.0.1:1", shell=True)


def test_ordinary_processes_still_run():
    import sys
    out = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "ok"


@pytest.mark.parametrize("host", ["0.0.0.0", "", "::", "192.168.1.10", "10.0.0.5"])
def test_no_socket_can_bind_a_public_host(host):
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    try:
        with pytest.raises(exposure_guard.PublicExposureRefused):
            s.bind((host, 0))
    finally:
        s.close()


def test_loopback_binds_still_work():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        assert s.getsockname()[1] > 0
    finally:
        s.close()
    s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        s6.bind(("::1", 0))
    except OSError:
        pytest.skip("no IPv6 loopback here")
    finally:
        s6.close()


def test_the_hosting_manager_cannot_reach_cloudflared_even_when_a_test_allows_starting(monkeypatch):
    """Defence in depth: the hosting tests stub `_spawn`; if one forgot, the
    process guard still stops the real binary."""
    from agent_friday.services import publish_hosting as ph
    monkeypatch.setattr(ph, "ALLOW_UNDER_TEST", True)
    with pytest.raises(exposure_guard.PublicExposureRefused):
        ph._spawn([r"C:\Program Files (x86)\cloudflared\cloudflared.exe", "tunnel", "--url", "http://127.0.0.1:1"])

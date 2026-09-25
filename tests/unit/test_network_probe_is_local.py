"""The 30-second connectivity probe contacts no third party by default.

By default it asks the routing table whether a non-loopback route exists,
which sends no packet. Probing public DNS resolvers is an opt-in
(`network_probe: "internet"`).
"""
from __future__ import annotations

import socket

import pytest

import agent_friday.core as core
from agent_friday.services import notifications as n


def _settings(monkeypatch, **values):
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: dict(values))


@pytest.fixture
def no_tcp(monkeypatch):
    calls = []

    def refuse(addr, *a, **k):
        calls.append(addr)
        raise OSError("blocked in test")
    monkeypatch.setattr(socket, "create_connection", refuse)
    return calls


def test_default_probe_opens_no_connection(monkeypatch, no_tcp):
    _settings(monkeypatch)
    assert core.DEFAULT_SETTINGS["network_probe"] == "route"
    sent = []
    real_socket = socket.socket

    class Recording(real_socket):
        def connect(self, addr):
            sent.append((self.type, addr))
            return super().connect(addr)

        def send(self, *a, **k):                # pragma: no cover - must not run
            raise AssertionError("the route probe sent data")

        sendto = send
    monkeypatch.setattr(socket, "socket", Recording)

    ok, latency, host = n._network_probe()

    assert no_tcp == []
    assert all(t == socket.SOCK_DGRAM for t, _ in sent)
    assert all(a[0] in ("192.0.2.1", "2001:db8::1") for _, a in sent)
    assert isinstance(ok, bool) and latency is None and host == "local route"


def test_internet_probe_is_opt_in(monkeypatch, no_tcp):
    _settings(monkeypatch, network_probe="internet")
    ok, _latency, _host = n._network_probe()
    assert ok is False
    assert no_tcp and no_tcp[0][0] == "dns.google"


def test_probe_can_be_turned_off(monkeypatch, no_tcp):
    _settings(monkeypatch, network_probe="off")
    assert n._network_probe() == (True, None, "not checked")
    assert no_tcp == []

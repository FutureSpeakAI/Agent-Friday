"""Hosting "This PC" (docs/design/active/vibe-coding-salon.md §4.10.1).

The manager starts the separate static server and a cloudflared quick tunnel
that points ONLY at it, learns the tunnel's hostname, reports whether pages
are reachable right now, and has one switch that takes every published page
offline at once. Nothing here proxies to Friday, and nothing runs when the
switch is off.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import publish_hosting as ph


class FakeProc:
    def __init__(self, argv, lines=()):
        self.argv = argv
        self.pid = 4242 + len(argv)
        self._alive = True
        self._lines = list(lines)
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0


@pytest.fixture
def world(monkeypatch, tmp_path):
    spawned = []

    def spawn(argv, **kw):
        lines = []
        if "cloudflared" in str(argv[0]).lower():
            lines = ["2026-09-30T01:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...",
                     "2026-09-30T01:00:01Z INF +--------------------------------------+",
                     "2026-09-30T01:00:01Z INF |  https://quiet-otter-42.trycloudflare.com  |",
                     "2026-09-30T01:00:01Z INF +--------------------------------------+"]
        p = FakeProc(argv, lines)
        spawned.append(p)
        return p

    monkeypatch.setattr(ph, "_spawn", spawn)
    monkeypatch.setattr(ph, "_read_tunnel_lines", lambda proc, timeout: list(proc._lines))
    monkeypatch.setattr(ph, "_cloudflared_path", lambda: r"C:\Program Files (x86)\cloudflared\cloudflared.exe")
    monkeypatch.setattr(ph, "_state_path", lambda: tmp_path / "publish-this-pc.json")
    monkeypatch.setattr(ph, "_published_root", lambda: tmp_path / "published")
    monkeypatch.setattr(ph, "_free_port", lambda: 48123)
    probes = {"result": True}
    monkeypatch.setattr(ph, "_probe", lambda url, timeout=4.0: probes["result"])
    settings = {"publish_this_pc_enabled": True}
    monkeypatch.setattr(ph, "_settings", lambda: settings)
    ph._reset_for_tests()
    # Every process here is a fake; only then may the manager "start".
    monkeypatch.setattr(ph, "ALLOW_UNDER_TEST", True)
    yield {"spawned": spawned, "settings": settings, "probes": probes, "tmp": tmp_path}
    ph.stop()


def test_starting_runs_the_static_server_and_a_tunnel_that_points_only_at_it(world):
    st = ph.start()
    server, tunnel = world["spawned"]
    assert "published_server.py" in " ".join(map(str, server.argv))
    assert "--root" in server.argv and str(world["tmp"] / "published") in server.argv
    assert "--port" in server.argv and "48123" in server.argv and "--bind" in server.argv and "127.0.0.1" in server.argv
    joined = " ".join(map(str, tunnel.argv))
    assert "cloudflared" in joined.lower() and "--url" in tunnel.argv and "http://127.0.0.1:48123" in tunnel.argv
    assert "--no-autoupdate" in tunnel.argv
    assert "3000" not in joined and "localhost" not in joined, "the tunnel never points at Friday"
    assert st["serving"] is True and st["url"] == "https://quiet-otter-42.trycloudflare.com"
    assert st["port"] == 48123 and st["enabled"] is True
    assert ph.public_base_url() == "https://quiet-otter-42.trycloudflare.com"


def test_the_state_is_written_beside_not_inside_the_served_folder(world):
    ph.start()
    p = world["tmp"] / "publish-this-pc.json"
    assert p.exists()
    d = json.loads(p.read_text())
    assert d["url"].startswith("https://") and d["port"] == 48123
    assert not (world["tmp"] / "published" / "publish-this-pc.json").exists()


def test_status_reports_reachability_honestly(world):
    ph.start()
    assert ph.status()["reachable"] is True
    world["probes"]["result"] = False
    assert ph.status(refresh=True)["reachable"] is False
    assert "not reachable" in ph.status_line().lower() or "unreachable" in ph.status_line().lower()
    world["probes"]["result"] = True
    line = ph.status_line(refresh=True)
    assert "quiet-otter-42.trycloudflare.com" in line and "reachable" in line.lower()


def test_the_kill_switch_stops_both_processes_and_nothing_restarts(world):
    ph.start()
    server, tunnel = world["spawned"]
    ph.set_enabled(False)
    assert server.poll() is not None and tunnel.poll() is not None
    st = ph.status()
    assert st["enabled"] is False and st["serving"] is False and st["url"] is None
    assert ph.public_base_url() is None
    assert world["settings"]["publish_this_pc_enabled"] is False
    # Off means off: a start request is refused while the switch is down.
    assert ph.start()["serving"] is False
    assert len(world["spawned"]) == 2
    assert "offline" in ph.status_line().lower()


def test_the_switch_back_on_starts_again(world):
    ph.set_enabled(False)
    ph.set_enabled(True)
    assert ph.status()["serving"] is True
    assert world["settings"]["publish_this_pc_enabled"] is True


def test_a_dead_server_process_is_reported_not_hidden(world):
    ph.start()
    server, _tunnel = world["spawned"]
    server._alive = False
    st = ph.status(refresh=True)
    assert st["serving"] is False
    assert "not running" in ph.status_line(refresh=True).lower()


def test_ensure_started_is_idempotent(world):
    ph.ensure_started()
    ph.ensure_started()
    assert len(world["spawned"]) == 2


def test_without_cloudflared_pages_serve_locally_and_the_line_says_so(world, monkeypatch):
    monkeypatch.setattr(ph, "_cloudflared_path", lambda: None)
    st = ph.start()
    assert st["serving"] is True and st["url"] == "http://127.0.0.1:48123"
    assert st["tunnel"] is False
    assert "this pc only" in ph.status_line().lower() or "no tunnel" in ph.status_line().lower()


def test_the_tunnel_can_be_switched_off_to_keep_pages_on_this_pc(world):
    world["settings"]["publish_this_pc_tunnel"] = False
    st = ph.start()
    assert len(world["spawned"]) == 1, "no cloudflared process when the tunnel is off"
    assert st["serving"] is True and st["tunnel"] is False
    assert st["url"] == "http://127.0.0.1:48123"
    assert "switched off" in ph.status_line().lower()


def test_nothing_is_spawned_under_a_test_run_unless_the_test_says_so(world, monkeypatch):
    """A unit test that approves a publish card once launched a real static
    server AND a real cloudflared quick tunnel per test worker, exposing temp
    folders publicly with no approval. Under pytest the manager spawns nothing
    unless the test has stubbed the processes and said so."""
    monkeypatch.setattr(ph, "ALLOW_UNDER_TEST", False)
    st = ph.start()
    assert world["spawned"] == []
    assert st["serving"] is False and st["url"] is None
    ph.ensure_started()
    assert world["spawned"] == []


def test_remote_adapters_are_not_connected_until_a_token_is_stored(world, monkeypatch):
    monkeypatch.setattr(ph, "_stored_secret", lambda name: None)
    assert ph.adapter_connected("cloudflare_pages") is False
    assert ph.adapter_connected("github_pages") is False
    monkeypatch.setattr(ph, "_stored_secret", lambda name: {"cloudflare_pages": "tok"}.get(name))
    assert ph.adapter_connected("cloudflare_pages") is True

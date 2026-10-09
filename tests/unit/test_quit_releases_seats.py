"""Quitting Friday gives the machine its memory back.

llama-server seats are separate processes on purpose — that is what lets them
survive a restart — so nothing about them dies when the server does.
`Arbiter.shutdown()` existed to evict them and had no caller anywhere, so a
quit from the tray left a 27B seat holding around 14 GB of RAM and most of a
12 GB card. The machine is the owner's, not Friday's, the moment he has closed
her; the incident was a video call dying minutes after a quit.

A planned restart is different: the deploy lane and the tray's own Restart
keep the seats, because reloading a 27B costs the better part of a minute and
nobody asked for the memory back (P-BRAIN-SEAT). Only a quit releases them.
"""
import json
import sys

import pytest

ra = pytest.importorskip("agent_friday.services.residency_arbiter")

KEY = "keep_brain_warm_between_sessions"


class _FakeArbiter:
    def __init__(self, seats=("bonsai2:27b",)):
        self.seats = list(seats)
        self.shutdown_calls = 0

    def resident_seats(self):
        return list(self.seats)

    def shutdown(self):
        self.shutdown_calls += 1
        self.seats = []


@pytest.fixture
def arb(monkeypatch):
    a = _FakeArbiter()
    monkeypatch.setattr(ra, "get_arbiter", lambda: a)
    return a


@pytest.fixture
def settings(monkeypatch):
    box = {}
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: box)
    return box


@pytest.fixture
def receipts(monkeypatch):
    written = []
    from agent_friday.governance import action_gate as ag
    monkeypatch.setattr(ag, "_receipt", lambda e: written.append(e))
    return written


# ── A quit releases ────────────────────────────────────────────────────────

def test_a_quit_evicts_every_seat(arb, settings, receipts):
    out = ra.release_for_quit("tray_quit")
    assert arb.shutdown_calls == 1
    assert out["decision"] == "released"
    assert out["seats_released"] == ["bonsai2:27b"]


def test_a_quit_writes_a_receipt_naming_what_it_released(arb, settings, receipts):
    ra.release_for_quit("tray_quit")
    assert len(receipts) == 1, "a quit that releases memory has to be recorded"
    r = receipts[0]
    assert r["tool"] == "residency.release_for_quit"
    assert r["decision"] == "released"
    assert r["seats_released"] == ["bonsai2:27b"]
    assert r["reason"] == "tray_quit"


def test_the_setting_defaults_to_off():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS.get(KEY) is False, (
        "the brain must not be kept warm unless the owner asked")


# ── What must not be released ──────────────────────────────────────────────

def test_a_planned_restart_keeps_the_seats(arb, settings, receipts):
    """P-BRAIN-SEAT: the deploy lane and tray Restart keep them."""
    out = ra.release_for_quit("deploy_restart", planned=True)
    assert arb.shutdown_calls == 0
    assert out["decision"] == "kept_planned_restart"
    assert out["seats_released"] == []
    assert receipts, "keeping them is a decision too, and is receipted"


def test_the_owner_can_ask_to_keep_the_brain_warm(arb, settings, receipts):
    settings[KEY] = True
    out = ra.release_for_quit("tray_quit")
    assert arb.shutdown_calls == 0
    assert out["decision"] == "kept_by_setting"
    assert out["keep_warm_setting"] is True


def test_the_receipt_says_which_way_the_setting_was_set(arb, settings, receipts):
    settings[KEY] = False
    ra.release_for_quit("tray_quit")
    assert receipts[0]["keep_warm_setting"] is False


# ── It must not be able to block a quit ────────────────────────────────────

def test_an_eviction_that_raises_does_not_stop_the_quit(arb, settings, receipts,
                                                       monkeypatch):
    def boom():
        raise RuntimeError("seat will not die")
    monkeypatch.setattr(arb, "shutdown", boom)
    out = ra.release_for_quit("tray_quit")
    assert out["decision"] == "failed", out
    assert receipts, "a failure is the most important thing to record"


def test_a_receipt_that_cannot_be_written_does_not_stop_the_eviction(
        arb, settings, monkeypatch):
    from agent_friday.governance import action_gate as ag
    monkeypatch.setattr(ag, "_receipt",
                        lambda e: (_ for _ in ()).throw(OSError("disk full")))
    out = ra.release_for_quit("tray_quit")
    assert arb.shutdown_calls == 1, (
        "the memory matters more than the record of it")
    assert out.get("receipt") == "unwritten"


def test_no_arbiter_is_reported_not_crashed(settings, receipts, monkeypatch):
    monkeypatch.setattr(ra, "get_arbiter",
                        lambda: (_ for _ in ()).throw(RuntimeError("none")))
    out = ra.release_for_quit("tray_quit")
    assert out["decision"] == "no_arbiter"


# ── The wiring ─────────────────────────────────────────────────────────────

#: friday_tray imports pystray, which pyproject declares for win32 only and
#: whose import selects an OS tray backend a headless Linux runner lacks. Only
#: the two tests that read the tray's source need it; on Windows the import
#: still runs for real, so a broken tray fails both Windows legs.
needs_windows_tray = pytest.mark.skipif(
    sys.platform != "win32",
    reason="friday_tray is a Windows-only tray app (pystray is win32-only)")


@needs_windows_tray
def test_the_tray_quit_releases_before_it_stops_the_server():
    """Order matters: stop_server terminates the process, and on Windows a
    terminated process runs no atexit handler, so asking afterwards asks
    nobody."""
    import inspect
    from agent_friday import friday_tray
    src = inspect.getsource(friday_tray.FridayTray._quit)
    assert "_release_seats" in src, "tray Quit must release the seats"
    assert src.index("_release_seats") < src.index("stop_server"), (
        "it has to be released BEFORE the server is terminated")


@needs_windows_tray
def test_the_tray_restart_does_not_release():
    import inspect
    from agent_friday import friday_tray
    src = inspect.getsource(friday_tray.FridayTray.restart_server)
    assert "_release_seats" not in src, (
        "a restart is planned; reloading a 27B for nothing is the regression")


def test_the_release_is_reachable_over_http():
    """The tray is a separate process: the arbiter is only reachable by asking
    the server."""
    import agent_friday.routes.residency as rr
    src = open(rr.__file__, encoding="utf-8").read()
    assert "release-for-quit" in src
    assert "release_for_quit" in src


def test_the_shutdown_path_is_actually_called_now(arb, settings, receipts):
    """The whole bug in one line: shutdown() had no caller."""
    ra.release_for_quit("tray_quit")
    assert arb.shutdown_calls == 1

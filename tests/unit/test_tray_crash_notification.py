"""Unit tests for FridayTray._watchdog()'s crash notification (2026-09-04).

Before this, the watchdog correctly noticed the server had died -- every 5s,
like clockwork -- and did exactly one thing about it: relabelled its own
tray menu. Friday's server was down 26 minutes before anyone knew, because
nobody was looking at that menu; an unrelated hourly port check outside the
app is what actually caught it. See KNOWN_ISSUES.md.

No auto-restart is added or tested here on purpose -- that's a deliberate
non-goal (resurrecting a crashed process on a loop can mask a repeating
fault), not an oversight.
"""
from __future__ import annotations

import sys

import pytest

if sys.platform != "win32":
    pytest.skip(
        "friday_tray is a Windows-only tray app (pystray is win32-only)",
        allow_module_level=True,
    )

import agent_friday.friday_tray as ft
from agent_friday.friday_tray import FridayTray


class _StopLoop(Exception):
    """Breaks _watchdog()'s `while True` after exactly one body execution."""


class _FakeIcon:
    def __init__(self):
        self.notifications = []
        self.menu_updates = 0

    def notify(self, message, title=None):
        self.notifications.append((message, title))

    def update_menu(self):
        self.menu_updates += 1


@pytest.fixture
def tray():
    t = FridayTray()
    t.icon = _FakeIcon()
    return t


def _run_one_tick(tray, monkeypatch, *, port_alive: bool):
    """Execute exactly one iteration of _watchdog()'s loop body."""
    ticks = {"n": 0}

    def fake_sleep(_seconds):
        ticks["n"] += 1
        if ticks["n"] > 1:
            raise _StopLoop()

    monkeypatch.setattr(ft.time, "sleep", fake_sleep)
    monkeypatch.setattr(ft, "_port_in_use", lambda port: port_alive)
    tray.server_proc = None  # alive is driven purely by the _port_in_use mock
    with pytest.raises(_StopLoop):
        tray._watchdog()


class TestCrashNotification:
    def test_server_dying_while_running_notifies(self, tray, monkeypatch):
        tray.running = True
        _run_one_tick(tray, monkeypatch, port_alive=False)

        assert tray.running is False, "state must still update, same as before"
        assert tray.icon.menu_updates == 1, "the menu relabel must still happen"
        assert len(tray.icon.notifications) == 1
        message, title = tray.icon.notifications[0]
        assert "unexpectedly" in message
        assert "NOT been restarted" in message, (
            "must be explicit that nothing auto-restarted -- that's the "
            "whole point of notifying instead of resurrecting"
        )
        assert title == "Friday Desktop"

    def test_a_deliberate_stop_does_not_notify(self, tray, monkeypatch):
        """stop_server() already sets self.running = False synchronously
        before the watchdog's next poll -- so by the time this tick runs,
        `alive` and `self.running` already agree and the notify branch
        never triggers. Simulated directly here rather than via a real
        stop_server() call, to isolate the watchdog's own decision logic."""
        tray.running = False
        _run_one_tick(tray, monkeypatch, port_alive=False)

        assert tray.icon.notifications == []
        assert tray.icon.menu_updates == 0, "no state change, no menu update needed"

    def test_coming_back_up_does_not_notify(self, tray, monkeypatch):
        tray.running = False
        _run_one_tick(tray, monkeypatch, port_alive=True)

        assert tray.running is True
        assert tray.icon.menu_updates == 1
        assert tray.icon.notifications == [], (
            "recovering is not a crash -- only running->dead is the shape "
            "worth telling him about"
        )

    def test_a_broken_notify_call_does_not_crash_the_watchdog(self, tray, monkeypatch):
        tray.running = True
        tray.icon.notify = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        # Must not raise out of the tick despite notify() failing.
        _run_one_tick(tray, monkeypatch, port_alive=False)
        assert tray.running is False

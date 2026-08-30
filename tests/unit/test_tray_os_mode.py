"""PR-2 (OS mode switch, OS-mode sequence) — behavior 1: the system tray must
not launch under FRIDAY_OS_MODE (there is no desktop to put it on in a kiosk).

friday_tray.py deliberately does NOT import agent_friday.core.os_mode (see
_os_mode_active()'s docstring in that module for why: importing anything
under the `agent_friday.core` package forces Python to execute
agent_friday/core/__init__.py first, which this pre-server entry point must
not do). These tests exercise the module's own duplicated env-var check and
the gate in main() that uses it, mocking out pystray/socket/signal so
nothing here actually creates a tray icon, binds a real port, or installs a
real signal handler.
"""
from __future__ import annotations

import socket as socket_mod
import sys

import pytest

# friday_tray is the Windows system-tray app: it imports pystray, which
# pyproject declares as sys_platform == "win32" only, and whose import
# selects an OS tray backend that does not exist on a headless Linux runner.
# Same skip pattern as test_tray_restart_debounce.py.
if sys.platform != "win32":
    pytest.skip(
        "friday_tray is a Windows-only tray app (pystray is win32-only)",
        allow_module_level=True,
    )

import agent_friday.friday_tray as friday_tray


class _FakeGuardSocket:
    """Stand-in for the single-instance guard socket. Records every instance
    created and whether bind() was called on it, without touching a real
    port."""
    created: list = []

    def __init__(self, *_args, **_kwargs):
        self.bound = False
        _FakeGuardSocket.created.append(self)

    def bind(self, _addr):
        self.bound = True


class _FakeTray:
    """Stand-in for FridayTray. Records instantiation and whether run() was
    called, without ever creating a real pystray icon (which would block)."""
    instances: list = []

    def __init__(self):
        self.ran = False
        _FakeTray.instances.append(self)

    def run(self):
        self.ran = True


@pytest.fixture(autouse=True)
def _reset_fakes():
    _FakeGuardSocket.created.clear()
    _FakeTray.instances.clear()
    yield


@pytest.fixture(autouse=True)
def _patch_collaborators(monkeypatch):
    """Every test in this file patches the same three things main() touches
    after the OS-mode gate: the tray class, the guard socket, and signal
    registration."""
    monkeypatch.setattr(friday_tray, "FridayTray", _FakeTray)
    monkeypatch.setattr(socket_mod, "socket", _FakeGuardSocket)
    monkeypatch.setattr(friday_tray.signal, "signal", lambda *a, **k: None)


# ── _os_mode_active() — the duplicated, import-light truthy check ──────────

def test_os_mode_active_default_false(monkeypatch):
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    assert friday_tray._os_mode_active() is False


def test_os_mode_active_true_values(monkeypatch):
    for val in ("1", "true", "True", "yes", "on"):
        monkeypatch.setenv("FRIDAY_OS_MODE", val)
        assert friday_tray._os_mode_active() is True, val


def test_os_mode_active_false_values(monkeypatch):
    for val in ("0", "false", "no", "", "garbage"):
        monkeypatch.setenv("FRIDAY_OS_MODE", val)
        assert friday_tray._os_mode_active() is False, val


# ── main() gating ───────────────────────────────────────────────────────────

def test_main_skips_tray_under_os_mode(monkeypatch):
    """FRIDAY_OS_MODE=1 -> main() must return before ever constructing a
    FridayTray, and before even creating (let alone binding) the
    single-instance guard socket."""
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")

    friday_tray.main()

    assert _FakeTray.instances == [], (
        "FridayTray must not be instantiated under OS mode — a tool that "
        "cannot exist should not be built and then not run"
    )
    assert _FakeGuardSocket.created == [], (
        "the single-instance guard socket must not even be created under "
        "OS mode"
    )


def test_main_runs_tray_when_os_mode_unset(monkeypatch):
    """Regression guard: the Windows default (FRIDAY_OS_MODE unset) must
    reach FridayTray().run() exactly as it did before this PR."""
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)

    friday_tray.main()

    assert len(_FakeGuardSocket.created) == 1
    assert _FakeGuardSocket.created[0].bound is True
    assert len(_FakeTray.instances) == 1
    assert _FakeTray.instances[0].ran is True


def test_main_runs_tray_when_os_mode_explicitly_off(monkeypatch):
    """FRIDAY_OS_MODE=0 is not truthy — same Windows-default behavior as
    unset, not treated as kiosk mode."""
    monkeypatch.setenv("FRIDAY_OS_MODE", "0")

    friday_tray.main()

    assert len(_FakeTray.instances) == 1
    assert _FakeTray.instances[0].ran is True

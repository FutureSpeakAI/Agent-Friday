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
def _reset_fakes(monkeypatch):
    _FakeGuardSocket.created.clear()
    _FakeTray.instances.clear()
    # The named Windows mutex is the one part of the single-instance guard
    # that reaches the real OS, and on a developer machine the real tray is
    # usually holding it - which made these tests pass or fail on whether
    # Friday happened to be running. That is worse than a failing test: it
    # will eventually pass for the wrong reason. Stubbed here so these tests
    # stay about OS mode, while the socket half of the guard (which they do
    # assert on) is still genuinely exercised.
    monkeypatch.setattr(friday_tray, "_claim_windows_mutex", lambda: True)
    friday_tray._INSTANCE_HANDLES.clear()
    yield
    friday_tray._INSTANCE_HANDLES.clear()


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


# ── Single-instance guard ────────────────────────────────────────────────
#
# Two trays were found running on 2026-09-18, both created in the same second,
# each having started its own server. The guard that was supposed to stop that
# was a bare socket bind, which fails open on Windows. These tests are about
# the guard itself rather than about OS mode, so they do NOT take the autouse
# stub above at face value - each drives `_acquire_single_instance` directly.


def test_a_second_tray_is_refused_when_the_mutex_is_already_held(monkeypatch):
    """The whole point. If this returns True twice, Friday runs twice."""
    monkeypatch.setattr(friday_tray, "_claim_windows_mutex", lambda: False)
    assert friday_tray._acquire_single_instance() is False


def test_the_first_tray_is_allowed_through(monkeypatch):
    monkeypatch.setattr(friday_tray, "_claim_windows_mutex", lambda: True)
    friday_tray._INSTANCE_HANDLES.clear()
    assert friday_tray._acquire_single_instance() is True
    friday_tray._INSTANCE_HANDLES.clear()


def test_the_socket_still_refuses_a_second_tray_without_the_mutex(monkeypatch):
    """Belt and braces, and it has to stay working.

    The mutex is Windows-only; on any other platform the socket IS the guard.
    A refactor that quietly made the socket decorative would leave every
    non-Windows install unprotected and every test still green.
    """
    monkeypatch.setattr(friday_tray, "_claim_windows_mutex", lambda: True)

    class _TakenSocket:
        def __init__(self, *a, **k):
            pass

        def setsockopt(self, *a, **k):
            pass

        def bind(self, *a, **k):
            raise OSError("address already in use")

    monkeypatch.setattr(friday_tray.socket, "socket", _TakenSocket)
    assert friday_tray._acquire_single_instance() is False


def test_the_handle_is_held_for_the_life_of_the_process(monkeypatch):
    """A mutex handle that falls out of scope is a mutex that is released.

    Held in a module-level list precisely so that garbage collection at the
    end of main() cannot quietly reopen the door this guard exists to shut.
    """
    monkeypatch.setattr(friday_tray, "_claim_windows_mutex", lambda: True)
    friday_tray._INSTANCE_HANDLES.clear()
    friday_tray._acquire_single_instance()
    assert friday_tray._INSTANCE_HANDLES, \
        "nothing is holding the guard open after it was acquired"
    friday_tray._INSTANCE_HANDLES.clear()
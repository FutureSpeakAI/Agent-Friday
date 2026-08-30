"""PR-2 (OS mode switch, OS-mode sequence) — behavior 4: `_open_app` in
services/agent.py must answer honestly that no applications are available
under FRIDAY_OS_MODE, rather than attempting a Windows-specific launch (or,
on a non-Windows dev machine, silently returning None as it always has).

`_open_app` returns:
  * None                      — the name isn't a recognized app at all
  * a confirmation string     — a recognized app, launched (win32 only)
  * (this PR) an honest "I can't launch ... no desktop applications are
    installed" string          — a recognized app, but FRIDAY_OS_MODE is on

The Windows-default path (OS mode off) is exercised for real: this suite
runs on Windows, so sys.platform == 'win32' and _open_app really does
subprocess.Popen a known app when it isn't gated. That call is monkeypatched
to a no-op recorder so this test never actually launches Notepad.
"""
from __future__ import annotations

import sys

import pytest

from agent_friday.services import agent as agent_mod


@pytest.fixture(autouse=True)
def _os_mode_unset_by_default(monkeypatch):
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    yield


@pytest.fixture(autouse=True)
def _never_actually_launch_anything(monkeypatch):
    """Prevent a real subprocess launch on the Windows-default path — the
    behavior under test is the STRING _open_app returns, not whether Notepad
    actually opens."""
    calls = []
    monkeypatch.setattr(
        agent_mod.subprocess, "Popen",
        lambda *a, **k: calls.append((a, k)) or object(),
    )
    return calls


def test_unrecognized_name_returns_none_regardless_of_os_mode(monkeypatch):
    """An unrecognized name was never Friday's to answer for — os_mode must
    not change this at all, in either direction."""
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    assert agent_mod._open_app("some totally made up app xyz") is None

    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    assert agent_mod._open_app("some totally made up app xyz") is None


def test_recognized_app_honest_refusal_under_os_mode(monkeypatch):
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    result = agent_mod._open_app("notepad")
    assert result is not None
    assert "notepad" in result.lower() or "Notepad" in result
    assert "no desktop applications" in result
    # Must not claim success — the exact opposite of the pre-PR behavior.
    assert "Done" not in result


def test_recognized_shell_app_honest_refusal_under_os_mode(monkeypatch):
    """_OPEN_SHELL_APPS is a second, separate lookup table (browsers/Office)
    — must be covered by the same gate as _OPEN_APPS."""
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    result = agent_mod._open_app("chrome")
    assert result is not None
    assert "no desktop applications" in result


def test_recognized_app_never_calls_popen_under_os_mode(monkeypatch, _never_actually_launch_anything):
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    agent_mod._open_app("notepad")
    assert _never_actually_launch_anything == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-default launch path is win32-only")
def test_recognized_app_still_launches_when_os_mode_unset(monkeypatch, _never_actually_launch_anything):
    """Regression guard: FRIDAY_OS_MODE unset (the Windows default) must
    reach the exact pre-PR behavior — a Popen call and a 'Done — I launched'
    confirmation, not the new honest-refusal string."""
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    result = agent_mod._open_app("notepad")
    assert result is not None
    assert "Done" in result
    assert "no desktop applications" not in result
    assert len(_never_actually_launch_anything) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-default launch path is win32-only")
def test_recognized_app_still_launches_when_os_mode_explicitly_off(monkeypatch, _never_actually_launch_anything):
    monkeypatch.setenv("FRIDAY_OS_MODE", "0")
    result = agent_mod._open_app("notepad")
    assert result is not None
    assert "Done" in result
    assert len(_never_actually_launch_anything) == 1

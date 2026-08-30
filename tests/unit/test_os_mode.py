"""PR-2 (OS mode switch, OS-mode sequence) — tests for agent_friday.core.os_mode.

`agent_friday.core.os_mode.is_os_mode()` is the single source of truth every
other OS-mode gate in this PR imports rather than reading FRIDAY_OS_MODE
itself. This module is a standalone leaf inside the `agent_friday.core`
PACKAGE (see its own docstring for the full rationale), so importing it here
DOES force Python to execute `agent_friday/core/__init__.py` first — this
test file accepts that cost the same way test_tray_restart_debounce.py and
other tests already importing agent_friday.core do, under the suite-wide
FRIDAY_HOME/HOME redirect in tests/conftest.py.
"""
from __future__ import annotations

from agent_friday.core.os_mode import is_os_mode


def test_default_unset_is_false(monkeypatch):
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    assert is_os_mode() is False


def test_explicit_zero_is_false(monkeypatch):
    monkeypatch.setenv("FRIDAY_OS_MODE", "0")
    assert is_os_mode() is False


def test_empty_string_is_false(monkeypatch):
    monkeypatch.setenv("FRIDAY_OS_MODE", "")
    assert is_os_mode() is False


def test_garbage_value_is_false(monkeypatch):
    monkeypatch.setenv("FRIDAY_OS_MODE", "nope")
    assert is_os_mode() is False


def test_one_is_true(monkeypatch):
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    assert is_os_mode() is True


def test_true_is_true_case_insensitive(monkeypatch):
    monkeypatch.setenv("FRIDAY_OS_MODE", "True")
    assert is_os_mode() is True


def test_yes_is_true(monkeypatch):
    monkeypatch.setenv("FRIDAY_OS_MODE", "yes")
    assert is_os_mode() is True


def test_reads_live_not_cached(monkeypatch):
    """A long-lived process (or a test suite using monkeypatch) must see the
    CURRENT value on every call, not whatever was set the first time this
    function ran — there is no module-level caching to go stale."""
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    assert is_os_mode() is False
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    assert is_os_mode() is True
    monkeypatch.setenv("FRIDAY_OS_MODE", "0")
    assert is_os_mode() is False

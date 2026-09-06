"""The maintainer, 2026-09-06: "always open files you create for me upon
completing them." `_maybe_auto_open` (services/agent.py) is the one shared
call site both file-creating paths use: `_tool_write_file` (a fresh write,
never an append) and `services/creations._notify_creation` (every creative
generation). Default off (`auto_open_created_files` in DEFAULT_SETTINGS) —
auto-launching a viewer the instant a file lands is a real product decision,
not a safe default for everyone.
"""
from __future__ import annotations

from agent_friday.services import agent as ag
from agent_friday.core import DEFAULT_SETTINGS


def test_default_setting_is_off():
    assert DEFAULT_SETTINGS["auto_open_created_files"] is False


def test_off_never_opens(monkeypatch):
    calls = []
    monkeypatch.setattr(ag, "_perform_open", lambda target, in_browser=False: calls.append(target))
    monkeypatch.setattr(ag, "_load_settings", lambda: {"auto_open_created_files": False})
    ag._maybe_auto_open(r"C:\fake\path.txt")
    assert calls == []


def test_on_opens_the_real_path(monkeypatch):
    calls = []
    monkeypatch.setattr(ag, "_perform_open", lambda target, in_browser=False: calls.append(target))
    monkeypatch.setattr(ag, "_load_settings", lambda: {"auto_open_created_files": True})
    ag._maybe_auto_open(r"C:\fake\path.txt")
    assert calls == [r"C:\fake\path.txt"]


def test_a_failed_open_is_swallowed_not_raised(monkeypatch):
    """Auto-open is a courtesy; it must never turn a successful creation
    into an error the user sees instead of their file."""
    def boom(target, in_browser=False):
        raise RuntimeError("no app registered for this extension")
    monkeypatch.setattr(ag, "_perform_open", boom)
    monkeypatch.setattr(ag, "_load_settings", lambda: {"auto_open_created_files": True})
    ag._maybe_auto_open(r"C:\fake\path.txt")  # must not raise


def test_write_file_auto_opens_on_write_not_append(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(ag, "_maybe_auto_open", lambda p: opened.append(str(p)))
    target = tmp_path / "note.txt"

    ag._tool_write_file({"path": str(target), "content": "hello", "mode": "write"})
    assert opened == [str(target.resolve())]

    opened.clear()
    ag._tool_write_file({"path": str(target), "content": " more", "mode": "append"})
    assert opened == [], "appending to an existing file must not pop it open"


def test_notify_creation_auto_opens_even_without_a_notification_engine(monkeypatch):
    """The auto-open preference must not be gated behind whether the
    notification engine happens to be configured -- they are independent
    concerns, and the bug shape (a real feature silently gated behind an
    unrelated early return) is exactly the class this codebase keeps
    finding elsewhere."""
    from agent_friday.services import creations as cr
    opened = []
    monkeypatch.setattr(ag, "_maybe_auto_open", lambda p: opened.append(str(p)))
    monkeypatch.setattr(cr, "_notif_engine", None)
    cr._notify_creation("friday_local_00005_.png")
    assert opened == [str(cr.CREATIONS_DIR / "friday_local_00005_.png")]

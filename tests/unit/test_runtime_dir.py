"""A8 — the local runtime stack has a declared, overridable home (D7).

Phase 2 put the stack in an invented per-machine directory outside both the
repo and ~/.friday, because no convention was declared for it. D7 settles that:
it belongs under the app's own convention, with an override, since the default
lands on the system drive and these artifacts run to tens of GB.
"""
from __future__ import annotations

from pathlib import Path

import agent_friday.core as core


def test_default_is_under_the_repo_convention():
    """~/.friday is where every other Friday artifact lives."""
    assert core.DEFAULT_RUNTIME_DIR == core.FRIDAY_DIR / "runtime"
    assert core.DEFAULT_RUNTIME_DIR.parent == core.FRIDAY_DIR


def test_default_used_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("FRIDAY_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(core, "_load_settings", lambda: {}, raising=False)
    assert core.runtime_dir() == core.DEFAULT_RUNTIME_DIR


def test_settings_key_overrides_the_default(monkeypatch):
    monkeypatch.delenv("FRIDAY_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"runtime_dir": r"D:\ai-stack"}, raising=False)
    assert core.runtime_dir() == Path(r"D:\ai-stack")


def test_env_var_beats_the_settings_key(monkeypatch):
    """Env wins: it is how you relocate without editing a live config."""
    monkeypatch.setenv("FRIDAY_RUNTIME_DIR", r"E:\from-env")
    monkeypatch.setattr(core, "_load_settings",
                        lambda: {"runtime_dir": r"D:\from-settings"},
                        raising=False)
    assert core.runtime_dir() == Path(r"E:\from-env")


def test_unreadable_settings_fall_back_rather_than_raise(monkeypatch):
    monkeypatch.delenv("FRIDAY_RUNTIME_DIR", raising=False)

    def _boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(core, "_load_settings", _boom, raising=False)
    assert core.runtime_dir() == core.DEFAULT_RUNTIME_DIR


def test_empty_values_are_treated_as_unset(monkeypatch):
    """The shipped default is "" — it must mean 'use the default', not Path('')."""
    monkeypatch.delenv("FRIDAY_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(core, "_load_settings", lambda: {"runtime_dir": ""},
                        raising=False)
    assert core.runtime_dir() == core.DEFAULT_RUNTIME_DIR


def test_user_paths_are_expanded(monkeypatch):
    monkeypatch.setenv("FRIDAY_RUNTIME_DIR", "~/custom-runtime")
    assert core.runtime_dir() == Path.home() / "custom-runtime"


def test_shipped_default_settings_declare_the_key():
    assert "runtime_dir" in core.DEFAULT_SETTINGS
    assert core.DEFAULT_SETTINGS["runtime_dir"] == ""

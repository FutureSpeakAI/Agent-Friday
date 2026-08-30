"""PR-1 (path consolidation, OS-mode sequence) — tests for agent_friday.paths.

These pin two things that must never regress:
  1. Every one of the four functions honors its env var override.
  2. friday_home() with no env var set is byte-identical to the literal
     `Path.home() / ".friday"` expression it replaced across ~22 call sites —
     this PR is a pure refactor and must not change Windows default behavior.

See agent_friday/paths.py's module docstring for why this module lives at
`agent_friday.paths` rather than `agent_friday.core.paths`: importing
`agent_friday.core` executes a ~2600-line Flask app module with real
import-time side effects (including a live `~/wiki` migration check against
the actual home directory), so these tests avoid importing `agent_friday.core`
except in the one test that specifically exercises the delegation to
`core.runtime_dir()`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_friday import paths


# ── friday_home() ──────────────────────────────────────────────────────────

def test_friday_home_honors_env_var(tmp_path, monkeypatch):
    custom = tmp_path / "custom-friday-home"
    monkeypatch.setenv("FRIDAY_HOME", str(custom))
    assert paths.friday_home() == custom


def test_friday_home_default_matches_pre_refactor_expression(monkeypatch):
    """No FRIDAY_HOME set -> must equal Path.home() / ".friday" exactly,
    byte-for-byte, since that was the literal expression at every one of the
    ~22 call sites this PR replaced. Path.home() is mocked here (rather than
    relying on the test suite's global HOME/USERPROFILE redirect) so this
    test is self-contained and never touches the real home directory even if
    run outside the full suite's conftest."""
    monkeypatch.delenv("FRIDAY_HOME", raising=False)
    fake_home = Path("C:/fake-home") if sys.platform == "win32" else Path("/fake-home")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert paths.friday_home() == fake_home / ".friday"


def test_friday_home_expands_user_in_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path) + "/sub")
    assert paths.friday_home() == tmp_path / "sub"


# ── models_dir() ────────────────────────────────────────────────────────────

def test_models_dir_honors_env_var(tmp_path, monkeypatch):
    custom = tmp_path / "custom-models"
    monkeypatch.setenv("FRIDAY_MODELS_DIR", str(custom))
    assert paths.models_dir() == custom


def test_models_dir_defaults_relative_to_friday_home(tmp_path, monkeypatch):
    monkeypatch.delenv("FRIDAY_MODELS_DIR", raising=False)
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    assert paths.models_dir() == tmp_path / "models"


# ── voice_assets_dir() ──────────────────────────────────────────────────────

def test_voice_assets_dir_honors_env_var(tmp_path, monkeypatch):
    custom = tmp_path / "custom-voice"
    monkeypatch.setenv("FRIDAY_VOICE_ASSETS", str(custom))
    assert paths.voice_assets_dir() == custom


def test_voice_assets_dir_defaults_relative_to_friday_home(tmp_path, monkeypatch):
    monkeypatch.delenv("FRIDAY_VOICE_ASSETS", raising=False)
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    assert paths.voice_assets_dir() == tmp_path / "voice_assets"


# ── runtime_dir() ───────────────────────────────────────────────────────────

def test_runtime_dir_honors_env_var_via_core_delegation(tmp_path, monkeypatch):
    """agent_friday.core.runtime_dir() already implements FRIDAY_RUNTIME_DIR
    with real precedence (env > settings.json > default); paths.runtime_dir()
    must delegate to it rather than reimplementing/diverging."""
    custom = tmp_path / "custom-runtime"
    monkeypatch.setenv("FRIDAY_RUNTIME_DIR", str(custom))
    assert paths.runtime_dir() == custom


def test_runtime_dir_falls_back_when_core_unimportable(tmp_path, monkeypatch):
    """If agent_friday.core cannot be imported (e.g. Flask missing), fall
    back to env-var-or-default without the settings.json layer, rather than
    raising."""
    custom = tmp_path / "fallback-runtime"
    monkeypatch.setenv("FRIDAY_RUNTIME_DIR", str(custom))
    monkeypatch.setitem(sys.modules, "agent_friday.core", None)
    assert paths.runtime_dir() == custom


def test_runtime_dir_fallback_default_relative_to_friday_home(tmp_path, monkeypatch):
    monkeypatch.delenv("FRIDAY_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "agent_friday.core", None)
    assert paths.runtime_dir() == tmp_path / "runtime"

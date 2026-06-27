"""Unit tests for per-workspace temperature resolution
(services/model_router.resolve_workspace_temperature) — the creative-pipeline
temperature profiles read from settings.workspace_temperatures.
"""
import pytest

from agent_friday.services import model_router as model_router


def test_explicit_temperature_always_wins():
    assert model_router.resolve_workspace_temperature("studio", 0.11) == 0.11


def test_known_workspaces_use_profile_defaults():
    # Defaults seeded in core.DEFAULT_SETTINGS.workspace_temperatures.
    assert model_router.resolve_workspace_temperature("studio") == 0.75
    assert model_router.resolve_workspace_temperature("research") == 0.25
    assert model_router.resolve_workspace_temperature("code") == 0.45
    assert model_router.resolve_workspace_temperature("content") == 0.6


def test_unknown_workspace_returns_none():
    assert model_router.resolve_workspace_temperature("nonexistent-ws") is None


def test_blank_workspace_returns_none():
    assert model_router.resolve_workspace_temperature("") is None
    assert model_router.resolve_workspace_temperature(None) is None


def test_case_insensitive():
    assert model_router.resolve_workspace_temperature("STUDIO") == 0.75


def test_null_entry_yields_none(monkeypatch):
    monkeypatch.setattr(model_router, "_load_settings",
                        lambda: {"workspace_temperatures": {"studio": None}})
    assert model_router.resolve_workspace_temperature("studio") is None


def test_value_is_clamped(monkeypatch):
    monkeypatch.setattr(model_router, "_load_settings",
                        lambda: {"workspace_temperatures": {"wild": 5.0}})
    assert model_router.resolve_workspace_temperature("wild") == 1.0

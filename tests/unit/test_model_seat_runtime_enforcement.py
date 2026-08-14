"""FR-1 hardening — un-bypassable runtime seat enforcement (docs:
toolcall-integrity-v5).

The settings-save gate (routes/core_routes.py::api_settings) only catches a
seat change made through POST /api/settings. Anything that writes
settings.json directly — or any other future writer — bypasses it entirely.
resolve_local_seat() (services/model_seat_gate.py) closes that gap: it is
re-evaluated on every tool-using dispatch in _call_ollama, using whatever
model_routing.local_model settings.json currently holds, regardless of how
it got there.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import model_seat_gate as gate


@pytest.fixture
def isolated_gate_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "GATE_DIR", tmp_path / "live")
    monkeypatch.setattr(gate, "EVIDENCE_DIR", tmp_path / "evidence")
    return tmp_path


def _write_result(directory, model, provider, passed, timestamp):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{gate._safe_name(model, provider)}.json"
    path.write_text(json.dumps({
        "model": model, "provider": provider, "passed": passed,
        "timestamp": timestamp, "score": "10/10" if passed else "0/10",
    }), encoding="utf-8")


class TestGetLastKnownGreen:
    def test_no_history_returns_none(self, isolated_gate_dirs):
        assert gate.get_last_known_green("local") is None

    def test_picks_the_passed_model(self, isolated_gate_dirs):
        _write_result(gate.GATE_DIR, "gemma4:latest", "local", True, 100)
        _write_result(gate.GATE_DIR, "gemma3:4b", "local", False, 200)
        assert gate.get_last_known_green("local") == "gemma4:latest"

    def test_picks_the_most_recent_passed_model(self, isolated_gate_dirs):
        _write_result(gate.GATE_DIR, "old-green:latest", "local", True, 100)
        _write_result(gate.GATE_DIR, "new-green:latest", "local", True, 500)
        assert gate.get_last_known_green("local") == "new-green:latest"

    def test_live_gate_dir_wins_over_committed_evidence(self, isolated_gate_dirs):
        # A live re-run on THIS machine is more authoritative than the
        # repo-committed reference result, even if older by timestamp.
        _write_result(gate.GATE_DIR, "gemma4:latest", "local", True, 1)
        _write_result(gate.EVIDENCE_DIR, "some-other-model:latest", "local", True, 999999)
        assert gate.get_last_known_green("local") == "gemma4:latest"

    def test_falls_back_to_evidence_dir_when_no_live_history(self, isolated_gate_dirs):
        _write_result(gate.EVIDENCE_DIR, "gemma4:latest", "local", True, 1)
        assert gate.get_last_known_green("local") == "gemma4:latest"

    def test_ignores_a_different_provider(self, isolated_gate_dirs):
        _write_result(gate.GATE_DIR, "some-model", "cloud", True, 100)
        assert gate.get_last_known_green("local") is None


class TestResolveLocalSeat:
    def test_green_model_passes_through_unmodified(self, isolated_gate_dirs):
        _write_result(gate.GATE_DIR, "gemma4:latest", "local", True, 100)
        result = gate.resolve_local_seat("gemma4:latest", provider="local")
        assert result == {
            "model": "gemma4:latest", "seat_ok": True, "requested": "gemma4:latest",
            "reason": "gated green", "fallback": None,
        }

    def test_red_model_falls_back_to_last_known_green(self, isolated_gate_dirs, monkeypatch):
        # 2026-08-14: the fallback is only offered when it's actually
        # INSTALLED (the live incident substituted a green-but-deleted model
        # and 404'd hourly all night). Pin the inventory so this unit test
        # never touches a real daemon.
        monkeypatch.setattr(gate, "_installed_local_models",
                            lambda: {"gemma3:4b", "gemma4:latest"})
        _write_result(gate.GATE_DIR, "gemma3:4b", "local", False, 100)
        _write_result(gate.GATE_DIR, "gemma4:latest", "local", True, 200)
        result = gate.resolve_local_seat("gemma3:4b", provider="local")
        assert result["seat_ok"] is False
        assert result["model"] == "gemma4:latest"
        assert result["fallback"] == "last_known_green:gemma4:latest"
        assert "gemma3:4b" in result["reason"]

    def test_ungated_model_with_no_history_falls_back_to_last_known_green(self, isolated_gate_dirs, monkeypatch):
        # Never gated at all (no cached status) is treated the same as red —
        # fail closed, not fail open.
        monkeypatch.setattr(gate, "_installed_local_models",
                            lambda: {"gemma4:latest"})
        _write_result(gate.GATE_DIR, "gemma4:latest", "local", True, 200)
        result = gate.resolve_local_seat("some-brand-new-model:latest", provider="local")
        assert result["seat_ok"] is False
        assert result["model"] == "gemma4:latest"

    def test_red_model_with_no_fallback_available_goes_tool_free(self, isolated_gate_dirs):
        _write_result(gate.GATE_DIR, "gemma3:4b", "local", False, 100)
        result = gate.resolve_local_seat("gemma3:4b", provider="local")
        assert result["seat_ok"] is False
        assert result["model"] is None
        assert result["fallback"] == "tool_free"

    def test_empty_model_is_a_noop(self, isolated_gate_dirs):
        result = gate.resolve_local_seat("", provider="local")
        assert result["seat_ok"] is True
        assert result["model"] == ""

    def test_fallback_never_points_back_at_the_requested_model(self, isolated_gate_dirs):
        # If the ONLY "green" record on file happens to be for the requested
        # model itself, is_seat_green() would already have returned True
        # above — this guards the (should-be-impossible) case where the
        # fallback search somehow returns the same model, which must never
        # be reported as a fallback for itself.
        _write_result(gate.GATE_DIR, "gemma3:4b", "local", False, 300)
        result = gate.resolve_local_seat("gemma3:4b", provider="local")
        assert result["fallback"] != "last_known_green:gemma3:4b"

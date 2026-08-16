"""There is no runtime seat enforcement any more (2026-08-15).

This file used to pin FR-1: an un-bypassable check re-evaluated on every
tool-using dispatch, which substituted a "red" model with the last one that had
scored green, and stripped tools entirely when no green fallback existed.

Stephen removed it, and the evidence backed him:

  * The structural failures it fired on were a broken harness. gemma4:12b,
    26b, e2b and e4b scored 1/10, 1/10, 4/10 and 0/10 under a gate that set no
    num_ctx, ran cases concurrently so they evicted each other, and dropped
    `tools` on its own fallback path. Fixed, every one of them scored 10/10.
  * The honesty record that refused gemma4:26b held eleven timeouts and one
    HTTP 400 — eleven empty answers and no model output at all. The single
    case that actually ran, passed.
  * On 2026-08-15 a dependent 5-call tool chain scored 0/5 on every local
    model because `json.loads()` was being called on an already-parsed dict in
    our own loop, silently dropping every tool argument. Once fixed: 15/15.

Twice now, "this model can't use tools" has turned out to mean "we broke the
tools". That is the argument against a homegrown eval standing between a user
and a model they chose.

    "I absolutely want the user to be able to set any model they wish at any
     seat they wish, so this is non-negotiable."   — Stephen, 2026-08-15

What is pinned here is the inverse of what used to be: resolve_local_seat is a
pass-through, and there is no fallback machinery left for anything to go wrong
in. The functions survive as no-ops because callers still reference them, and
these tests exist so that a future change cannot quietly reintroduce
substitution behind those names.
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


class TestThereIsNoFallbackSeat:
    """The last-known-green concept is gone, not merely unused.

    It has to return None even when green records exist on disk, because the
    records outlive the feature — several are still sitting in the evidence
    directory — and a fallback that reappeared because an old file was still
    readable is exactly the failure this pins against.
    """

    def test_no_history_returns_none(self, isolated_gate_dirs):
        assert gate.get_last_known_green() is None

    def test_a_green_record_on_disk_does_not_resurrect_the_fallback(
            self, isolated_gate_dirs):
        _write_result(isolated_gate_dirs / "live", "old-green:latest",
                      "local", True, 900)
        assert gate.get_last_known_green() is None

    def test_committed_evidence_does_not_resurrect_it_either(
            self, isolated_gate_dirs):
        _write_result(isolated_gate_dirs / "evidence", "old-green:latest",
                      "local", True, 900)
        assert gate.get_last_known_green() is None


class TestResolveLocalSeatIsAPassThrough:

    def test_a_model_with_a_green_record_passes_through(self,
                                                        isolated_gate_dirs):
        _write_result(isolated_gate_dirs / "live", "good:7b", "local", True, 10)
        assert gate.resolve_local_seat("good:7b")["model"] == "good:7b"

    def test_a_model_with_a_red_record_passes_through_unchanged(
            self, isolated_gate_dirs):
        """A failing diagnostic is information. It was never a veto."""
        _write_result(isolated_gate_dirs / "live", "bad:7b", "local", False, 10)
        _write_result(isolated_gate_dirs / "live", "old-green:latest",
                      "local", True, 5)
        seat = gate.resolve_local_seat("bad:7b")
        assert seat["model"] == "bad:7b", "something substituted the model"
        assert seat.get("fallback") in (None, "")

    def test_a_model_nobody_ever_tested_passes_through(self,
                                                       isolated_gate_dirs):
        seat = gate.resolve_local_seat("brand-new:70b")
        assert seat["model"] == "brand-new:70b"
        assert seat.get("seat_ok") is not False

    def test_no_seat_is_ever_sent_tool_free(self, isolated_gate_dirs):
        """Stripping tools guaranteed the tool-calling failure the gate
        claimed to be detecting."""
        _write_result(isolated_gate_dirs / "live", "bad:7b", "local", False, 10)
        seat = gate.resolve_local_seat("bad:7b")
        assert seat.get("tool_free") is not True
        assert seat.get("drop_tools") is not True

    def test_empty_model_is_a_noop(self, isolated_gate_dirs):
        assert gate.resolve_local_seat("")["model"] in ("", None)


class TestTheAxisReportIsDiagnosticOnly:

    def test_axis_status_reports_structural_and_never_gates(self,
                                                            monkeypatch):
        monkeypatch.setattr(gate, "get_cached_status",
                            lambda m, provider="local": None)
        st = gate.axis_status("x:1b", "local")
        assert st["gates"] is False
        assert "honesty" not in st and "dual_green" not in st

    def test_the_honesty_battery_module_is_gone(self):
        with pytest.raises(ImportError):
            import agent_friday.services.honesty_battery  # noqa: F401

"""The model slate shows this PC's own benchmark (VRAM, speed, cold load) and uses its cold-load time as the
switch estimate until a real load has been timed. Always labelled with what it is and the date."""
from __future__ import annotations

from agent_friday.services import hardware_profile, model_slate as ms, residency_catalog as rc, seat_timing
from tests.unit.test_model_slate import CAT


def _pc(monkeypatch, tmp_path, rows):
    monkeypatch.setattr(seat_timing, "_path", lambda: tmp_path / "t.json")
    seat_timing._forget_memory_only()
    monkeypatch.setattr(ms, "_catalog", lambda: CAT)
    monkeypatch.setattr(ms, "_settings", lambda: {"model_routing": {"mode": "local_preferred"}})
    monkeypatch.setattr(ms, "_local_busy_reason", lambda mid: None)
    monkeypatch.setattr(ms, "_live_local", lambda: set())
    monkeypatch.setattr(hardware_profile, "get", lambda: {"gpus": [], "ram": {"total_mib": 1}})
    monkeypatch.setattr(rc, "measurements", lambda mid, fp: rows.get(mid, []))
    return {r["id"]: r for r in ms.slate()["models"]}


def test_the_newest_benchmark_is_shown_and_labelled(monkeypatch, tmp_path):
    rows = {"gemma4:e4b": [{"num_ctx": 8192, "vram_mib": 3081, "tok_s_median": 99.9, "cold_load_s": 27.5,
                            "measured_at": "2026-08-14"},
                           {"num_ctx": 32768, "vram_mib": 3200, "tok_s_median": 90.0, "measured_at": "2026-08-15"}]}
    got = _pc(monkeypatch, tmp_path, rows)["gemma4:e4b"]
    assert got["benchmark"]["vram_mib"] == 3200 and got["benchmark"]["measured_at"] == "2026-08-15"
    assert "not declared" in got["benchmark"]["basis"]


def test_a_benchmarked_cold_load_is_the_estimate_until_a_real_load_is_timed(monkeypatch, tmp_path):
    rows = {"gemma4:e4b": [{"num_ctx": 8192, "cold_load_s": 27.5, "measured_at": "2026-08-14"}]}
    sw = _pc(monkeypatch, tmp_path, rows)["gemma4:e4b"]["switch"]
    assert sw == {"cold": True, "expected_seconds": 27.5, "basis": "benchmarked on this PC's hardware on 2026-08-14"}
    seat_timing.record("gemma4:e4b", 31.0)
    assert {r["id"]: r for r in ms.slate()["models"]}["gemma4:e4b"]["switch"]["expected_seconds"] == 31.0


def test_an_unbenchmarked_model_shows_nothing_invented(monkeypatch, tmp_path):
    got = _pc(monkeypatch, tmp_path, {})["gemma4:e4b"]
    assert got["benchmark"] is None and got["switch"]["expected_seconds"] is None

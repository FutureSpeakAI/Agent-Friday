"""A cold local pick says how long it last took to load here, never a made-up number."""
from __future__ import annotations

import inspect

import pytest

from agent_friday.services import model_slate as ms
from agent_friday.services import residency_arbiter, seat_timing
from tests.unit.test_model_slate import CAT


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(seat_timing, "_path", lambda: tmp_path / "seat_load_times.json")
    monkeypatch.setattr(ms, "_benchmark", lambda mid: None)
    seat_timing._forget_memory_only()
    yield tmp_path
    seat_timing._forget_memory_only()


def test_a_real_load_is_remembered_and_survives_a_restart():
    assert seat_timing.last_load_seconds("gemma4:e4b") is None
    assert seat_timing.record("gemma4:e4b", 41.26)
    assert seat_timing.last_load_seconds("gemma4:e4b") == 41.3
    seat_timing._forget_memory_only()
    assert seat_timing.last_load_seconds("gemma4:e4b") == 41.3


@pytest.mark.parametrize("bad", [0, 0.0, -3, None, "soon", float("nan"), 99999])
def test_something_that_is_not_a_load_is_not_recorded(bad):
    assert seat_timing.record("m", bad) is False and seat_timing.last_load_seconds("m") is None


def test_a_damaged_file_starts_empty_not_broken(_clean):
    (_clean / "seat_load_times.json").write_text("{nope", encoding="utf-8")
    assert seat_timing.last_load_seconds("m") is None
    assert seat_timing.record("m", 5)


def test_the_arbiter_records_every_real_spawn():
    src = inspect.getsource(residency_arbiter.LlamaServerBackend._load_locked)
    assert "_seat_timing.record(model_id, took)" in src


def test_the_slate_says_what_switching_costs(monkeypatch):
    monkeypatch.setattr(ms, "_catalog", lambda: CAT)
    monkeypatch.setattr(ms, "_settings", lambda: {"orchestrator_model": "bonsai2:27b",
                                                  "model_routing": {"mode": "local_preferred"}})
    monkeypatch.setattr(ms, "_local_busy_reason", lambda mid: None)
    monkeypatch.setattr(ms, "_live_local", lambda: {"bonsai2:27b"})
    rows = {r["id"]: r for r in ms.slate()["models"]}
    assert rows["claude-opus-5-5"]["switch"] == {"cold": False, "expected_seconds": 0.0, "basis": "already available"}
    assert rows["bonsai2:27b"]["switch"]["cold"] is False
    assert rows["gemma4:e4b"]["switch"] == {"cold": True, "expected_seconds": None,
                                            "basis": "not loaded on this PC yet"}
    seat_timing.record("gemma4:e4b", 38.0)
    assert {r["id"]: r for r in ms.slate()["models"]}["gemma4:e4b"]["switch"] == {
        "cold": True, "expected_seconds": 38.0, "basis": "last load measured on this PC"}
    monkeypatch.setattr(ms, "_live_local", lambda: None)
    unk = {r["id"]: r for r in ms.slate()["models"]}["gemma4:e4b"]["switch"]
    assert unk["cold"] is None and unk["expected_seconds"] is None

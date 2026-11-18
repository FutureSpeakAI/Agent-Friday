"""Measured first-token wait per model: recorded from real answered calls, read by the model slate."""
from __future__ import annotations

import inspect

import pytest

from agent_friday.services import model_slate as ms, reasoning_trace, ttft_stats
from tests.unit.test_model_slate import CAT


@pytest.fixture(autouse=True)
def _own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ttft_stats, "_path", lambda: tmp_path / "first_token_times.json")
    monkeypatch.setattr(ms, "_benchmark", lambda mid: None)
    ttft_stats._forget_memory_only()
    yield
    ttft_stats._forget_memory_only()


def test_nothing_is_reported_until_a_call_was_timed():
    assert ttft_stats.summary("m") is None


def test_median_p90_and_cache_share_come_from_the_recorded_calls():
    for ms_wait, pre, cache in [(900, 100, 0), (300, 10, 90), (500, 50, 50), (400, 20, 80), (4000, 100, 0)]:
        assert ttft_stats.record("m", ms_wait, pre, cache)
    s = ttft_stats.summary("m")
    assert s["samples"] == 5 and s["median_ms"] == 500 and s["p90_ms"] == 4000
    assert s["cache_share"] == round((0 + .9 + .5 + .8 + 0) / 5, 3) and "measured" in s["basis"]


def test_only_the_last_window_counts():
    for i in range(ttft_stats._WINDOW + 5):
        ttft_stats.record("m", 1000 + i)
    assert ttft_stats.summary("m")["samples"] == ttft_stats._WINDOW


@pytest.mark.parametrize("bad", [0, -5, None, "fast", True, float("nan"), 10**9])
def test_nonsense_is_not_a_measurement(bad):
    assert ttft_stats.record("m", bad) is False and ttft_stats.summary("m") is None


def test_it_survives_a_restart_and_a_damaged_file_starts_empty(tmp_path):
    ttft_stats.record("m", 250, 10, 90)
    ttft_stats._forget_memory_only()
    assert ttft_stats.summary("m")["median_ms"] == 250
    (tmp_path / "first_token_times.json").write_text("{not json", encoding="utf-8")
    ttft_stats._forget_memory_only()
    assert ttft_stats.summary("m") is None
    assert ttft_stats.record("m", 120)


def test_a_call_that_reports_a_first_token_time_is_recorded_by_the_trace():
    assert "_ttft_stats.record(model, ttft_ms, prefill_tokens, cache_tokens)" in inspect.getsource(
        reasoning_trace.model_call)
    reasoning_trace.model_call("m2", ttft_ms=321, prefill_tokens=10, cache_tokens=30)
    assert ttft_stats.summary("m2")["median_ms"] == 321


def test_the_slate_shows_measured_wait_per_model(monkeypatch):
    monkeypatch.setattr(ms, "_settings", lambda: {"model_routing": {"mode": "local_preferred"}})
    monkeypatch.setattr(ms, "_local_busy_reason", lambda mid: None)
    monkeypatch.setattr(ms, "_live_local", lambda: set())
    monkeypatch.setattr(ms, "_catalog", lambda: CAT)
    ttft_stats.record("gemma4:e4b", 700, 10, 10)
    rows = {r["id"]: r for r in ms.slate()["models"]}
    assert rows["gemma4:e4b"]["measured"]["median_ms"] == 700
    assert rows["bonsai2:27b"]["measured"] is None


def test_a_damaged_saved_file_drops_only_the_damaged_part_and_record_never_raises(tmp_path, monkeypatch):
    big = "9" * 400
    monkeypatch.setattr(ttft_stats, "_path", lambda: tmp_path / "t.json")
    ttft_stats._forget_memory_only()
    (tmp_path / "t.json").write_text(
        '{"a": {"at": [1], "calls": [[500, null]]}, "b": {"at": 1, "calls": [[700, null], [700, "x"], [NaN, 0.5],'
        ' [-3, null], [800, 0.25]]}, "c": {"at": %s, "calls": [[900, null]]}}' % big, encoding="utf-8")
    assert ttft_stats.record("m", 400) is True
    assert ttft_stats.summary("a")["samples"] == 1                # a bad stamp costs the stamp, not the history
    b = ttft_stats.summary("b")
    assert b["samples"] == 2 and b["cache_share"] == 0.25         # only the sane calls count
    assert ttft_stats.summary("c")["samples"] == 1
    assert ttft_stats.record("m", float("inf")) is False and ttft_stats.record("m", True) is False
    ttft_stats._forget_memory_only()

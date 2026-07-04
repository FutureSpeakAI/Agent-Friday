"""Unit tests for the provider_health measurement plane (spec §8):
record()/stats() ring buffers, percentiles, error rates, and the circuit
breaker with cooldown + half-open.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import provider_health as ph


@pytest.fixture(autouse=True)
def _clean():
    ph.reset_stats()
    yield
    ph.reset_stats()


class TestRecordStats:
    def test_unknown_provider_has_unknown_availability(self):
        s = ph.stats("never-called")
        assert s["availability"] == "unknown"
        assert s["requests"] == 0

    def test_success_counting_and_latency(self):
        for ms in (100, 200, 300, 400, 500):
            ph.record("prov-a", True, latency_ms=ms, status=200)
        s = ph.stats("prov-a")
        assert s["requests"] == 5 and s["errors"] == 0
        assert s["error_rate"] == 0.0
        assert s["availability"] == "ok"
        assert s["latency_p50_ms"] == 300
        assert s["latency_p95_ms"] in (400, 500)
        assert s["last_ok_at"] is not None

    def test_error_rate_degraded(self):
        for _ in range(9):
            ph.record("prov-b", True, latency_ms=100)
        ph.record("prov-b", False, latency_ms=100, status=500)
        s = ph.stats("prov-b")
        assert s["errors"] == 1
        assert 0.05 <= s["error_rate"] < 0.25
        assert s["availability"] == "degraded"

    def test_heavy_errors_down(self):
        # Alternate so consecutive-failure breaker doesn't trip first — this
        # tests the ERROR-RATE path to "down".
        for i in range(10):
            ph.record("prov-c", i % 2 == 0, latency_ms=100, status=500)
        s = ph.stats("prov-c")
        assert s["error_rate"] >= 0.25
        assert s["availability"] == "down"

    def test_record_never_raises(self):
        ph.record(None, True)      # ignored, no crash
        ph.record("", False)
        ph.record("x", True, latency_ms="not-a-number" if False else 0)


class TestCircuitBreaker:
    def test_five_consecutive_failures_trip(self):
        for _ in range(5):
            ph.record("prov-d", False, latency_ms=50, status=502)
        s = ph.stats("prov-d")
        assert s["consecutive_failures"] == 5
        assert s["availability"] == "down"
        assert ph.availability("prov-d") == "down"

    def test_success_resets_breaker(self):
        for _ in range(5):
            ph.record("prov-e", False, latency_ms=50)
        assert ph.availability("prov-e") == "down"
        ph.record("prov-e", True, latency_ms=50)
        s = ph.stats("prov-e")
        assert s["consecutive_failures"] == 0
        # Breaker closed + last call good → at worst degraded (recovery path);
        # the lingering window error-rate keeps it out of full "ok".
        assert s["availability"] == "degraded"

    def test_cooldown_half_opens(self, monkeypatch):
        for _ in range(5):
            ph.record("prov-f", False, latency_ms=50)
        assert ph.availability("prov-f") == "down"
        # Age the trip past the cooldown → half-open reports degraded.
        with ph._STATS_LOCK:
            ph._TRIPPED_AT["prov-f"] -= (ph._BREAKER_COOLDOWN_S + 1)
        assert ph.availability("prov-f") == "degraded"

    def test_four_failures_do_not_trip(self):
        for _ in range(4):
            ph.record("prov-g", False, latency_ms=50)
        # 100% error rate in window → "down" via error rate is fine, but the
        # breaker itself must not have tripped.
        s = ph.stats("prov-g")
        assert s["consecutive_failures"] == 4
        with ph._STATS_LOCK:
            assert "prov-g" not in ph._TRIPPED_AT


class TestHealthOrdering:
    def test_health_order_demotes_down_provider(self):
        from agent_friday.services.model_router import _health_order
        for _ in range(5):
            ph.record("anthropic", False, latency_ms=50)
        attempts = [("cloud", "fn1", "m1"), ("openai", "fn2", None),
                    ("local", "fn3", None)]
        ordered = _health_order(attempts, None)
        assert [a[0] for a in ordered] == ["openai", "local", "cloud"]

    def test_health_order_stable_when_healthy(self):
        from agent_friday.services.model_router import _health_order
        attempts = [("openai", "fn2", "m"), ("cloud", "fn1", None),
                    ("local", "fn3", None)]
        assert [a[0] for a in _health_order(attempts, "openrouter")] == \
            ["openai", "cloud", "local"]

    def test_health_order_uses_routed_provider_name(self):
        from agent_friday.services.model_router import _health_order
        for _ in range(5):
            ph.record("openrouter", False, latency_ms=50)
        attempts = [("openai", "fn2", "m"), ("cloud", "fn1", None)]
        ordered = _health_order(attempts, "openrouter")
        assert [a[0] for a in ordered] == ["cloud", "openai"]


class TestAllStats:
    def test_all_stats_lists_recorded_providers(self):
        ph.record("prov-x", True, latency_ms=10)
        ph.record("prov-y", False, latency_ms=10)
        allstats = ph.all_stats()
        assert "prov-x" in allstats and "prov-y" in allstats


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

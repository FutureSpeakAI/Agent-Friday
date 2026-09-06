"""Gauntlet finding F66 (2026-09-04): cost_meter.timeseries() crashed with
TypeError the first time an unpriced-model call (cost_usd IS NULL,
UNPRICED_MODELS -- F50) landed inside the requested range.

summary() and by_schedule() sum cost_usd in SQL via COALESCE(SUM(...),0),
which silently and correctly treats NULL as "contributes nothing to the
total." timeseries() sums in Python (`b["usd"] += cost`), and nothing
skipped a None there -- `0.0 += None` raises TypeError, which the route
(routes/costs.py) catches and turns into a 500.

Found via F64's investigation: an order-dependent test failure
(tests/api/test_self_sufficient_routes.py::test_costs_timeseries_and_scheduled
failing only when the full suite ran, never in isolation) traced back to an
earlier test in the same session recording a real call for an unpriced
model (going through the real, shared cost_meter.DB_PATH), leaving exactly
one NULL-cost_usd row for `timeseries()` to trip over later. Confirmed by
direct reproduction: record() one unpriced-model call, call timeseries(),
watch it crash with the exact "unsupported operand type(s) for +=: 'float'
and 'NoneType'" message -- this is a real bug F50's own fix made newly
reachable, not a test artifact.
"""
from __future__ import annotations

import agent_friday.services.cost_meter as cost_meter


class TestTimeseriesHandlesUnpricedCalls:
    def setup_method(self, method):
        cost_meter.reset_for_tests()

    def teardown_method(self, method):
        cost_meter.reset_for_tests()

    def test_an_unpriced_call_does_not_crash_timeseries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cost_meter, "DB_PATH", tmp_path / "costs.db")
        cost_meter.reset_for_tests()

        cost_meter.record("cohere", "command-r", input_tokens=100, output_tokens=50)

        series = cost_meter.timeseries("month", "day")

        assert len(series) == 1
        assert series[0]["calls"] == 1

    def test_an_unpriced_call_is_counted_but_contributes_zero_to_the_bucket_total(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(cost_meter, "DB_PATH", tmp_path / "costs.db")
        cost_meter.reset_for_tests()

        cost_meter.record("anthropic", "claude-sonnet-5", input_tokens=1000, output_tokens=1000)
        cost_meter.record("cohere", "command-r", input_tokens=100, output_tokens=50)

        series = cost_meter.timeseries("month", "day")

        assert len(series) == 1
        bucket = series[0]
        assert bucket["calls"] == 2, "both calls should be counted"
        assert bucket["usd"] > 0, (
            "the priced call's real cost must still be reflected -- the fix "
            "must skip None, not zero out the whole bucket"
        )

    def test_a_bucket_with_only_unpriced_calls_has_zero_usd_not_a_crash(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(cost_meter, "DB_PATH", tmp_path / "costs.db")
        cost_meter.reset_for_tests()

        cost_meter.record("cohere", "command-r", input_tokens=100, output_tokens=50)
        cost_meter.record("groq", "llama-3.3-70b-versatile", input_tokens=200, output_tokens=100)

        series = cost_meter.timeseries("month", "day")

        assert len(series) == 1
        assert series[0]["calls"] == 2
        assert series[0]["usd"] == 0.0

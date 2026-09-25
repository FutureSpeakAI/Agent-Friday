"""Gauntlet finding Q7 part (b) (findings.jsonl Q7).

creations.py's _daily_budget_remaining() computes
`ceiling - cost_meter._rolling_spend()[0]` (today's total spend). That is
correct arithmetic only if creative generation writes to cost_meter:
otherwise _rolling_spend() structurally excludes every dollar of
creative-generation spend, the gate's number is silently near-zero, and it
keeps offering the most expensive autonomous mode no matter how much media
has been generated that day.

Q7 part (a) wires cost_meter.meter()/record() into every creative_engine.py
and music_engine.py call site. _rolling_spend() sums ALL rows in cost_calls
for today, with no filter on provider or kind, so it picks up creative
spend automatically once something writes it.

This test proves that end-to-end, without mocking cost_meter: it records a
real creative-generation charge through the same public API creative_engine
calls, then asserts _daily_budget_remaining() reflects it. If this ever
regresses (e.g. a refactor makes _rolling_spend provider-specific, or
_daily_budget_remaining starts reading a different source), this fails and
says so directly rather than looking green while quietly lying.

Isolation: cost_meter.record() must not write to the REAL, SHARED
cost_meter.DB_PATH (the on-disk costs.db every other test in the same
pytest PROCESS reads). Each run would permanently add $0.25 to today's
spend for the rest of the process -- invisible when tests/gauntlet/ runs on
its own, but an order-dependent failure under a bare `pytest`
(testpaths=tests). The fix is the isolation primitive cost_meter.py ships
for this purpose, as in test_cost_meter_timeseries_handles_unpriced_calls.py:
reset_for_tests() plus a monkeypatched, disposable DB_PATH.
"""
from __future__ import annotations

from agent_friday.services import cost_meter
from agent_friday.services.creations import _daily_budget_remaining


class TestDailyBudgetGateReflectsCreativeSpend:
    def setup_method(self, method):
        cost_meter.reset_for_tests()

    def teardown_method(self, method):
        cost_meter.reset_for_tests()

    def test_daily_budget_gate_drops_after_a_real_creative_charge(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(cost_meter, "DB_PATH", tmp_path / "costs.db")
        cost_meter.reset_for_tests()
        monkeypatch.setattr(
            "agent_friday.core._load_settings",
            lambda: {"daily_creation_budget_usd": 1.00},
        )

        before = _daily_budget_remaining()
        assert before > 0.5, (
            "test assumes a mostly-unspent budget in its own isolated "
            "costs.db -- if this fails, the isolation itself (DB_PATH "
            "monkeypatch + reset_for_tests) did not take effect, not that "
            "another test polluted a shared database"
        )

        cost_meter.record("gemini", "gemini-2.5-flash-image",
                           input_tokens=0, output_tokens=0,
                           cost_usd=0.25, kind="creative")

        after = _daily_budget_remaining()
        assert after == round(before - 0.25, 4), (
            f"recording a real ${0.25} creative-generation charge through "
            "cost_meter did not reduce _daily_budget_remaining() by that "
            f"amount (before={before}, after={after}) -- the daily-creation "
            "budget gate is not actually reading real creative spend, so it "
            "will keep offering expensive autonomous modes regardless of what "
            "Friday has already spent today (Q7 part b)"
        )

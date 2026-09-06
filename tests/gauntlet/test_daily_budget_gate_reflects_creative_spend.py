"""Gauntlet finding Q7 part (b) — resolved under Stephen's 2026-09-04
delegation (docs/audits/gauntlet-2026-09-03/progress.md; findings.jsonl Q7).

The original finding: creations.py's _daily_budget_remaining() computes
`ceiling - cost_meter._rolling_spend()[0]` (today's total spend), which is
correct arithmetic -- but since creative_engine.py/music_engine.py never
called into cost_meter at all, _rolling_spend() structurally excluded every
dollar of real creative-generation spend, so the gate looked functional
while its number was silently wrong (always near-zero), and it would keep
offering the most expensive autonomous mode no matter how much media had
actually been generated that day.

Q7 part (a) (a separate fix, already landed -- see findings.jsonl) wired
cost_meter.meter()/record() into every creative_engine.py and
music_engine.py call site named by this finding. _rolling_spend() itself
was never broken: it sums ALL rows in cost_calls for today, with no filter
on provider or kind (cost_meter.py's _rolling_spend, verified by reading
it), so it was always going to pick up creative spend automatically once
something started writing it.

This test proves that end-to-end, without mocking cost_meter: it records a
real creative-generation charge through the same public API creative_engine
now calls, then asserts _daily_budget_remaining() reflects it. If this ever
regresses (e.g. a future refactor makes _rolling_spend provider-specific,
or _daily_budget_remaining starts reading a different source), this fails
and says so directly rather than looking green while quietly lying again.

CORRECTION (second cold-verification pass, 2026-09-05): this test used to
call cost_meter.record() against the REAL, SHARED cost_meter.DB_PATH (the
same on-disk costs.db every other test in the same pytest PROCESS reads),
with no cleanup afterward -- so every run of this test permanently added
$0.25 to "today"'s cumulative spend for the rest of that process's tests.
Invisible when tests/gauntlet/ runs as its own separate pytest invocation
(a fresh isolated temp home every time), but a genuine, self-inflicted
order-dependent failure the moment this file shares a process with any
other test that reads today's spend -- exactly what a bare `pytest`
(pytest.ini's own documented, testpaths=tests invocation) does. This is
the same class of bug F66's test file (test_cost_meter_timeseries_handles_
unpriced_calls.py) already root-caused and fixed for a different pair of
tests, via the same isolation primitive cost_meter.py itself ships for
exactly this purpose: reset_for_tests() plus a monkeypatched, disposable
DB_PATH. Applied here too, rather than inventing a second mechanism.
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

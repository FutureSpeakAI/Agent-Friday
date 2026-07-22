"""Unit tests for services/goals.py's per-goal budget hard-stop
(V6_WHOLENESS_SPEC.md §4 Phase 5 / AUTONOMY_SPEC.md §8 A3).

The AUTHORITATIVE cap is the goal's own cumulative spent_mψ vs budget_cap_mψ
(duration-independent — see goals.py's module docstring, "Budget model", for
why budget_enforcer's calendar-month reset can't be the sole enforcement
point). budget_enforcer.reserve_budget/release_budget are ALSO called for
cross-goal observability in the existing Budgets panel — verified here too,
but as reuse, not as the deciding factor.

All milestone action descriptions here use a leading "Draft"/"Write" verb so
the Q3 approval gate auto-proceeds (see test_approval_gate.py for the gate
itself) — these tests isolate the budget variable specifically.
"""
from __future__ import annotations

import pytest

from agent_friday.services import goals


@pytest.fixture(autouse=True)
def _iso_goals(tmp_path, monkeypatch):
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield
    goals.set_executor(None)


def _passing_executor(cost_mψ=500):
    calls = []

    def _fn(goal, milestone, *, critique_hint=""):
        calls.append(1)
        return {"ok": True, "text": "a fine draft", "cost_mψ": cost_mψ}
    _fn.calls = calls
    return _fn


def _always_pass(monkeypatch):
    monkeypatch.setattr(goals.qa_gates, "evaluate_text",
                       lambda content, intent, **kw: {
                           "status": "ok", "passed": True, "score": 0.9,
                           "threshold": 0.7, "critique": "", "suggestions": ""})


class TestHardStop:
    def test_estimate_exceeding_cap_blocks_before_execution(self, monkeypatch):
        _always_pass(monkeypatch)
        g = goals.create_goal(title="Draft a capped goal", status="active", budget_cap_mψ=500,
                              milestones=[{"name": "Draft the memo", "cost_estimate_mψ": 1000}])
        mid = g["milestones"][0]["milestone_id"]
        executor = _passing_executor()
        goals.set_executor(executor)

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["ok"] is False
        assert result["status"] == "budget_exceeded"
        assert len(executor.calls) == 0  # never even dispatched

        updated = goals.get_goal(g["goal_id"])
        assert updated["milestones"][0]["status"] == "blocked"
        assert updated["spent_mψ"] == 0

    def test_budget_blocked_receipt_is_recorded(self, monkeypatch):
        _always_pass(monkeypatch)
        g = goals.create_goal(title="Draft a capped goal", status="active", budget_cap_mψ=100,
                              milestones=[{"name": "Draft the memo", "cost_estimate_mψ": 1000}])
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_passing_executor())
        goals.run_milestone(g["goal_id"], mid)
        updated = goals.get_goal(g["goal_id"])
        assert len(updated["receipts"]) == 1
        receipt = updated["receipts"][0]
        assert receipt["kind"] == "budget_blocked"
        assert goals.verify_receipt(receipt) is True

    def test_zero_cap_means_unlimited(self, monkeypatch):
        _always_pass(monkeypatch)
        g = goals.create_goal(title="Draft an uncapped goal", status="active", budget_cap_mψ=0,
                              milestones=[{"name": "Draft something huge", "cost_estimate_mψ": 10_000_000}])
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_passing_executor(cost_mψ=10_000_000))
        result = goals.run_milestone(g["goal_id"], mid)
        assert result["ok"] is True
        assert result["status"] == "done"

    def test_cumulative_spend_across_milestones_hard_stops_the_second(self, monkeypatch):
        _always_pass(monkeypatch)
        g = goals.create_goal(
            title="Draft a two-milestone capped goal", status="active", budget_cap_mψ=800,
            milestones=[
                {"name": "Draft part one", "cost_estimate_mψ": 500},
                {"name": "Draft part two", "cost_estimate_mψ": 500},
            ])
        m1, m2 = g["milestones"][0]["milestone_id"], g["milestones"][1]["milestone_id"]
        goals.set_executor(_passing_executor(cost_mψ=500))

        first = goals.run_milestone(g["goal_id"], m1)
        assert first["status"] == "done"
        assert goals.get_goal(g["goal_id"])["spent_mψ"] == 500

        second = goals.run_milestone(g["goal_id"], m2)
        assert second["status"] == "budget_exceeded"
        assert goals.get_goal(g["goal_id"])["spent_mψ"] == 500  # unchanged — never spent

    def test_spent_reflects_actual_cost_not_estimate(self, monkeypatch):
        """A milestone whose actual executor cost is LOWER than its
        cost_estimate_mψ only ever debits the goal for what was actually
        spent, not the reserved estimate."""
        _always_pass(monkeypatch)
        g = goals.create_goal(title="Draft with a generous estimate", status="active",
                              budget_cap_mψ=1000,
                              milestones=[{"name": "Draft it", "cost_estimate_mψ": 900}])
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_passing_executor(cost_mψ=50))
        goals.run_milestone(g["goal_id"], mid)
        assert goals.get_goal(g["goal_id"])["spent_mψ"] == 50

    def test_estimate_never_reserved_negative_on_get(self):
        """Sanity check for the atomic-reservation rewrite: get_goal never
        sees a negative spent_mψ from the reserve/release escrow dance."""
        g = goals.create_goal(title="Draft a sanity-check goal", status="active",
                              budget_cap_mψ=1000,
                              milestones=[{"name": "Draft it", "cost_estimate_mψ": 500}])
        assert g["spent_mψ"] == 0
        assert goals._reserve_goal_budget(g["goal_id"], 500) is True
        goals._release_goal_budget(g["goal_id"], 500)
        assert goals.get_goal(g["goal_id"])["spent_mψ"] == 0

    def test_reserve_and_release_are_called_on_budget_enforcer(self, monkeypatch):
        """Reuse check: budget_enforcer.reserve_budget/release_budget are
        called for the pseudo-workspace 'goal:<goal_id>' (cross-goal
        observability in the Budgets panel), even though the hard-stop
        decision itself is this module's own ledger."""
        _always_pass(monkeypatch)
        calls = {"reserve": [], "release": []}

        class _FakeBE:
            @staticmethod
            def reserve_budget(workspace, amount):
                calls["reserve"].append((workspace, amount))
                return True

            @staticmethod
            def release_budget(workspace, amount):
                calls["release"].append((workspace, amount))

        import agent_friday.services.budget_enforcer as be
        monkeypatch.setattr(be, "reserve_budget", _FakeBE.reserve_budget)
        monkeypatch.setattr(be, "release_budget", _FakeBE.release_budget)

        g = goals.create_goal(title="Draft with tracked budget", status="active", budget_cap_mψ=1000,
                              milestones=[{"name": "Draft it", "cost_estimate_mψ": 900}])
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_passing_executor(cost_mψ=50))
        goals.run_milestone(g["goal_id"], mid)

        assert calls["reserve"] == [(f"goal:{g['goal_id']}", 900)]
        assert calls["release"] == [(f"goal:{g['goal_id']}", 850)]  # 900 estimate - 50 actual


# ═══════════════════════════════════════════════════════════════════════════
#  Regression: the hard-stop must hold WITHIN a single milestone's own
#  bounded verify->repair loop, not just once before it (AUTONOMY_SPEC A3
#  finding). Previously _reserve_goal_budget was checked exactly once, before
#  the loop, against a single attempt's cost_estimate_mψ — the repair loop
#  itself (up to max_repair_retries+1 real attempts) then accumulated actual
#  executor cost with no re-check between attempts, so a milestone that kept
#  failing verification could spend up to ~(max_repair_retries+1)x its
#  approved estimate before the cap was ever enforced again.
# ═══════════════════════════════════════════════════════════════════════════

class TestHardStopAcrossRepairLoop:
    def _always_fail(self, monkeypatch):
        monkeypatch.setattr(goals.qa_gates, "evaluate_text",
                           lambda content, intent, **kw: {
                               "status": "ok", "passed": False, "score": 0.3,
                               "threshold": 0.7, "critique": "never good enough",
                               "suggestions": "try again"})

    def test_budget_cap_hard_stops_within_a_single_milestones_repair_loop(self, monkeypatch):
        """goal cap == the milestone's own single-attempt cost_estimate_mψ
        (so the PRE-flight check at run_milestone's top passes: 0+1000 is not
        > 1000), qa_gates always fails, and the executor genuinely 'ok's
        with cost_mψ=1000 every attempt (mirrors this module's own
        test_persistent_failure_escalates_never_marks_done fixture pattern,
        just with a nonzero cost). With DEFAULT_MAX_REPAIR_RETRIES=2,
        run_milestone would previously execute all 3 attempts before
        escalating, writing spent_mψ=3000 -- 3x the declared hard cap.
        The goal's OWN cumulative spend must never exceed its own cap, and
        the loop must stop dispatching BEFORE spending past it."""
        self._always_fail(monkeypatch)
        g = goals.create_goal(title="Draft a capped goal that never verifies",
                              status="active", budget_cap_mψ=1000,
                              milestones=[{"name": "Draft the memo", "cost_estimate_mψ": 1000}])
        mid = g["milestones"][0]["milestone_id"]
        executor = _passing_executor(cost_mψ=1000)
        goals.set_executor(executor)

        result = goals.run_milestone(g["goal_id"], mid)

        updated = goals.get_goal(g["goal_id"])
        assert updated["spent_mψ"] <= 1000, (
            f"goal spent {updated['spent_mψ']} mψ against a declared cap of "
            f"1000 mψ -- the hard-stop must never be exceeded")
        # Only the ONE attempt the initial reservation actually paid for
        # should have run; the repair loop must not reach its full
        # max_repair_retries+1 bound once the cap is exhausted.
        assert len(executor.calls) == 1
        assert result["ok"] is False
        assert result.get("budget_exhausted_mid_repair") is True
        assert updated["milestones"][0]["status"] == "escalated"
        assert updated["milestones"][0]["status"] != "done"

    def test_budget_exhaustion_mid_repair_still_escalates_to_a_human_gate(self, monkeypatch):
        """A milestone that hits the cap mid-repair isn't silently dropped —
        it still lands in the SAME human approval queue any other exhausted
        repair loop does, with a receipt explaining why."""
        self._always_fail(monkeypatch)
        g = goals.create_goal(title="Draft another capped goal", status="active",
                              budget_cap_mψ=1000,
                              milestones=[{"name": "Draft the report", "cost_estimate_mψ": 1000}])
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_passing_executor(cost_mψ=1000))

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["status"] == "escalated"
        appr = goals.approvals.get_approval(result["approval_id"])
        assert appr is not None
        assert appr["kind"] == "goal_milestone_escalation"
        assert appr["status"] == "pending"
        assert appr["payload"]["budget_exhausted_mid_repair"] is True

        updated = goals.get_goal(g["goal_id"])
        receipt = updated["receipts"][-1]
        assert goals.verify_receipt(receipt) is True
        assert "budget" in receipt["note"].lower()

    def test_repair_loop_never_exceeds_cap_even_with_room_for_a_partial_attempt(self, monkeypatch):
        """A cap that leaves room for the first attempt but not a second must
        still stop the loop before the second attempt's reservation, not
        merely before some LATER attempt."""
        self._always_fail(monkeypatch)
        g = goals.create_goal(title="Draft a goal with a tight cap", status="active",
                              budget_cap_mψ=150,
                              milestones=[{"name": "Draft the memo", "cost_estimate_mψ": 100}])
        mid = g["milestones"][0]["milestone_id"]
        executor = _passing_executor(cost_mψ=100)
        goals.set_executor(executor)

        goals.run_milestone(g["goal_id"], mid)

        # cap=150: attempt 1 reserves 100 (0+100<=150, ok); attempt 2 would
        # need another 100 (100+100=200 > 150) -- must stop there.
        assert len(executor.calls) == 1
        assert goals.get_goal(g["goal_id"])["spent_mψ"] <= 150


# ═══════════════════════════════════════════════════════════════════════════
#  Regression: _reserve_goal_budget must be an ATOMIC check-and-commit so two
#  concurrent callers (e.g. the scheduler's goal_milestones_tick beat racing
#  an on-demand POST .../milestones/<id>/run for a SIBLING milestone of the
#  SAME goal) cannot both observe the same unreserved headroom and both
#  proceed, together overspending past the declared cap (AUTONOMY_SPEC A3
#  finding). Previously this was a plain read-then-compare with the actual
#  debit deferred until the milestone finished (up to milestone_timeout_
#  seconds later) — two successive calls against a cap of 100 both returned
#  True with spent_mψ still 0 after both.
# ═══════════════════════════════════════════════════════════════════════════

class TestReservationIsAtomicAcrossConcurrentCallers:
    def test_two_reservations_against_the_same_cap_cannot_both_succeed(self):
        g = goals.create_goal(title="Draft a two-milestone capped goal",
                              status="active", budget_cap_mψ=100,
                              milestones=[{"name": "Draft A", "cost_estimate_mψ": 100},
                                         {"name": "Draft B", "cost_estimate_mψ": 100}])

        first_ok = goals._reserve_goal_budget(g["goal_id"], 100)
        second_ok = goals._reserve_goal_budget(g["goal_id"], 100)

        assert first_ok is True
        assert second_ok is False, (
            "a second reservation against an already-fully-committed cap "
            "must be rejected -- it must NOT see stale, pre-commit headroom")
        assert goals.get_goal(g["goal_id"])["spent_mψ"] == 100

    def test_concurrent_sibling_milestones_of_the_same_goal_cannot_both_overspend(self, monkeypatch):
        """End-to-end version of the same property: two DIFFERENT milestones
        of the same goal, cap == exactly one milestone's cost, both dispatched
        (simulating the scheduler tick and an on-demand API call racing each
        other) -- only one may actually complete; the other must be
        budget-blocked, and the goal's total spend must never exceed the cap."""
        _always_pass(monkeypatch)
        g = goals.create_goal(
            title="Draft a two-milestone capped goal", status="active", budget_cap_mψ=100,
            milestones=[
                {"name": "Draft part one", "cost_estimate_mψ": 100},
                {"name": "Draft part two", "cost_estimate_mψ": 100},
            ])
        m1, m2 = g["milestones"][0]["milestone_id"], g["milestones"][1]["milestone_id"]
        goals.set_executor(_passing_executor(cost_mψ=100))

        first = goals.run_milestone(g["goal_id"], m1)
        second = goals.run_milestone(g["goal_id"], m2)

        outcomes = {first["status"], second["status"]}
        assert "done" in outcomes
        assert "budget_exceeded" in outcomes
        assert goals.get_goal(g["goal_id"])["spent_mψ"] <= 100

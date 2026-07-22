"""Unit tests for services/goals.py's run_milestone() verify -> notice ->
repair -> escalate loop (V6_WHOLENESS_SPEC.md §4 Phase 5 / AUTONOMY_SPEC.md
§8 A3). The one invariant every test here guards: a milestone is NEVER
marked "done" on a failed verification.

The milestone-work executor is swapped via goals.set_executor() (the test
seam, mirrors interest_model.register_goals_provider) so these tests never
spin a real background agent thread. qa_gates.evaluate_text is monkeypatched
directly on the qa_gates module object goals.py holds a reference to
(`goals.qa_gates`) so the verification VERDICT is fully deterministic —
these are internal-drafting-style action descriptions (a leading "Draft"/
"Write" verb), so the Q3 gate auto-proceeds and the tests exercise the
verification loop in isolation from the approval gate (see
test_approval_gate.py for that).
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


def _make_goal(**overrides):
    kwargs = dict(
        title="Write the weekly newsletter draft",
        description="Draft this week's internal newsletter",
        success_criteria="mentions the newsletter and is at least one sentence",
        status="active",
        milestones=[{"name": "Draft this week's issue"}],
    )
    kwargs.update(overrides)
    return goals.create_goal(**kwargs)


def _executor_recording_hints(text="a solid newsletter draft"):
    """Fake executor that always 'succeeds' at running, recording every
    critique_hint it was called with (so tests can assert repair actually
    folds the critique in)."""
    calls = []

    def _fn(goal, milestone, *, critique_hint=""):
        calls.append(critique_hint)
        return {"ok": True, "text": f"{text} (attempt {len(calls)})", "cost_mψ": 100}
    _fn.calls = calls
    return _fn


def _verdict_sequence(*passed_flags, score_fail=0.3, score_pass=0.9):
    """Fake qa_gates.evaluate_text returning one verdict per call, cycling
    through passed_flags in order (extra calls repeat the last flag)."""
    calls = {"n": 0}

    def _fn(content, intent, *, workspace="", extra_criteria=""):
        i = min(calls["n"], len(passed_flags) - 1)
        passed = passed_flags[i]
        calls["n"] += 1
        return {
            "status": "ok", "passed": passed,
            "score": score_pass if passed else score_fail, "threshold": 0.7,
            "critique": "" if passed else f"attempt {calls['n']} missed the mark",
            "suggestions": "" if passed else "be more specific",
        }
    _fn.calls = calls
    return _fn


# ═══════════════════════════════════════════════════════════════════════════
#  Immediate pass — no repair needed
# ═══════════════════════════════════════════════════════════════════════════

class TestImmediatePass:
    def test_passing_verification_marks_done_with_signed_receipt(self, monkeypatch):
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(True))

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["ok"] is True
        assert result["status"] == "done"
        assert result["verification"]["passed"] is True

        updated = goals.get_goal(g["goal_id"])
        ms = updated["milestones"][0]
        assert ms["status"] == "done"
        assert ms["done_at"] is not None
        assert ms["verification"]["passed"] is True

        assert len(updated["receipts"]) == 1
        receipt = updated["receipts"][0]
        assert receipt["kind"] == "milestone_complete"
        assert receipt["verified"]["passed"] is True
        assert goals.verify_receipt(receipt) is True

    def test_single_milestone_goal_auto_completes(self, monkeypatch):
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(True))
        goals.run_milestone(g["goal_id"], mid)
        assert goals.get_goal(g["goal_id"])["status"] == "completed"

    def test_multi_milestone_goal_stays_active_until_all_done(self, monkeypatch):
        g = _make_goal(milestones=[{"name": "One"}, {"name": "Two"}])
        m1, m2 = g["milestones"][0]["milestone_id"], g["milestones"][1]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(True))
        goals.run_milestone(g["goal_id"], m1)
        assert goals.get_goal(g["goal_id"])["status"] == "active"
        goals.run_milestone(g["goal_id"], m2)
        assert goals.get_goal(g["goal_id"])["status"] == "completed"

    def test_second_call_on_already_done_milestone_is_a_noop(self, monkeypatch):
        # Two milestones so the GOAL stays "active" after the first completes
        # (a single-milestone goal auto-completes — see
        # TestImmediatePass.test_single_milestone_goal_auto_completes — which
        # would make a second run_milestone call fail on "not active" before
        # ever reaching the already-done check this test targets).
        g = _make_goal(milestones=[{"name": "One"}, {"name": "Two"}])
        mid = g["milestones"][0]["milestone_id"]
        executor = _executor_recording_hints()
        goals.set_executor(executor)
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(True))
        goals.run_milestone(g["goal_id"], mid)
        result = goals.run_milestone(g["goal_id"], mid)
        assert result == {"ok": True, "status": "already_done"}
        assert len(executor.calls) == 1  # never re-dispatched


# ═══════════════════════════════════════════════════════════════════════════
#  Repair — a failing attempt is followed by an improved, passing one
# ═══════════════════════════════════════════════════════════════════════════

class TestRepair:
    def test_fail_then_pass_within_retry_budget_ends_done(self, monkeypatch):
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        executor = _executor_recording_hints()
        goals.set_executor(executor)
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False, True))

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["ok"] is True
        assert result["status"] == "done"
        assert len(executor.calls) == 2  # one failed attempt, one repaired attempt
        # The SECOND attempt's critique_hint must fold in the FIRST attempt's
        # critique — that is the "regenerate with critique folded in" contract.
        assert executor.calls[0] == ""
        assert "attempt 1 missed the mark" in executor.calls[1]
        assert "be more specific" in executor.calls[1]

    def test_never_marks_done_mid_repair(self, monkeypatch):
        """Regression guard for the core invariant: while repair is still in
        flight (not yet exhausted, not yet passed), the milestone must never
        read as 'done' even transiently. Exercises the REAL run_milestone
        loop (not just the local _verdict_sequence fixture helper in
        isolation) by spying on every goals._update_milestone call's
        `status` kwarg — 'done' must never appear before the LAST such call,
        which only happens after a genuine pass."""
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text",
                           _verdict_sequence(False, False, True))

        statuses_seen = []
        real_update = goals._update_milestone

        def _spy_update(goal_id, milestone_id, **fields):
            if "status" in fields:
                statuses_seen.append(fields["status"])
            return real_update(goal_id, milestone_id, **fields)
        monkeypatch.setattr(goals, "_update_milestone", _spy_update)

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["ok"] is True
        assert result["status"] == "done"
        assert statuses_seen, "expected at least one status transition to be recorded"
        assert statuses_seen[-1] == "done"
        assert "done" not in statuses_seen[:-1], (
            f"'done' appeared before the loop's real final outcome: {statuses_seen}")

    def test_attempts_counter_persists_across_the_call(self, monkeypatch):
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False, True))
        goals.run_milestone(g["goal_id"], mid)
        ms = goals.get_goal(g["goal_id"])["milestones"][0]
        assert ms["attempts"] == 2

    def test_executor_failure_folds_error_into_next_critique(self, monkeypatch):
        calls = []

        def _flaky_executor(goal, milestone, *, critique_hint=""):
            calls.append(critique_hint)
            if len(calls) == 1:
                return {"ok": False, "text": "", "cost_mψ": 0, "error": "tool crashed"}
            return {"ok": True, "text": "recovered output", "cost_mψ": 50}

        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_flaky_executor)
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(True))

        result = goals.run_milestone(g["goal_id"], mid)
        assert result["ok"] is True
        assert result["status"] == "done"
        assert "tool crashed" in calls[1]


# ═══════════════════════════════════════════════════════════════════════════
#  Escalate — repair exhausted, still failing -> human gate, NEVER done
# ═══════════════════════════════════════════════════════════════════════════

class TestEscalation:
    def test_persistent_failure_escalates_never_marks_done(self, monkeypatch):
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        executor = _executor_recording_hints()
        goals.set_executor(executor)
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False))  # always fails

        result = goals.run_milestone(g["goal_id"], mid)

        assert result["ok"] is False
        assert result["status"] == "escalated"
        assert "approval_id" in result

        updated = goals.get_goal(g["goal_id"])
        ms = updated["milestones"][0]
        assert ms["status"] == "escalated"
        assert ms["status"] != "done"
        assert ms["done_at"] is None

    def test_escalation_is_bounded_by_max_repair_retries(self, monkeypatch):
        g = _make_goal()
        goals.update_goal(g["goal_id"], {"max_repair_retries": 2})
        mid = g["milestones"][0]["milestone_id"]
        executor = _executor_recording_hints()
        goals.set_executor(executor)
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False))
        goals.run_milestone(g["goal_id"], mid)
        assert len(executor.calls) == 3  # 1 initial + 2 repairs, then stop

    def test_escalation_creates_a_pending_approval_card(self, monkeypatch):
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False))
        result = goals.run_milestone(g["goal_id"], mid)

        appr = goals.approvals.get_approval(result["approval_id"])
        assert appr is not None
        assert appr["kind"] == "goal_milestone_escalation"
        assert appr["status"] == "pending"

    def test_escalation_produces_a_signed_receipt_never_marked_passed(self, monkeypatch):
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False))
        goals.run_milestone(g["goal_id"], mid)

        updated = goals.get_goal(g["goal_id"])
        receipt = updated["receipts"][-1]
        assert receipt["kind"] == "verification_failed_escalated"
        assert receipt["verified"]["passed"] is False
        assert goals.verify_receipt(receipt) is True

    def test_goal_stays_active_not_failed_on_escalation(self, monkeypatch):
        """Escalating one milestone doesn't unilaterally fail the whole
        goal — that is an owner decision (or a later milestone-failure
        aggregation policy), not this loop's to make."""
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False))
        goals.run_milestone(g["goal_id"], mid)
        assert goals.get_goal(g["goal_id"])["status"] == "active"

    def test_human_approval_of_escalation_marks_done_with_override_flag(self, monkeypatch):
        """The ONLY path that can mark an escalated milestone done: an
        explicit human decision, and even then the verification record is
        flagged human_override=True — never confusable with a real pass."""
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False))
        result = goals.run_milestone(g["goal_id"], mid)
        appr_id = result["approval_id"]

        goals.approvals.decide(appr_id, "approve", decided_by="owner")

        updated = goals.get_goal(g["goal_id"])
        ms = updated["milestones"][0]
        assert ms["status"] == "done"
        assert ms["verification"]["passed"] is True
        assert ms["verification"]["human_override"] is True
        kinds = [r["kind"] for r in updated["receipts"]]
        assert "milestone_human_approved_override" in kinds

    def test_denying_an_escalation_marks_milestone_failed(self, monkeypatch):
        g = _make_goal()
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _verdict_sequence(False))
        result = goals.run_milestone(g["goal_id"], mid)
        goals.approvals.decide(result["approval_id"], "deny")
        assert goals.get_goal(g["goal_id"])["milestones"][0]["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════════
#  verification_mode="manual" — never auto-verified, always a human confirm
# ═══════════════════════════════════════════════════════════════════════════

class TestManualVerificationMode:
    def test_manual_mode_never_calls_qa_gates(self, monkeypatch):
        called = {"n": 0}

        def _spy(*a, **k):
            called["n"] += 1
            return {"status": "ok", "passed": True, "score": 1.0, "threshold": 0.7,
                    "critique": "", "suggestions": ""}
        monkeypatch.setattr(goals.qa_gates, "evaluate_text", _spy)

        g = _make_goal(verification_mode="manual")
        mid = g["milestones"][0]["milestone_id"]
        goals.set_executor(_executor_recording_hints())

        result = goals.run_milestone(g["goal_id"], mid)
        assert result["status"] == "pending_manual_confirmation"
        assert called["n"] == 0
        ms = goals.get_goal(g["goal_id"])["milestones"][0]
        assert ms["status"] == "verifying"
        assert ms["status"] != "done"

    def test_manual_mode_approval_marks_done(self, monkeypatch):
        goals.set_executor(_executor_recording_hints())
        g = _make_goal(verification_mode="manual")
        mid = g["milestones"][0]["milestone_id"]
        result = goals.run_milestone(g["goal_id"], mid)

        goals.approvals.decide(result["approval_id"], "approve")

        ms = goals.get_goal(g["goal_id"])["milestones"][0]
        assert ms["status"] == "done"
        assert ms["verification"]["passed"] is True

    def test_manual_mode_denial_marks_failed(self, monkeypatch):
        goals.set_executor(_executor_recording_hints())
        g = _make_goal(verification_mode="manual")
        mid = g["milestones"][0]["milestone_id"]
        result = goals.run_milestone(g["goal_id"], mid)
        goals.approvals.decide(result["approval_id"], "deny")
        assert goals.get_goal(g["goal_id"])["milestones"][0]["status"] == "failed"

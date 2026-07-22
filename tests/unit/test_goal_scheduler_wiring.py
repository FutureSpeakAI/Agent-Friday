"""Unit tests for the scheduler-driven automation half of services/goals.py
(V6_WHOLENESS_SPEC.md §4 Phase 5 / AUTONOMY_SPEC.md §8 A3, item 2 wiring +
item 6 weekly review) — run_due_milestones (the tick services/scheduler.py's
'goal_milestones_tick' builtin calls), run_weekly_review/latest_review (the
'goals_weekly_review' builtin), and confirmation that all three new builtins
(goal_milestones_tick, goals_weekly_review, approvals_expiry_sweep) actually
register into services/scheduler.py's BUILTIN_TASKS.
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


def _always_pass(monkeypatch):
    monkeypatch.setattr(goals.qa_gates, "evaluate_text",
                       lambda content, intent, **kw: {
                           "status": "ok", "passed": True, "score": 0.9,
                           "threshold": 0.7, "critique": "", "suggestions": ""})


def _passing_executor():
    return lambda g, m, *, critique_hint="": {"ok": True, "text": "draft output", "cost_mψ": 10}


class TestRunDueMilestones:
    def test_advances_only_due_pending_milestones(self, monkeypatch):
        _always_pass(monkeypatch)
        goals.set_executor(_passing_executor())
        past = goals._iso_from_ts(__import__("time").time() - 3600)
        future = goals._iso_from_ts(__import__("time").time() + 3600)
        g = goals.create_goal(title="Draft a due-milestones goal", status="active",
                              milestones=[{"name": "Overdue", "due": past},
                                          {"name": "Not yet due", "due": future}])
        result = goals.run_due_milestones()
        assert result["changed"] is True
        assert len(result["processed"]) == 1

        updated = goals.get_goal(g["goal_id"])
        overdue_ms = next(m for m in updated["milestones"] if m["name"] == "Overdue")
        not_due_ms = next(m for m in updated["milestones"] if m["name"] == "Not yet due")
        assert overdue_ms["status"] == "done"
        assert not_due_ms["status"] == "pending"

    def test_ignores_milestones_without_a_due_date(self, monkeypatch):
        _always_pass(monkeypatch)
        goals.set_executor(_passing_executor())
        goals.create_goal(title="Draft an undated goal", status="active",
                          milestones=[{"name": "No due date"}])
        result = goals.run_due_milestones()
        assert result["changed"] is False

    def test_ignores_non_active_goals(self, monkeypatch):
        _always_pass(monkeypatch)
        goals.set_executor(_passing_executor())
        past = goals._iso_from_ts(__import__("time").time() - 3600)
        goals.create_goal(title="Draft a proposed goal", status="proposed",
                          milestones=[{"name": "Overdue but not active", "due": past}])
        result = goals.run_due_milestones()
        assert result["changed"] is False

    def test_respects_max_per_tick(self, monkeypatch):
        _always_pass(monkeypatch)
        goals.set_executor(_passing_executor())
        past = goals._iso_from_ts(__import__("time").time() - 3600)
        goals.create_goal(title="Draft a backlog goal", status="active",
                          milestones=[{"name": f"Overdue {i}", "due": past} for i in range(5)])
        result = goals.run_due_milestones(max_per_tick=2)
        assert len(result["processed"]) == 2

    def test_does_not_re_advance_an_already_blocked_gated_milestone(self, monkeypatch):
        """A due milestone that reads as a hard/gated action creates a
        pending approval on its first tick and must not be re-dispatched
        (and re-approval-carded) on every subsequent tick."""
        _always_pass(monkeypatch)
        goals.set_executor(_passing_executor())
        past = goals._iso_from_ts(__import__("time").time() - 3600)
        goals.create_goal(title="Send the newsletter", status="active",
                          milestones=[{"name": "Send the newsletter to the list", "due": past}])
        first = goals.run_due_milestones()
        assert len(first["processed"]) == 1
        assert first["processed"][0]["status"] == "pending_approval"
        # The milestone is now "blocked", not "pending" — a second tick finds
        # nothing left to advance.
        second = goals.run_due_milestones()
        assert second["changed"] is False


class TestWeeklyReview:
    def test_no_review_before_any_run(self):
        assert goals.latest_review() is None

    def test_review_lists_active_goals_with_progress(self, monkeypatch):
        # Two milestones so the goal stays ACTIVE (not auto-completed) by the
        # time the review runs — see test_goal_verification_repair.py's
        # identical single-milestone-auto-completes note.
        _always_pass(monkeypatch)
        goals.set_executor(_passing_executor())
        g = goals.create_goal(title="Draft the roadmap doc", status="active",
                              milestones=[{"name": "Draft section one"},
                                          {"name": "Draft section two"}])
        mid = g["milestones"][0]["milestone_id"]
        goals.run_milestone(g["goal_id"], mid)

        result = goals.run_weekly_review()
        assert result["changed"] is True

        review = goals.latest_review()
        assert review is not None
        assert "Draft the roadmap doc" in review["content"]
        assert "1/2 milestones done" in review["content"]

    def test_review_rolls_over_overdue_milestones(self, monkeypatch):
        _always_pass(monkeypatch)
        past = goals._iso_from_ts(__import__("time").time() - 3600)
        g = goals.create_goal(title="Draft a late-milestone goal", status="active",
                              milestones=[{"name": "Late one", "due": past}])
        result = goals.run_weekly_review()
        assert g["goal_id"] in result["rolled_over"]
        updated = goals.get_goal(g["goal_id"])
        assert goals._parse_ts(updated["milestones"][0]["due"]) > __import__("time").time()

    def test_no_active_goals_still_writes_a_review_doc(self):
        result = goals.run_weekly_review()
        assert result["changed"] is False
        assert goals.latest_review() is not None


class TestSchedulerBuiltinRegistration:
    def test_all_three_builtins_register(self):
        from agent_friday.services import scheduler as sched
        sched._register_default_builtin_tasks()
        for ref in ("goal_milestones_tick", "goals_weekly_review", "approvals_expiry_sweep"):
            assert ref in sched.BUILTIN_TASKS, f"{ref} did not register as a scheduler builtin"

    def test_goal_milestones_tick_calls_run_due_milestones(self):
        from agent_friday.services import scheduler as sched
        sched._register_default_builtin_tasks()
        assert sched.BUILTIN_TASKS["goal_milestones_tick"]["fn"] is goals.run_due_milestones

    def test_goals_weekly_review_calls_run_weekly_review(self):
        from agent_friday.services import scheduler as sched
        sched._register_default_builtin_tasks()
        assert sched.BUILTIN_TASKS["goals_weekly_review"]["fn"] is goals.run_weekly_review

    def test_approvals_expiry_sweep_calls_expire_stale(self):
        from agent_friday.services import scheduler as sched
        from agent_friday.services import approvals
        sched._register_default_builtin_tasks()
        assert sched.BUILTIN_TASKS["approvals_expiry_sweep"]["fn"] is approvals.expire_stale


class TestInterestModelProvider:
    def test_active_goals_are_surfaced_to_interest_model(self, monkeypatch):
        from agent_friday.services import interest_model as im
        g = goals.create_goal(title="Draft the memoir", status="active",
                              milestones=[{"name": "Chapter 1"}])
        goals.create_goal(title="A proposed-only goal", status="proposed")

        snapshot = im.assemble_interest_model()
        goal_ids = {entry["goal_id"] for entry in snapshot["goals"]}
        assert g["goal_id"] in goal_ids
        # Only ACTIVE goals are surfaced — a merely-proposed goal isn't yet
        # "what the user has signalled they want done."
        assert len(snapshot["goals"]) == 1

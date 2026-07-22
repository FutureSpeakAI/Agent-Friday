"""Unit tests for services/goals.py — the Goal state machine, CRUD, and
milestone rollover (V6_WHOLENESS_SPEC.md §4 Phase 5 / AUTONOMY_SPEC.md §8 A3).

Covers: create defaults, valid/invalid state transitions, same-status no-op,
unknown-goal errors, milestone add/normalize, and rollover of overdue
milestones/deadlines (weekly-review's "incomplete goals roll over with
adjusted deadlines").
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import goals


@pytest.fixture(autouse=True)
def _iso_goals(tmp_path, monkeypatch):
    """Every test gets its own empty goals store — mirrors test_dissent_gate.
    py's _iso_events fixture."""
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    yield


# ═══════════════════════════════════════════════════════════════════════════
#  create_goal defaults + persistence shape
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateGoal:
    def test_defaults_to_proposed(self):
        g = goals.create_goal(title="Ship the Q3 report")
        assert g["status"] == "proposed"

    def test_required_shape_present(self):
        g = goals.create_goal(title="Write a novel", description="One chapter/week",
                              success_criteria="a complete chapter",
                              budget_cap_mψ=5000, approval_required=True,
                              verification_mode="manual",
                              milestones=[{"name": "Chapter 1", "due": "2026-08-01T00:00:00Z"}])
        for key in ("goal_id", "title", "description", "owner", "status", "deadline",
                    "milestones", "success_criteria", "linked_schedules", "linked_chains",
                    "budget_cap_mψ", "approval_required", "verification_mode", "receipts"):
            assert key in g
        assert g["approval_required"] is True
        assert g["verification_mode"] == "manual"
        assert g["milestones"][0]["name"] == "Chapter 1"
        assert g["milestones"][0]["status"] == "pending"
        assert g["milestones"][0]["milestone_id"]

    def test_blank_title_falls_back(self):
        g = goals.create_goal(title="   ")
        assert g["title"] == "Untitled goal"

    def test_get_goal_round_trips(self):
        g = goals.create_goal(title="Round trip me")
        fetched = goals.get_goal(g["goal_id"])
        assert fetched == g

    def test_get_goal_unknown_is_none(self):
        assert goals.get_goal("goal_does_not_exist") is None

    def test_list_goals_filters_by_status(self):
        a = goals.create_goal(title="Active one", status="active")
        b = goals.create_goal(title="Proposed one")
        active = goals.list_goals(status="active")
        proposed = goals.list_goals(status="proposed")
        assert {g["goal_id"] for g in active} == {a["goal_id"]}
        assert {g["goal_id"] for g in proposed} == {b["goal_id"]}


class TestUpdateGoal:
    def test_mutable_field_updates(self):
        g = goals.create_goal(title="Original title")
        updated = goals.update_goal(g["goal_id"], {"title": "New title", "budget_cap_mψ": 999})
        assert updated["title"] == "New title"
        assert updated["budget_cap_mψ"] == 999

    def test_immutable_field_ignored(self):
        g = goals.create_goal(title="X")
        updated = goals.update_goal(g["goal_id"], {"goal_id": "goal_hacked", "status": "completed"})
        assert updated["goal_id"] == g["goal_id"]
        assert updated["status"] == "proposed"  # status is NOT in _MUTABLE_GOAL_FIELDS

    def test_unknown_goal_returns_none(self):
        assert goals.update_goal("goal_nope", {"title": "x"}) is None


class TestAddMilestone:
    def test_add_milestone_normalizes_and_appends(self):
        g = goals.create_goal(title="Multi-milestone goal")
        updated = goals.add_milestone(g["goal_id"], {"name": "First milestone"})
        assert len(updated["milestones"]) == 1
        assert updated["milestones"][0]["name"] == "First milestone"
        assert updated["milestones"][0]["status"] == "pending"
        assert updated["milestones"][0]["attempts"] == 0


# ═══════════════════════════════════════════════════════════════════════════
#  State machine — valid/invalid transitions
# ═══════════════════════════════════════════════════════════════════════════

class TestTransitions:
    def test_full_happy_path(self):
        g = goals.create_goal(title="Happy path goal")
        g = goals.transition_goal(g["goal_id"], "approved")
        assert g["status"] == "approved"
        g = goals.transition_goal(g["goal_id"], "active")
        assert g["status"] == "active"
        g = goals.transition_goal(g["goal_id"], "paused")
        assert g["status"] == "paused"
        g = goals.transition_goal(g["goal_id"], "active")
        assert g["status"] == "active"
        g = goals.transition_goal(g["goal_id"], "completed")
        assert g["status"] == "completed"

    @pytest.mark.parametrize("start,target", [
        ("proposed", "active"),        # must go through 'approved' first
        ("proposed", "completed"),
        ("completed", "active"),       # terminal
        ("cancelled", "active"),       # terminal
        ("active", "proposed"),        # can't go backwards
    ])
    def test_illegal_transition_raises(self, start, target):
        g = goals.create_goal(title="Illegal transition goal", status=start)
        with pytest.raises(ValueError):
            goals.transition_goal(g["goal_id"], target)

    def test_illegal_transition_does_not_mutate_status(self):
        g = goals.create_goal(title="No mutation on failure")
        with pytest.raises(ValueError):
            goals.transition_goal(g["goal_id"], "completed")
        assert goals.get_goal(g["goal_id"])["status"] == "proposed"

    def test_same_status_is_a_noop(self):
        g = goals.create_goal(title="Same status", status="active")
        before_history_len = len(g["history"])
        g2 = goals.transition_goal(g["goal_id"], "active")
        assert g2["status"] == "active"
        assert len(g2["history"]) == before_history_len  # no spurious history entry

    def test_transition_records_history(self):
        g = goals.create_goal(title="History goal")
        g = goals.transition_goal(g["goal_id"], "approved", reason="looks good")
        last = g["history"][-1]
        assert last["from"] == "proposed"
        assert last["to"] == "approved"
        assert last["reason"] == "looks good"

    def test_unknown_goal_raises_keyerror(self):
        with pytest.raises(KeyError):
            goals.transition_goal("goal_missing", "approved")

    def test_unknown_status_raises_valueerror(self):
        g = goals.create_goal(title="Bad status target")
        with pytest.raises(ValueError):
            goals.transition_goal(g["goal_id"], "not_a_real_status")

    def test_failed_can_be_revived(self):
        g = goals.create_goal(title="Revivable", status="active")
        g = goals.transition_goal(g["goal_id"], "failed")
        g = goals.transition_goal(g["goal_id"], "active")
        assert g["status"] == "active"

    def test_failed_can_be_cancelled(self):
        g = goals.create_goal(title="Failed then cancelled", status="active")
        g = goals.transition_goal(g["goal_id"], "failed")
        g = goals.transition_goal(g["goal_id"], "cancelled")
        assert g["status"] == "cancelled"


# ═══════════════════════════════════════════════════════════════════════════
#  Rollover — "incomplete goals roll over with adjusted deadlines"
# ═══════════════════════════════════════════════════════════════════════════

class TestRollover:
    def test_overdue_milestone_due_date_is_pushed_forward(self):
        past = goals._iso_from_ts(time.time() - 3600)  # 1h ago
        g = goals.create_goal(title="Overdue milestone goal", status="active",
                              milestones=[{"name": "Late one", "due": past}])
        mid = g["milestones"][0]["milestone_id"]
        updated = goals.rollover_incomplete_milestones(g["goal_id"], extend_seconds=86400)
        new_due = goals._parse_ts(goals._find_milestone(updated, mid)["due"])
        assert new_due > time.time()
        assert updated["rollovers"] == 1

    def test_done_milestone_is_never_rolled_over(self):
        past = goals._iso_from_ts(time.time() - 3600)
        g = goals.create_goal(title="Done milestone goal", status="active",
                              milestones=[{"name": "Finished", "due": past, "status": "done"}])
        updated = goals.rollover_incomplete_milestones(g["goal_id"], extend_seconds=86400)
        assert updated["milestones"][0]["due"] == past  # untouched
        assert updated["rollovers"] == 0

    def test_future_milestone_is_not_rolled_over(self):
        future = goals._iso_from_ts(time.time() + 3600)
        g = goals.create_goal(title="Future milestone goal", status="active",
                              milestones=[{"name": "Not due yet", "due": future}])
        updated = goals.rollover_incomplete_milestones(g["goal_id"], extend_seconds=86400)
        assert updated["milestones"][0]["due"] == future
        assert updated["rollovers"] == 0

    def test_overdue_goal_deadline_also_rolls_over(self):
        past = goals._iso_from_ts(time.time() - 3600)
        g = goals.create_goal(title="Overdue deadline goal", status="active", deadline=past)
        updated = goals.rollover_incomplete_milestones(g["goal_id"], extend_seconds=86400)
        assert goals._parse_ts(updated["deadline"]) > time.time()

    def test_rollover_records_history(self):
        past = goals._iso_from_ts(time.time() - 3600)
        g = goals.create_goal(title="History on rollover", status="active",
                              milestones=[{"name": "Late", "due": past}])
        updated = goals.rollover_incomplete_milestones(g["goal_id"], extend_seconds=86400)
        assert "rollover" in updated["history"][-1]["reason"]

    def test_rollover_unknown_goal_returns_none(self):
        assert goals.rollover_incomplete_milestones("goal_missing") is None

    def test_multiple_rollovers_accumulate_counter(self):
        far_past = goals._iso_from_ts(time.time() - 100)
        g = goals.create_goal(title="Repeatedly late", status="active",
                              milestones=[{"name": "Chronically late", "due": far_past}])
        goals.rollover_incomplete_milestones(g["goal_id"], extend_seconds=86400)

        # Simulate another week passing with the milestone STILL not done:
        # push its due back into the past directly rather than depending on
        # real wall-clock elapsing between two calls (which the rollover
        # math's "overshoot by up to one extend_seconds increment" makes
        # flaky to time precisely) — then roll over a second time.
        def _fn(goal):
            goal["milestones"][0]["due"] = goals._iso_from_ts(time.time() - 50)
            return goal
        goals._mutate_goal(g["goal_id"], _fn)

        updated = goals.rollover_incomplete_milestones(g["goal_id"], extend_seconds=86400)
        assert updated["rollovers"] == 2

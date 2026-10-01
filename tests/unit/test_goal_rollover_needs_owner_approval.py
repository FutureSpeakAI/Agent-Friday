"""A missed deadline is surfaced, never silently moved (NS-17.5-2).

Material changes to a goal's scope, budget, deadline or authority need the
owner's approval. The weekly review notices overdue milestones and an overdue
goal deadline, writes them into the review, and raises one approval card
proposing the new dates. The dates move only when the owner approves that
card, and only if they are still the dates the card was raised against.
"""
from __future__ import annotations

import time

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


def _late_goal():
    past = goals._iso_from_ts(time.time() - 3600)
    g = goals.create_goal(title="Late goal", status="active", deadline=past,
                          milestones=[{"name": "Late one", "due": past},
                                      {"name": "Later one",
                                       "due": goals._iso_from_ts(time.time() + 86400 * 30)}])
    return g, past


def _rollover_cards():
    return [c for c in goals.approvals.list_approvals()
            if c.get("kind") == goals.ROLLOVER_KIND]


def test_weekly_review_does_not_move_a_missed_deadline():
    g, past = _late_goal()
    result = goals.run_weekly_review()
    after = goals.get_goal(g["goal_id"])
    assert after["milestones"][0]["due"] == past
    assert after["deadline"] == past
    assert int(after.get("rollovers") or 0) == 0
    assert result["rolled_over"] == []
    assert g["goal_id"] in result["rollover_proposed"]


def test_weekly_review_surfaces_the_miss_and_raises_one_card():
    g, past = _late_goal()
    goals.run_weekly_review()
    review = goals.latest_review()["content"]
    assert "overdue" in review.lower()
    assert "approval" in review.lower()
    cards = _rollover_cards()
    assert len(cards) == 1
    card = cards[0]
    assert card["status"] == "pending"
    p = card["payload"]
    assert p["goal_id"] == g["goal_id"]
    assert [c["from"] for c in p["milestones"]] == [past]
    assert p["deadline"]["from"] == past
    # A second review over the same missed dates does not raise a second card.
    goals.run_weekly_review()
    assert len(_rollover_cards()) == 1


def test_approving_the_card_moves_the_dates():
    g, past = _late_goal()
    goals.run_weekly_review()
    card = _rollover_cards()[0]
    goals.approvals.decide(card["approval_id"], "approve")
    after = goals.get_goal(g["goal_id"])
    assert goals._parse_ts(after["milestones"][0]["due"]) > time.time()
    assert goals._parse_ts(after["deadline"]) > time.time()
    assert after["milestones"][1]["due"] == g["milestones"][1]["due"]
    assert after["rollovers"] == 1
    assert "approved" in after["history"][-1]["reason"]


def test_denying_the_card_leaves_the_dates_missed():
    g, past = _late_goal()
    goals.run_weekly_review()
    goals.approvals.decide(_rollover_cards()[0]["approval_id"], "deny")
    after = goals.get_goal(g["goal_id"])
    assert after["milestones"][0]["due"] == past
    assert after["deadline"] == past
    assert "declined" in after["history"][-1]["reason"]


def test_an_approval_never_overwrites_a_date_changed_since():
    g, past = _late_goal()
    goals.run_weekly_review()
    mid = g["milestones"][0]["milestone_id"]
    owner_date = goals._iso_from_ts(time.time() + 86400 * 3)
    goals._update_milestone(g["goal_id"], mid, due=owner_date)
    goals.approvals.decide(_rollover_cards()[0]["approval_id"], "approve")
    after = goals.get_goal(g["goal_id"])
    assert after["milestones"][0]["due"] == owner_date

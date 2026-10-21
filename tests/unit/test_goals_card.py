"""The Goals card (north star §17.13, BD-12 slice G3): every goal with its
state, what each milestone waits on, and the moves the owner can make.
Synthetic fixtures only."""
from __future__ import annotations

import time

import pytest

from agent_friday.services import goals


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    yield
    goals.set_executor(None)


def _goal(**kw):
    args = dict(title="Synthetic goal", status="active", success_criteria="a synthetic draft exists",
                milestones=[{"name": "Draft"}])
    args.update(kw)
    return goals.create_goal(**args)


def _moves(gid):
    s = goals.summary(goals.get_goal(gid))
    return [m["to"] for m in s["moves"]]


def test_the_card_offers_the_owners_moves_for_each_state():
    summ = getattr(goals, "summary", None)
    assert summ is not None, "there is no Goals card summary"
    g = _goal()
    gid = g["goal_id"]
    assert set(_moves(gid)) == {"paused", "met_unverified", "abandoned"}
    goals.transition_goal(gid, "paused")
    assert set(_moves(gid)) == {"active", "abandoned"}
    goals.transition_goal(gid, "active")
    goals.transition_goal(gid, "met_unverified")
    labels = {m["to"]: m["label"] for m in goals.summary(goals.get_goal(gid))["moves"]}
    assert labels == {"active": "Reopen", "archived": "Archive"}
    goals.transition_goal(gid, "archived")
    assert _moves(gid) == ["active"]


def test_the_card_says_what_each_step_waits_on_and_whether_a_restart_needs_a_yes():
    g = _goal(milestones=[{"name": "Draft"}, {"name": "Review"}])
    gid = g["goal_id"]
    m0, m1 = (m["milestone_id"] for m in g["milestones"])
    goals._block(gid, m0, goals.goal_state.blocker(
        "needs_user_input", "Friday stopped while “Draft” was running.", type="interrupted",
        task_id=None, needs_confirmation=True), note="synthetic")
    goals._block(gid, m1, goals.goal_state.blocker(
        "policy_blocked", "The card for this step was not approved.", type="approval_denied",
        needs_confirmation=False), note="synthetic")
    rows = {r["name"]: r for r in goals.summary(goals.get_goal(gid))["milestones"]}
    assert rows["Draft"]["waits"].startswith("Friday stopped")
    assert rows["Draft"]["can_restart"] and rows["Draft"]["restart_needs_confirmation"]
    assert rows["Review"]["can_restart"] and not rows["Review"]["restart_needs_confirmation"]


def test_a_finished_milestone_says_whether_it_was_checked_or_taken_on_your_word(monkeypatch):
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", lambda c, i, **kw: {
        "status": "ok", "passed": True, "score": 0.9, "threshold": 0.7, "critique": "", "suggestions": ""})
    goals.set_executor(lambda g, m, *, critique_hint="": {"ok": True, "text": "draft", "cost_mψ": 1})
    g = _goal(milestones=[{"name": "Draft"}, {"name": "Review"}])
    goals.run_milestone(g["goal_id"], g["milestones"][0]["milestone_id"])
    rows = {r["name"]: r for r in goals.summary(goals.get_goal(g["goal_id"]))["milestones"]}
    assert rows["Draft"]["verified"] is True and rows["Review"]["can_run"] is True


def test_goals_that_need_you_come_first_and_archived_ones_only_on_request():
    a = _goal(title="Quiet goal")
    b = _goal(title="Stuck goal")
    c = _goal(title="Old goal")
    goals._block(b["goal_id"], b["milestones"][0]["milestone_id"],
                 goals.goal_state.blocker("run_failed", "synthetic", needs_confirmation=True), note="x")
    goals.transition_goal(c["goal_id"], "met_unverified")
    goals.transition_goal(c["goal_id"], "archived")
    titles = [s["title"] for s in goals.summaries()["goals"]]
    assert titles == ["Stuck goal", "Quiet goal"]
    assert "Old goal" in [s["title"] for s in goals.summaries(include_finished=True)["goals"]]
    assert a  # quiet goal listed


def test_an_owner_route_choice_holds_work_on_the_wrong_route(monkeypatch):
    from agent_friday.services import cloud_mode_policy as cmp
    monkeypatch.setattr(cmp, "current_mode", lambda: "cloud_only")
    calls = []
    goals.set_executor(lambda g, m, *, critique_hint="": calls.append(1) or {"ok": True, "text": "x", "cost_mψ": 1})
    g = _goal(milestones=[{"name": "Draft", "due": "2020-01-01T00:00:00Z"}])
    goals.update_goal(g["goal_id"], {"allowed_routes": ["local_only"]})
    goals.run_due_milestones()
    assert calls == []
    assert goals.get_goal(g["goal_id"])["milestones"][0]["hold"]["type"] == "route"

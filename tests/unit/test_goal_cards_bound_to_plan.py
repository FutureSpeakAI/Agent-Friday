"""Second review of BD-12 slice G2: a yes is bound to the exact plan it was
given for, the goal's allowed routes hold for every dispatch, and the values
that card subjects and the route check are built from are validated.

Every fixture is synthetic; the executor and the check are fakes.
"""
from __future__ import annotations

import pytest

from agent_friday.services import goals

PAST = "2020-01-01T00:00:00Z"


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
    monkeypatch.setattr(goals.qa_gates, "evaluate_text", lambda *a, **k: {
        "status": "ok", "passed": True, "score": 0.9, "threshold": 0.7, "critique": "", "suggestions": ""})
    yield
    goals.set_executor(None)


class _Work:
    def __init__(self, fail_first=False):
        self.calls = 0
        self.fail_first = fail_first

    def __call__(self, goal, milestone, *, critique_hint=""):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            return {"ok": False, "error": "synthetic failure", "cost_mψ": 0}
        return {"ok": True, "text": f"synthetic draft {self.calls}", "cost_mψ": 10}


def _goal(**kw):
    args = dict(title="Synthetic goal", status="active", success_criteria="a synthetic draft exists",
                milestones=[{"name": "Draft", "due": PAST}])
    args.update(kw)
    return goals.create_goal(**args)


def _m(gid, i=0):
    return goals.get_goal(gid)["milestones"][i]


def test_the_card_is_bound_to_the_exact_instruction(monkeypatch):
    """The gate's subject changes with the instruction it checks, so an
    approved card for the old text is never found for the new one."""
    seen = []
    real = goals.approvals.gate_action

    def spy(**kw):
        seen.append(kw["subject_id"])
        return real(**kw)
    monkeypatch.setattr(goals.approvals, "gate_action", spy)
    goals.set_executor(_Work())
    g = _goal(approval_required=True)
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    first = goals.run_milestone(gid, mid)
    assert first["status"] == "pending_approval"
    goals.update_goal(gid, {"success_criteria": "a synthetic draft that also deletes the old backups"})
    second = goals.run_milestone(gid, mid)
    assert len(seen) == 2 and seen[0] != seen[1], "the new plan was gated under the old card's subject"
    assert second["status"] == "pending_approval" and second["approval_id"] != first["approval_id"]


def test_a_yes_to_the_old_plan_does_not_run_the_new_one(monkeypatch):
    work = _Work()
    goals.set_executor(work)
    g = _goal(approval_required=True)
    gid, mid = g["goal_id"], _m(g["goal_id"])["milestone_id"]
    first = goals.run_milestone(gid, mid)
    goals.update_goal(gid, {"success_criteria": "a synthetic draft that also deletes the old backups"})
    goals.approvals.decide(first["approval_id"], "approve", decided_by="owner")
    assert work.calls == 0, "the new plan ran on a yes given to the old one"


def test_the_goals_routes_hold_for_a_direct_run_too(monkeypatch):
    from agent_friday.services import cloud_mode_policy as cmp
    monkeypatch.setattr(cmp, "current_mode", lambda: "cloud_only")
    work = _Work()
    goals.set_executor(work)
    g = _goal(milestones=[{"name": "Draft", "due": PAST}])
    goals.update_goal(g["goal_id"], {"allowed_routes": ["local_only"]})
    out = goals.run_milestone(g["goal_id"], _m(g["goal_id"])["milestone_id"])
    assert out["ok"] is False and out["status"] == "held"
    assert out["blocker"]["type"] == "route"
    assert work.calls == 0


@pytest.mark.parametrize("bad", ["local_only", 7, ["local_only", "anywhere"], [None]])
def test_allowed_routes_must_be_a_list_of_routing_modes(bad):
    g = _goal()
    with pytest.raises(ValueError):
        goals.update_goal(g["goal_id"], {"allowed_routes": bad})
    assert goals.update_goal(g["goal_id"], {"allowed_routes": ["local_only", "local_only"]})[
        "allowed_routes"] == ["local_only"]


def test_a_supplied_milestone_id_cannot_imitate_a_card_round():
    g = _goal(milestones=[{"name": "A", "milestone_id": "ms_a:r1"}, {"name": "B", "milestone_id": "ms_b"}])
    ids = [m["milestone_id"] for m in g["milestones"]]
    assert ":" not in ids[0] and ids[1] == "ms_b"

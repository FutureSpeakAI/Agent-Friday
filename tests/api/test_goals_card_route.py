"""GET /api/goals/summary and the owner's moves through the goal routes
(north star §17.13, BD-12 slice G3). Synthetic fixtures only."""
from __future__ import annotations

import pytest

from agent_friday.services import goals as goals_mod


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(goals_mod, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals_mod, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals_mod, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals_mod.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")


def _goal(client):
    resp = client.post("/api/goals", json={"title": "Synthetic goal", "status": "active",
                                           "success_criteria": "a synthetic draft exists",
                                           "milestones": [{"name": "Draft"}]})
    assert resp.status_code == 201
    return resp.get_json()["goal"]["goal_id"]


def _summary(client, gid):
    body = client.get("/api/goals/summary").get_json()
    assert body["ok"] is True
    return {g["goal_id"]: g for g in body["goals"]}[gid]


def test_the_summary_lists_each_goal_with_its_moves(client):
    gid = _goal(client)
    s = _summary(client, gid)
    assert s["state_words"] == "active" and s["total"] == 1
    assert {m["to"] for m in s["moves"]} == {"paused", "met_unverified", "abandoned"}


def test_the_owner_can_pause_resume_end_reopen_mark_met_and_archive(client):
    gid = _goal(client)
    for status, expect in (("paused", "paused"), ("active", "active"), ("abandoned", "abandoned"),
                           ("active", "active"), ("met_unverified", "met_unverified"), ("archived", "archived")):
        r = client.post(f"/api/goals/{gid}/transition", json={"status": status, "reason": "synthetic"})
        assert r.status_code == 200, (status, r.get_json())
        assert r.get_json()["goal"]["status"] == expect
    assert client.post(f"/api/goals/{gid}/transition", json={"status": "met_verified"}).status_code in (400, 409)


def test_the_owner_can_replan_change_budget_deadline_authority_and_routing(client):
    gid = _goal(client)
    r = client.patch(f"/api/goals/{gid}", json={"success_criteria": "a different draft",
                                               "budget_cap_mψ": 500, "deadline": "2030-01-01T17:00:00Z",
                                               "approval_required": True, "allowed_routes": ["local_only"]})
    g = r.get_json()["goal"]
    assert g["success_criteria"] == "a different draft" and g["budget_cap_mψ"] == 500
    assert g["approval_required"] is True and g["allowed_routes"] == ["local_only"]
    assert g.get("plan_revised_at")
    s = _summary(client, gid)
    assert s["approval_required"] is True and s["allowed_routes"] == ["local_only"]

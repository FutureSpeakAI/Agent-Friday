"""API tests for routes/goals.py — /api/goals/* CRUD + approve + receipts +
review, and the general /api/approvals/* queue (V6_WHOLENESS_SPEC.md §4
Phase 5 / AUTONOMY_SPEC.md §8 A3). GoalsWS (the UI panel) is deferred to A8 —
this is the API-complete surface.

The milestone executor is swapped for a fast, deterministic fake (goals.
set_executor) so POST .../run never spins a real background agent thread;
qa_gates.evaluate_text is monkeypatched to a clear pass so verification
outcomes are deterministic rather than depending on the globally-stubbed LLM
text being unparseable-into-a-score (which would ALSO degrade to a pass, but
opaquely — see tests/api/conftest.py's _no_real_llm).
"""
from __future__ import annotations

import pytest

from agent_friday.services import goals as goals_mod
from agent_friday.services import approvals as approvals_mod


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(goals_mod, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals_mod, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals_mod, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(approvals_mod, "APPROVALS_FILE", tmp_path / "approvals.json")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")

    goals_mod.set_executor(lambda g, m, *, critique_hint="":
                           {"ok": True, "text": "a solid draft", "cost_mψ": 25})
    monkeypatch.setattr(goals_mod.qa_gates, "evaluate_text",
                        lambda content, intent, **kw: {
                            "status": "ok", "passed": True, "score": 0.9,
                            "threshold": 0.7, "critique": "", "suggestions": ""})
    yield
    goals_mod.set_executor(None)


def _create_goal(client, **overrides):
    body = {"title": "Draft the weekly newsletter", "description": "Draft it every week",
            "success_criteria": "mentions the newsletter"}
    body.update(overrides)
    resp = client.post("/api/goals", json=body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["goal"]


# ═══════════════════════════════════════════════════════════════════════════
#  Goals CRUD
# ═══════════════════════════════════════════════════════════════════════════

class TestCreate:
    def test_missing_title_is_400(self, client):
        resp = client.post("/api/goals", json={"description": "no title"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_create_returns_proposed_goal(self, client):
        goal = _create_goal(client)
        assert goal["status"] == "proposed"
        assert goal["title"] == "Draft the weekly newsletter"

    def test_invalid_verification_mode_is_400(self, client):
        resp = client.post("/api/goals", json={"title": "X", "verification_mode": "bogus"})
        assert resp.status_code == 400


class TestListAndGet:
    def test_list_includes_created_goal(self, client):
        goal = _create_goal(client)
        resp = client.get("/api/goals")
        assert resp.status_code == 200
        ids = {g["goal_id"] for g in resp.get_json()["goals"]}
        assert goal["goal_id"] in ids

    def test_list_filters_by_status(self, client):
        active = _create_goal(client, status="active")
        _create_goal(client, status="proposed")
        resp = client.get("/api/goals?status=active")
        ids = {g["goal_id"] for g in resp.get_json()["goals"]}
        assert ids == {active["goal_id"]}

    def test_list_bad_status_is_400(self, client):
        resp = client.get("/api/goals?status=not_a_status")
        assert resp.status_code == 400

    def test_get_detail(self, client):
        goal = _create_goal(client)
        resp = client.get(f"/api/goals/{goal['goal_id']}")
        assert resp.status_code == 200
        assert resp.get_json()["goal"]["goal_id"] == goal["goal_id"]

    def test_get_unknown_is_404(self, client):
        resp = client.get("/api/goals/goal_does_not_exist")
        assert resp.status_code == 404


class TestUpdate:
    def test_patch_mutable_field(self, client):
        goal = _create_goal(client)
        resp = client.patch(f"/api/goals/{goal['goal_id']}", json={"title": "New title"})
        assert resp.status_code == 200
        assert resp.get_json()["goal"]["title"] == "New title"

    def test_patch_unknown_is_404(self, client):
        resp = client.patch("/api/goals/goal_missing", json={"title": "x"})
        assert resp.status_code == 404


class TestTransition:
    def test_happy_path_transition(self, client):
        goal = _create_goal(client)
        resp = client.post(f"/api/goals/{goal['goal_id']}/transition", json={"status": "approved"})
        assert resp.status_code == 200
        assert resp.get_json()["goal"]["status"] == "approved"

    def test_illegal_transition_is_409(self, client):
        goal = _create_goal(client)  # proposed
        resp = client.post(f"/api/goals/{goal['goal_id']}/transition", json={"status": "completed"})
        assert resp.status_code == 409

    def test_bad_status_value_is_400(self, client):
        goal = _create_goal(client)
        resp = client.post(f"/api/goals/{goal['goal_id']}/transition", json={"status": "sideways"})
        assert resp.status_code == 400

    def test_unknown_goal_is_404(self, client):
        resp = client.post("/api/goals/goal_missing/transition", json={"status": "approved"})
        assert resp.status_code == 404


class TestMilestones:
    def test_add_milestone(self, client):
        goal = _create_goal(client)
        resp = client.post(f"/api/goals/{goal['goal_id']}/milestones", json={"name": "Draft issue 1"})
        assert resp.status_code == 201
        assert resp.get_json()["goal"]["milestones"][0]["name"] == "Draft issue 1"

    def test_add_milestone_missing_name_is_400(self, client):
        goal = _create_goal(client)
        resp = client.post(f"/api/goals/{goal['goal_id']}/milestones", json={})
        assert resp.status_code == 400

    def test_add_milestone_unknown_goal_is_404(self, client):
        resp = client.post("/api/goals/goal_missing/milestones", json={"name": "X"})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
#  Run + receipts
# ═══════════════════════════════════════════════════════════════════════════

class TestRunAndReceipts:
    def _active_goal_with_milestone(self, client, milestone_name="Draft this week's issue"):
        goal = _create_goal(client, status="active")
        resp = client.post(f"/api/goals/{goal['goal_id']}/milestones", json={"name": milestone_name})
        return resp.get_json()["goal"]

    def test_internal_milestone_runs_to_done(self, client):
        goal = self._active_goal_with_milestone(client)
        mid = goal["milestones"][0]["milestone_id"]
        resp = client.post(f"/api/goals/{goal['goal_id']}/milestones/{mid}/run")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["status"] == "done"

    def test_run_unknown_milestone_is_404(self, client):
        goal = _create_goal(client, status="active")
        resp = client.post(f"/api/goals/{goal['goal_id']}/milestones/ms_missing/run")
        assert resp.status_code == 404

    def test_run_on_non_active_goal_is_409(self, client):
        goal = self._active_goal_with_milestone(client)
        mid = goal["milestones"][0]["milestone_id"]
        client.post(f"/api/goals/{goal['goal_id']}/transition", json={"status": "paused"})
        resp = client.post(f"/api/goals/{goal['goal_id']}/milestones/{mid}/run")
        assert resp.status_code == 409

    def test_receipts_after_run(self, client):
        goal = self._active_goal_with_milestone(client)
        mid = goal["milestones"][0]["milestone_id"]
        client.post(f"/api/goals/{goal['goal_id']}/milestones/{mid}/run")
        resp = client.get(f"/api/goals/{goal['goal_id']}/receipts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert data["receipts"][0]["kind"] == "milestone_complete"

    def test_receipts_unknown_goal_is_404(self, client):
        resp = client.get("/api/goals/goal_missing/receipts")
        assert resp.status_code == 404

    def test_gated_milestone_blocks_then_approve_resumes(self, client):
        goal = self._active_goal_with_milestone(client, milestone_name="Send the newsletter to the list")
        mid = goal["milestones"][0]["milestone_id"]

        run_resp = client.post(f"/api/goals/{goal['goal_id']}/milestones/{mid}/run")
        assert run_resp.status_code == 200
        data = run_resp.get_json()
        assert data["status"] == "pending_approval"
        approval_id = data["approval_id"]

        list_resp = client.get("/api/approvals?status=pending")
        ids = {a["approval_id"] for a in list_resp.get_json()["approvals"]}
        assert approval_id in ids

        decide_resp = client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "approve"})
        assert decide_resp.status_code == 200
        assert decide_resp.get_json()["approval"]["status"] == "approved"

        goal_after = client.get(f"/api/goals/{goal['goal_id']}").get_json()["goal"]
        ms = next(m for m in goal_after["milestones"] if m["milestone_id"] == mid)
        assert ms["status"] == "done"

    def test_gated_milestone_deny_blocks_it(self, client):
        goal = self._active_goal_with_milestone(client, milestone_name="Delete the archive permanently")
        mid = goal["milestones"][0]["milestone_id"]
        run_resp = client.post(f"/api/goals/{goal['goal_id']}/milestones/{mid}/run")
        approval_id = run_resp.get_json()["approval_id"]
        client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "deny"})
        goal_after = client.get(f"/api/goals/{goal['goal_id']}").get_json()["goal"]
        ms = next(m for m in goal_after["milestones"] if m["milestone_id"] == mid)
        assert ms["status"] == "blocked"


# ═══════════════════════════════════════════════════════════════════════════
#  Weekly review
# ═══════════════════════════════════════════════════════════════════════════

class TestReview:
    def test_review_is_null_before_any_run(self, client):
        resp = client.get("/api/goals/review")
        assert resp.status_code == 200
        assert resp.get_json()["review"] is None

    def test_review_run_then_get(self, client):
        _create_goal(client, status="active")
        run_resp = client.post("/api/goals/review/run")
        assert run_resp.status_code == 200
        assert run_resp.get_json()["ok"] is True

        get_resp = client.get("/api/goals/review")
        review = get_resp.get_json()["review"]
        assert review is not None
        assert "Weekly Goal Review" in review["content"]


# ═══════════════════════════════════════════════════════════════════════════
#  General approvals queue
# ═══════════════════════════════════════════════════════════════════════════

class TestApprovalsRoutes:
    def test_get_unknown_approval_is_404(self, client):
        resp = client.get("/api/approvals/appr_missing")
        assert resp.status_code == 404

    def test_decide_bad_value_is_400(self, client):
        goal = _create_goal(client, status="active")
        client.post(f"/api/goals/{goal['goal_id']}/milestones", json={"name": "Send an urgent email"})
        goal = client.get(f"/api/goals/{goal['goal_id']}").get_json()["goal"]
        mid = goal["milestones"][0]["milestone_id"]
        run_resp = client.post(f"/api/goals/{goal['goal_id']}/milestones/{mid}/run")
        approval_id = run_resp.get_json()["approval_id"]

        resp = client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "maybe"})
        assert resp.status_code == 400

    def test_decide_unknown_approval_is_404(self, client):
        resp = client.post("/api/approvals/appr_missing/decide", json={"decision": "approve"})
        assert resp.status_code == 404

    def test_list_bad_status_is_400(self, client):
        resp = client.get("/api/approvals?status=not_a_status")
        assert resp.status_code == 400

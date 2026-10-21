"""POST /api/goals/<gid>/milestones/<mid>/restart: a milestone a restart
stopped starts again only with the owner's explicit yes, given by a click on
Friday's own page (BD-12 slice G1). Synthetic fixtures only."""
from __future__ import annotations

import time

import pytest

from agent_friday.services import goals as goals_mod
from agent_friday.services import approvals as approvals_mod


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(goals_mod, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals_mod, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals_mod, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(approvals_mod, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(goals_mod.qa_gates, "evaluate_text",
                        lambda content, intent, **kw: {
                            "status": "ok", "passed": True, "score": 0.9,
                            "threshold": 0.7, "critique": "", "suggestions": ""})
    monkeypatch.setattr(goals_mod, "_task_status", lambda tid: ("interrupted", ""), raising=False)
    from agent_friday.services import task_resume
    monkeypatch.setattr(task_resume, "resumability", lambda tid: {
        "resumable": True, "needs_confirmation": True, "reason": "send_email was in flight.",
        "iteration": 2, "pending_tool": "send_email", "attempts": 0})
    yield
    goals_mod.set_executor(None)


def _stopped_milestone(calls):
    goals_mod.set_executor(lambda g, m, *, critique_hint="":
                           calls.append(1) or {"ok": True, "text": "draft", "cost_mψ": 1})
    goal = goals_mod.create_goal(title="Synthetic goal", status="active",
                                 milestones=[{"name": "Synthetic step"}])
    gid, mid = goal["goal_id"], goal["milestones"][0]["milestone_id"]
    goals_mod._update_milestone(gid, mid, status="in_progress",
                                run={"task_id": "task_x", "boot": "previous", "started_at": time.time()})
    fn = getattr(goals_mod, "reconcile_on_boot", None)
    if fn:
        fn(background=False)
    return gid, mid


def _screen_headers():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token()}


def test_restart_without_confirmation_is_refused_and_runs_nothing(client):
    calls = []
    gid, mid = _stopped_milestone(calls)
    resp = client.post(f"/api/goals/{gid}/milestones/{mid}/restart", json={})
    assert resp.status_code == 409
    assert resp.get_json()["status"] == "needs_confirmation"
    assert calls == []


def test_a_confirmation_that_is_not_a_screen_click_is_refused(client):
    calls = []
    gid, mid = _stopped_milestone(calls)
    resp = client.post(f"/api/goals/{gid}/milestones/{mid}/restart",
                       json={"confirm_unknown_outcome": True})
    assert resp.status_code == 403
    assert calls == []


def test_a_screen_click_confirmation_starts_it_once(client):
    calls = []
    gid, mid = _stopped_milestone(calls)
    resp = client.post(f"/api/goals/{gid}/milestones/{mid}/restart",
                       json={"confirm_unknown_outcome": True}, headers=_screen_headers())
    assert resp.status_code == 200, resp.get_json()
    assert calls == [1]


def test_the_list_reports_unreadable_goals(client):
    goal = goals_mod.create_goal(title="Synthetic goal")
    goals_mod._goal_path(goal["goal_id"]).write_text("{", encoding="utf-8")
    body = client.get("/api/goals").get_json()
    assert [u["goal_id"] for u in body.get("unreadable", [])] == [goal["goal_id"]]


def test_a_request_cannot_post_a_milestone_as_already_running_or_done(client):
    goal = client.post("/api/goals", json={"title": "Synthetic goal", "status": "active",
                                           "milestones": [{"name": "Posted as done", "status": "done",
                                                           "done_at": "2026-01-01T00:00:00Z"}]}
                       ).get_json()["goal"]
    assert goal["milestones"][0]["status"] == "pending"
    added = client.post(f"/api/goals/{goal['goal_id']}/milestones",
                        json={"name": "Posted as running", "status": "in_progress",
                              "run": {"task_id": "task_x"}}).get_json()["goal"]
    m = added["milestones"][-1]
    assert m["status"] == "pending" and not m.get("run")

"""GET/DELETE /api/goals/drafts and confirming a draft (BD-12 slice G6).
Synthetic fixtures only."""
from __future__ import annotations

import pytest

from agent_friday.services import goals as goals_mod


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    from agent_friday.services import off_record
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(goals_mod, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals_mod, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals_mod, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    monkeypatch.setattr(goals_mod.approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)


def _screen():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


SAID = {"title": "Synthetic goal", "completion_condition": "a synthetic report exists",
        "evidence_required": ["report.md"], "stop_conditions": ["it needs money"]}


def test_a_draft_is_listed_confirmed_by_a_click_and_then_gone(client):
    from agent_friday.services import goal_tools
    goal_tools.handle_draft_goal(SAID)
    drafts = client.get("/api/goals/drafts").get_json()["drafts"]
    assert len(drafts) == 1 and drafts[0]["questions"] == []
    d = drafts[0]
    body = {"goal": d["spec"], "summary_id": d["summary_id"], "draft_id": d["draft_id"]}
    assert client.post("/api/goals/define", json=body).status_code == 403
    assert client.get("/api/goals/drafts").get_json()["drafts"], "a refused confirm used up the draft"
    assert client.post("/api/goals/define", json=body, headers=_screen()).status_code == 201
    assert client.get("/api/goals/drafts").get_json()["drafts"] == []


def test_a_draft_can_be_discarded(client):
    from agent_friday.services import goal_tools
    goal_tools.handle_draft_goal(SAID)
    did = client.get("/api/goals/drafts").get_json()["drafts"][0]["draft_id"]
    assert client.delete(f"/api/goals/drafts/{did}").status_code == 200
    assert client.delete(f"/api/goals/drafts/{did}").status_code == 404
    assert goals_mod.list_goals() == []

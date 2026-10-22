"""POST /api/goals/draft and /api/goals/define (north star §17.4, BD-12 slice
G4): a draft creates nothing; a goal is created only from the confirmed
summary. Synthetic fixtures only."""
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


SPEC = {"title": "Synthetic goal", "completion_condition": "a synthetic report exists",
        "evidence_required": ["report.md"], "stop_conditions": ["it needs money"],
        "milestones": [{"name": "Draft the report"}]}


def test_a_draft_creates_nothing_and_says_what_it_needs(client):
    resp = client.post("/api/goals/draft", json={"title": "Synthetic goal"})
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] is True
    assert [q["key"] for q in body["questions"]] == ["completion_condition", "evidence_required", "stop_conditions"]
    assert "Goal: Synthetic goal." in body["summary"]
    assert goals_mod.list_goals() == []


def _screen():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def test_define_creates_only_the_goal_whose_summary_was_confirmed(client):
    sid = client.post("/api/goals/draft", json=SPEC).get_json()["summary_id"]
    changed = dict(SPEC, completion_condition="something else")
    assert client.post("/api/goals/define", json={"goal": changed, "summary_id": sid}, headers=_screen()).status_code == 409
    assert client.post("/api/goals/define", json={"goal": {"title": "x"}, "summary_id": sid}, headers=_screen()).status_code == 409
    assert goals_mod.list_goals() == []
    ok = client.post("/api/goals/define", json={"goal": SPEC, "summary_id": sid}, headers=_screen())
    assert ok.status_code == 201
    g = ok.get_json()["goal"]
    assert g["status"] == "approved" and g["schema_version"] == 2
    assert client.post("/api/goals/draft", json=["not", "an", "object"]).status_code == 400


def test_only_a_click_in_fridays_window_confirms_a_goal_or_lets_it_do_more(client):
    sid = client.post("/api/goals/draft", json=SPEC).get_json()["summary_id"]
    refused = client.post("/api/goals/define", json={"goal": SPEC, "summary_id": sid})
    assert refused.status_code == 403 and goals_mod.list_goals() == []
    gid = client.post("/api/goals/define", json={"goal": SPEC, "summary_id": sid}, headers=_screen()).get_json()["goal"]["goal_id"]
    assert client.patch(f"/api/goals/{gid}", json={"autonomy": {"send": "on_its_own"}}).status_code == 403
    assert goals_mod.get_goal(gid)["autonomy"]["send"] == "ask_me"
    assert client.patch(f"/api/goals/{gid}", json={"autonomy": {"send": "never"}}).status_code == 200
    assert client.patch(f"/api/goals/{gid}", json={"autonomy": {"send": "on_its_own"}}, headers=_screen()).status_code == 200

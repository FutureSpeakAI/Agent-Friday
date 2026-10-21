"""Defining a goal with Friday and the full goal schema (north star §17.1,
§17.2, §17.4, §17.5; BD-12 slice G4). Synthetic fixtures; the executor and
the check are fakes, so nothing reaches a model."""
from __future__ import annotations

import pytest

from agent_friday.services import goal_definition as gd
from agent_friday.services import goal_state
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
    def __init__(self):
        self.calls = 0

    def __call__(self, goal, milestone, *, critique_hint=""):
        self.calls += 1
        return {"ok": True, "text": f"synthetic draft {self.calls}", "cost_mψ": 10}


FULL = {
    "title": "Plan a synthetic trip", "intent": "A weekend away, nothing fancy.",
    "completion_condition": "an itinerary file is saved",
    "evidence_required": [{"kind": "file", "what": "itinerary.md in Documents"}],
    "stop_conditions": ["flights cost more than the budget"],
    "milestones": [{"name": "Find options", "planned_actions": ["read", "draft"]},
                   {"name": "Write the itinerary", "planned_actions": ["write"], "depends_on": ["ms_1"],
                    "risk": "medium", "decision_points": ["which hotel"], "outcome": "itinerary.md"}],
    "budget": {"mψ": 500, "runtime_minutes": 30},
}


def test_only_the_questions_the_goal_still_needs_are_asked():
    assert gd.draft(FULL)["questions"] == []
    asked = [q["key"] for q in gd.draft({"title": "Synthetic"})["questions"]]
    assert asked == ["completion_condition", "evidence_required", "stop_conditions"]
    outward = dict(FULL, milestones=[{"name": "Email the plan", "planned_actions": ["send"]}])
    assert [q["key"] for q in gd.draft(outward)["questions"]] == ["people"]
    assert gd.draft(dict(outward, people=["a synthetic friend"]))["questions"] == []
    owner_judges = {"title": "T", "completion_condition": "done", "evaluator": "owner_confirms", "stop_conditions": ["x"]}
    assert gd.draft(owner_judges)["questions"] == [], "no proof is asked for when the owner judges it"


def test_the_summary_says_the_whole_goal_in_plain_words():
    s = gd.draft(FULL)["summary"]
    for part in ("Goal: Plan a synthetic trip.", "Done when: an itinerary file is saved.", "Proof: itinerary.md in Documents.",
                 "Steps: 1. Find options; 2. Write the itinerary.", "On my own I may look things up and read, draft.",
                 "I ask you first before I change files or settings", "I never delete things",
                 "500 mψ", "30 minutes of work", "I stop and come back to you if: flights cost more than the budget."):
        assert part in s, part


def test_spending_always_asks():
    assert gd.autonomy({"spend": "on_its_own"})["spend"] == "ask_me"


def test_a_goal_is_created_only_from_the_summary_the_owner_confirmed():
    with pytest.raises(gd.DefinitionRefused):
        goals.define_goal({"title": "Only a title"}, gd.draft({"title": "Only a title"})["summary_id"])
    sid = gd.draft(FULL)["summary_id"]
    with pytest.raises(gd.DefinitionRefused):
        goals.define_goal(dict(FULL, completion_condition="something else"), sid)
    g = goals.define_goal(FULL, sid)
    assert g["status"] == "approved" and g["schema_version"] == 2
    for key in ("principal", "intent", "completion_condition", "evaluator", "evidence_required", "constraints",
                "budget", "autonomy", "notification_policy", "permitted_tools", "people", "triggers",
                "stop_conditions"):
        assert key in g, key
    m2 = g["milestones"][1]
    assert m2["depends_on"] == ["ms_1"] and m2["risk"] == "medium" and m2["decision_points"] == ["which hotel"]
    assert m2["planned_actions"] == ["write"] and m2["outcome"] == "itinerary.md"
    r = g["receipts"][0]
    assert r["kind"] == "goal_defined" and "Done when: an itinerary file is saved." in r["note"]


def test_a_step_of_a_kind_the_owner_said_never_to_does_not_run():
    work = _Work()
    goals.set_executor(work)
    spec = dict(FULL, milestones=[{"name": "Clear old drafts", "planned_actions": ["delete"], "due": PAST}])
    g = goals.define_goal(spec, gd.draft(spec)["summary_id"])
    goals.transition_goal(g["goal_id"], "active")
    mid = g["milestones"][0]["milestone_id"]
    out = goals.run_milestone(g["goal_id"], mid)
    assert out["status"] == "not_allowed" and work.calls == 0
    m = goals.get_goal(g["goal_id"])["milestones"][0]
    assert m["status"] == "blocked" and m["blocker"]["type"] == "not_allowed"
    goals.update_goal(g["goal_id"], {"autonomy": {"delete": "ask_me"}})
    assert goals.get_goal(g["goal_id"])["milestones"][0]["status"] == "pending"


def test_a_step_of_a_kind_that_asks_waits_for_a_card():
    work = _Work()
    goals.set_executor(work)
    spec = dict(FULL, milestones=[{"name": "Save the itinerary", "planned_actions": ["write"], "due": PAST}])
    g = goals.define_goal(spec, gd.draft(spec)["summary_id"])
    goals.transition_goal(g["goal_id"], "active")
    out = goals.run_milestone(g["goal_id"], g["milestones"][0]["milestone_id"])
    assert out["status"] == "pending_approval" and work.calls == 0


def test_autonomy_values_are_validated():
    g = goals.define_goal(FULL, gd.draft(FULL)["summary_id"])
    with pytest.raises(ValueError):
        goals.update_goal(g["goal_id"], {"autonomy": {"send": "whenever"}})
    with pytest.raises(ValueError):
        goals.update_goal(g["goal_id"], {"autonomy": {"teleport": "on_its_own"}})


def test_a_goal_that_used_its_minutes_does_not_start_more_work():
    g = goals.define_goal(FULL, gd.draft(FULL)["summary_id"])
    goal = dict(goals.get_goal(g["goal_id"]), status="active", spent_runtime_s=30 * 60)
    hold = goal_state.continuation_hold(goal, goal["milestones"][0])
    assert hold and hold["category"] == "budget_exhausted" and hold.get("type") == "runtime"


def test_home_shows_a_goal_only_for_the_moments_the_owner_asked_for():
    from agent_friday.services import desktop_card_sources as dcs
    quiet = dict(FULL, notification_policy={"on": ["needs_owner"]})
    g = goals.define_goal(quiet, gd.draft(quiet)["summary_id"])
    goals.transition_goal(g["goal_id"], "active")
    records, _stamp, _detail = dcs._goals(0)
    rec = {r["id"]: r for r in records}[g["goal_id"]]
    assert rec["notify"] is False, "an active goal went to Home though the owner asked only for 'needs you'"
    loud = goals.define_goal(FULL, gd.draft(FULL)["summary_id"])
    goals.transition_goal(loud["goal_id"], "active")
    records, _stamp, _detail = dcs._goals(0)
    assert {r["id"]: r for r in records}[loud["goal_id"]]["notify"] is True

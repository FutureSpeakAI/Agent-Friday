"""The plan tools: plan_first, plan_approve, plan_milestone. All INTERNAL: a
plan is an artifact in Friday's own store, and approving one is the user's
decision that the model only reports."""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent as ag
from agent_friday.services import artifacts as art
from agent_friday.services import plans


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    yield


def _schema(name):
    hits = [t for t in (ag.CLAUDE_TOOLS + ag.WORKSPACE_TOOLS.get("hub", [])) if t.get("name") == name]
    assert len(hits) == 1, name
    return hits[0]


def test_the_tools_are_declared_and_internal():
    s = _schema("plan_first")
    assert set(s["input_schema"]["required"]) == {"title", "plan", "milestones"}
    assert set(_schema("plan_milestone")["input_schema"]["required"]) == {"n", "status"}
    assert set(_schema("plan_milestone")["input_schema"]["properties"]["blocker"]["enum"]) == set(plans.BLOCKERS)
    _schema("plan_approve")
    for name in ("plan_first", "plan_approve", "plan_milestone"):
        assert name in ag.CLAUDE_TOOL_HANDLERS, name
        assert action_gate.classify(name, {})[0] == action_gate.INTERNAL, name


def test_plan_first_then_approve_then_milestones_for_the_asking_conversation():
    tok = ag._CURRENT_CONVERSATION.set("conv-p")
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["plan_first"]({"title": "Tracker", "plan": "# Plan\n", "milestones": ["One", "Two"]})
        assert out["status"] == "awaiting_approval" and out["artifact_id"]
        assert "do not build" in out["note"].lower()
        # Before the user says so, a milestone cannot move.
        blocked = ag.CLAUDE_TOOL_HANDLERS["plan_milestone"]({"n": 1, "status": "doing"})
        assert isinstance(blocked, str) and "approv" in blocked.lower()
        ok = ag.CLAUDE_TOOL_HANDLERS["plan_approve"]({"user_words": "yes, go ahead and build it"})
        assert ok["status"] == "approved"
        m = ag.CLAUDE_TOOL_HANDLERS["plan_milestone"]({"n": 1, "status": "done", "step": "abc1234"})
        assert m["status"] == "ok" and m["next"] == 2
        m = ag.CLAUDE_TOOL_HANDLERS["plan_milestone"]({"n": 2, "status": "blocked", "blocker": "external_wait", "note": "waiting on the API key"})
        assert m["status"] == "ok" and m["blocked"] == "external_wait"
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)


def test_plan_approve_needs_the_users_words():
    tok = ag._CURRENT_CONVERSATION.set("conv-q")
    try:
        ag.CLAUDE_TOOL_HANDLERS["plan_first"]({"title": "T", "plan": "# p", "milestones": ["One"]})
        out = ag.CLAUDE_TOOL_HANDLERS["plan_approve"]({})
        assert isinstance(out, str) and "user" in out.lower()
        assert plans.current("conv-q")["meta"]["plan"]["approved"] is False
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)

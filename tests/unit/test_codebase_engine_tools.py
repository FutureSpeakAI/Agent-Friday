"""The engine choice and one agent run, as tools in chat and by voice (salon spec §4.7)."""
from __future__ import annotations

import inspect

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent as ag
from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import voice_engine


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    # The approvals store and the grants are this test's own: a card another
    # test left pending must not be counted here, nor this test's left behind.
    from agent_friday.services import approvals as _ap
    monkeypatch.setattr(_ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(_ap, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(_ap, "_notify_pending", lambda rec: None)
    from agent_friday.governance import action_gate as _gate
    monkeypatch.setattr(_gate, "_grants_file", lambda: tmp_path / "grants.json")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_resident_brain", lambda: None)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 0.0)
    yield


def test_the_two_tools_are_declared_and_classified_by_what_they_do():
    for name in ("codebase_engine", "codebase_agent"):
        assert any(t["name"] == name for t in (ag.CLAUDE_TOOLS + ag.WORKSPACE_TOOLS.get("hub", [])))
        assert ag.TOOL_RINGS.get(name) == 1 and name in ag.CLAUDE_TOOL_HANDLERS
        assert name in [v[0] for v in voice_engine._VOICE_LIVE_TOOLS]
    # Choosing the engine records the user's choice and runs nothing: internal.
    assert "codebase_engine" in action_gate.INTERNAL_TOOLS
    # Running Claude's agent is a process on this PC that reaches the provider:
    # outward, and self-gated behind one card per task (services/codebase_tasks).
    assert "codebase_agent" not in action_gate.INTERNAL_TOOLS
    assert "codebase_agent" in action_gate.OUTWARD_TOOLS and "codebase_agent" in action_gate.SELF_GATED
    assert '"codebase_engine", "codebase_agent"' in inspect.getsource(voice_engine._voice_tool_run)


def test_choosing_the_engine_is_announced_with_the_disclosure_and_gates_the_run(monkeypatch):
    conv = convs.create("Rent tracker")
    rec = cb.create("Rent tracker", conversation_id=conv["id"])
    tok = ag._CURRENT_CONVERSATION.set(conv["id"])
    try:
        refused = ag.CLAUDE_TOOL_HANDLERS["codebase_agent"]({"task": "add a search box"})
        assert refused["status"] == "refused" and "engine" in refused["say"].lower()
        out = ag.CLAUDE_TOOL_HANDLERS["codebase_engine"]({"engine": "claude_agent"})
        assert out["status"] == "ok" and out["engine"] == "claude_agent" and "this PC" in out["say"]
        assert cb.load(rec["id"])["seats"]["engine"] == "claude_agent"
        line = [m["text"] for m in convs.messages(conv["id"]) if m.get("kind") == "seat_change"][-1]
        assert "Engine change" in line and "Claude's agent" in line
        # The run waits on one card for the task; approving it runs the task
        # with the engine module and the codebase's key profile.
        from agent_friday.services import approvals as _ap
        from agent_friday.services import claude_engine as ce
        seen = {}
        monkeypatch.setattr(ce, "run_task", lambda cid, task, **kw: seen.update(cid=cid, task=task, **kw) or {"status": "ok", "say": "done"})
        waiting = ag.CLAUDE_TOOL_HANDLERS["codebase_agent"]({"task": "add a search box"})
        assert waiting["status"] == "waiting" and waiting.get("approval_id") and seen == {}, "nothing runs before the card"
        _ap.decide(waiting["approval_id"], "approve")
        assert seen["cid"] == rec["id"] and seen["task"] == "add a search box" and seen["key_profile"] == "mine"
        back = ag.CLAUDE_TOOL_HANDLERS["codebase_engine"]({"engine": "friday"})
        assert back["engine"] == "friday"
        assert ag.CLAUDE_TOOL_HANDLERS["codebase_engine"]({"engine": "gpt"})["status"] == "refused"
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)

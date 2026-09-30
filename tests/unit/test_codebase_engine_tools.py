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
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_resident_brain", lambda: None)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 0.0)
    yield


def test_the_two_tools_are_declared_inside_and_by_voice():
    for name in ("codebase_engine", "codebase_agent"):
        assert any(t["name"] == name for t in ag.CLAUDE_TOOLS)
        assert name in action_gate.INTERNAL_TOOLS and ag.TOOL_RINGS.get(name) == 1 and name in ag.CLAUDE_TOOL_HANDLERS
        assert name in [v[0] for v in voice_engine._VOICE_LIVE_TOOLS]
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
        # The run itself goes to the engine module with the codebase's key profile.
        from agent_friday.services import claude_engine as ce
        seen = {}
        monkeypatch.setattr(ce, "run_task", lambda cid, task, **kw: seen.update(cid=cid, task=task, **kw) or {"status": "ok", "say": "done"})
        ag.CLAUDE_TOOL_HANDLERS["codebase_agent"]({"task": "add a search box"})
        assert seen["cid"] == rec["id"] and seen["task"] == "add a search box" and seen["key_profile"] == "mine"
        back = ag.CLAUDE_TOOL_HANDLERS["codebase_engine"]({"engine": "friday"})
        assert back["engine"] == "friday"
        assert ag.CLAUDE_TOOL_HANDLERS["codebase_engine"]({"engine": "gpt"})["status"] == "refused"
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)

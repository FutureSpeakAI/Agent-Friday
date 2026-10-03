"""'Use Opus for this one.' / 'Use Alex's key.' / 'How much has this cost?'
(salon spec §4.7, §6.3 voice rows): three tools in chat and by voice, and
the model each step names is the one that made it.
"""
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
    monkeypatch.setattr(cb, "_resident_brain", lambda: ("bonsai2:27b", "Bonsai2"))
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 0.0)
    monkeypatch.setattr(cm, "codebase_costs", lambda cid, rng="all": {"total_usd": 0.42, "by_key_profile": {"mine": 0.42}, "calls": 3})
    yield


def _tool(name):
    return next(t for t in (ag.CLAUDE_TOOLS + ag.WORKSPACE_TOOLS.get("hub", [])) if t["name"] == name)


def _scoped(monkeypatch):
    conv = convs.create("Rent tracker")
    rec = cb.create("Rent tracker", conversation_id=conv["id"])
    monkeypatch.setattr(ag, "_CURRENT_CONVERSATION", ag._CURRENT_CONVERSATION)
    tok = ag._CURRENT_CONVERSATION.set(conv["id"])
    return rec, conv, tok


def test_the_three_tools_are_declared_inside_and_by_voice():
    for name, ring in (("codebase_seat", 1), ("codebase_key", 1), ("codebase_costs", 0)):
        t = _tool(name)
        assert name in action_gate.INTERNAL_TOOLS and ag.TOOL_RINGS.get(name) == ring and name in ag.CLAUDE_TOOL_HANDLERS
        assert name in [v[0] for v in voice_engine._VOICE_LIVE_TOOLS], name
    assert "which" in _tool("codebase_seat")["input_schema"]["properties"] and "model" in _tool("codebase_seat")["input_schema"]["properties"]
    src = inspect.getsource(voice_engine._voice_tool_run)
    # The voice dispatch names the three in the branch that sets the call's
    # conversation for the codebase tools (it carries the others beside them).
    import re as _re
    branch = next(m.group(1) for m in _re.finditer(r"if name in \(([^)]*)\):", src) if '"codebase_seat"' in m.group(1))
    for name in ("codebase_seat", "codebase_key", "codebase_costs"):
        assert '"%s"' % name in branch, name


def test_use_opus_for_this_one_changes_the_seat_and_speaks_it(monkeypatch):
    rec, conv, tok = _scoped(monkeypatch)
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["codebase_seat"]({"which": "heavy", "model": "Opus 5.5"})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    assert out["status"] == "ok" and cb.load(rec["id"])["seats"]["heavy_seat"] == "claude-opus-5-5"
    assert out["say"] == "Switching big edits to Opus 5.5 on your key."
    assert "Opus 5.5" in out["header"]


def test_an_unknown_model_or_key_is_refused_plainly(monkeypatch):
    rec, conv, tok = _scoped(monkeypatch)
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["codebase_seat"]({"which": "heavy", "model": "the big one"})
        out2 = ag.CLAUDE_TOOL_HANDLERS["codebase_key"]({"profile": "alex"})
        ok = ag.CLAUDE_TOOL_HANDLERS["codebase_key"]({"profile": "mine"})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    assert out["status"] == "refused" and "the big one" in out["say"]
    assert out2["status"] == "refused" and "Alex" in out2["say"] or "alex" in out2["say"]
    assert ok["status"] == "ok" and "your key" in ok["say"].lower()


def test_how_much_has_this_cost_answers_from_the_meter(monkeypatch):
    rec, conv, tok = _scoped(monkeypatch)
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["codebase_costs"]({})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    assert out["status"] == "ok" and out["total_usd"] == 0.42 and "0.42" in out["say"] and "your key" in out["say"].lower()


def test_a_step_names_the_model_that_made_it(monkeypatch):
    rec, conv, tok = _scoped(monkeypatch)
    mtok = ag._CURRENT_MODEL.set("claude-opus-5-5")
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["codebase_edit"]({"files": {"index.html": "<h1>hi</h1>"}, "summary": "Said hi"})
    finally:
        ag._CURRENT_MODEL.reset(mtok); ag._CURRENT_CONVERSATION.reset(tok)
    assert out["status"] == "ok"
    st = cb.steps(rec["id"], limit=1)[0]
    assert st["receipt"]["model"] == "claude-opus-5-5" and st["receipt"]["key_profile"] == "mine"
    assert st["who"].startswith("claude-opus-5-5 via mine")


def test_both_agent_loops_set_the_current_model_before_running_a_tool():
    for fn in (ag._call_claude_agent, ag._oai_agentic_loop):
        src = inspect.getsource(fn)
        i = src.index("_CURRENT_MODEL.set(")
        assert i < src.index("_execute_tool(", i), fn.__name__

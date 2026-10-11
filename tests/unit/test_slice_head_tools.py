"""P7-4: the two chat tools that sit on the slice-need head.

`request_context` asks the user's own computer for a slice the prompt held
back; `why_that_plan` reads a turn's receipt back. Neither is declared to the
voice front (its contract is full): voice reaches them through ask_friday.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent
from agent_friday.services import slice_head as sh


def _schema(name):
    return next(t for t in agent.CLAUDE_TOOLS if t["name"] == name)


@pytest.fixture
def ctx():
    """Run a handler as a cloud tool call in conversation c1."""
    toks = [agent._CURRENT_CONVERSATION.set("c1"), agent._CURRENT_PROVIDER.set("anthropic")]
    yield
    agent._CURRENT_PROVIDER.reset(toks.pop())
    agent._CURRENT_CONVERSATION.reset(toks.pop())


def test_both_tools_are_registered_with_rings_and_a_matching_schema():
    assert agent.CLAUDE_TOOL_HANDLERS["request_context"] is agent._tool_request_context
    assert agent.CLAUDE_TOOL_HANDLERS["why_that_plan"] is agent._tool_why_that_plan
    assert agent.TOOL_RINGS["request_context"] == 2     # its payload goes out on a card
    assert agent.TOOL_RINGS["why_that_plan"] == 0       # reads the receipt only
    enum = _schema("request_context")["input_schema"]["properties"]["category"]["enum"]
    assert tuple(enum) == sh.REQUESTABLE
    assert _schema("request_context")["input_schema"]["required"] == ["category"]


def test_neither_tool_is_declared_to_the_voice_front():
    from agent_friday.services import voice_engine
    ids = {t[0] for t in voice_engine._VOICE_LIVE_TOOLS}
    assert not ({"request_context", "why_that_plan"} & ids)


def test_both_tools_take_the_ask_first_checkpoints_internal_class():
    from agent_friday.governance import action_gate
    assert {"request_context", "why_that_plan"} <= set(action_gate.INTERNAL_TOOLS)


def test_a_local_conversation_has_nothing_held_back():
    toks = [agent._CURRENT_CONVERSATION.set("c1"), agent._CURRENT_PROVIDER.set("ollama")]
    try:
        out = agent._tool_request_context({"category": "notes"})
    finally:
        agent._CURRENT_PROVIDER.reset(toks.pop())
        agent._CURRENT_CONVERSATION.reset(toks.pop())
    assert "own computer" in out


def test_no_conversation_means_nothing_to_ask_for():
    tok = agent._CURRENT_PROVIDER.set("anthropic")
    try:
        assert "not part of a chat" in agent._tool_request_context({"category": "notes"})
    finally:
        agent._CURRENT_PROVIDER.reset(tok)


def test_the_handler_passes_the_turns_taint_and_reports_each_outcome(ctx, monkeypatch):
    seen = []
    results = iter([{"status": "pending"},
                    {"status": "unavailable", "reason": "Nothing was held back."},
                    {"status": "sent", "text": "scrubbed answer"}])
    monkeypatch.setattr(sh, "request_context",
                        lambda *a, **kw: seen.append(kw) or next(results))
    monkeypatch.setattr(sh, "is_tainted", lambda key: True)
    assert "asked on screen" in agent._tool_request_context({"category": "notes", "why": "x"})
    assert seen[-1]["tainted"] is True and seen[-1]["conversation_id"] == "c1"
    monkeypatch.setattr(sh, "is_tainted", lambda key: False)
    assert agent._tool_request_context({"category": "notes"}) == "Nothing was held back."
    assert seen[-1]["tainted"] is False
    monkeypatch.setattr(sh, "take_released_text", lambda conv: "")
    assert agent._tool_request_context({"category": "notes"}).endswith("scrubbed answer")


def test_why_that_plan_names_sections_to_a_local_model_and_counts_to_a_cloud_one(monkeypatch):
    from agent_friday.services import turn_receipt
    receipt = {"sections": [
        {"name": "recent_memories", "sent": True, "planner": "include", "applied": True,
         "destination_class": "cloud"},
        {"name": "active_tasks", "sent": False, "planner": "withhold", "applied": True,
         "destination_class": "cloud"}]}
    monkeypatch.setattr(turn_receipt, "build", lambda tid: receipt)
    monkeypatch.setattr(sh, "last_turn_for", lambda conv: "t-last")
    for provider, names in (("ollama", True), ("anthropic", False)):
        toks = [agent._CURRENT_CONVERSATION.set("c1"), agent._CURRENT_PROVIDER.set(provider)]
        try:
            out = agent._tool_why_that_plan({})
        finally:
            agent._CURRENT_PROVIDER.reset(toks.pop())
            agent._CURRENT_CONVERSATION.reset(toks.pop())
        assert ("active_tasks" in out) is names, (provider, out)
    monkeypatch.setattr(sh, "last_turn_for", lambda conv: None)
    assert "not have a turn" in agent._tool_why_that_plan({})

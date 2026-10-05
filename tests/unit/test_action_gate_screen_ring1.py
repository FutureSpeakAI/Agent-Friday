"""screen_select is an INTERNAL ring-1 tool; organize_email is classified exactly as before (SENSITIVE).

Showing ticks changes nothing, so the gate lets it through. Every change to the mail still goes
through organize_email, which stays OUTWARD and SELF_GATED: it raises its own card.
"""
from __future__ import annotations

from agent_friday.governance import action_gate as ag
from agent_friday.services import agent


def test_screen_select_is_internal_and_not_self_gated():
    assert "screen_select" in ag.INTERNAL_TOOLS
    assert "screen_select" not in ag.OUTWARD_TOOLS and "screen_select" not in ag.SELF_GATED
    cls = ag.classify("screen_select", {"op": "select", "scope": "all", "match": {"category": "newsletters"}})
    assert cls[0] == ag.INTERNAL, cls


def test_it_sits_in_ring_one_beside_navigate_to():
    rings = agent.TOOL_RINGS if hasattr(agent, "TOOL_RINGS") else None
    if rings is None:
        import inspect
        src = inspect.getsource(agent)
        assert '"screen_select":        1' in src
    else:
        assert rings["screen_select"] == 1 and rings["navigate_to"] == 1


def test_organize_email_is_unchanged_outward_and_self_gated():
    assert "organize_email" in ag.OUTWARD_TOOLS and "organize_email" in ag.SELF_GATED
    assert ag.classify("organize_email", {"action": "archive", "selection": "screen"})[0] == ag.OUTWARD
    assert ag.classify("organize_email", {"action": "star", "query": "is:unread"})[0] == ag.OUTWARD


def test_a_screen_tool_cannot_press_a_button_or_answer_a_card():
    """The See & Touch tools offer no way to submit, send or decide."""
    tool = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "screen_select")
    props = set(tool["input_schema"]["properties"])
    assert props == {"op", "scope", "match", "label"}
    assert not ({"approve", "decision", "card_id", "send", "confirm"} & props)

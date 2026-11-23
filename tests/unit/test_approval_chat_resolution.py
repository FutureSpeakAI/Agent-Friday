"""A card the owner approves in chat is resolved once, everywhere, and only the owner's own page can do it.

The owner's typed yes on their own page answers the confirmation card of the question it replies to: the card is
approved and spent in that one step, the page feed says it is resolved (so every open list drops it), and the pending
list no longer holds it. Nothing a model writes (a turn that says "approved", the same call issued again with no new
owner message), and no yes from a phone, a relay or a script, resolves a card. Synthetic data only.
"""
from __future__ import annotations

import queue

import pytest

import agent_friday.services.agent as agent
from agent_friday.services import approval_feed, approvals, taint

SID = "2026-11-22-chat-approve"
CALL = {"title": "Synthetic lunch", "start": "2026-11-25T12:00"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRIDAY_SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    approval_feed.reset()
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "create_calendar_event",
                        lambda inp: ran.append(dict(inp or {})) or '{"created": true}')
    yield ran
    agent._PENDING_CONFIRMATIONS.clear()
    approval_feed.reset()
    taint.reset()


def _turn(message, owner_surface=True):
    return agent.prepare_confirmation_ctx(SID, message, {"authenticated": True, "owner_surface": owner_surface})


def _ask():
    """The first ask: the question goes to chat and the same request is raised as one tray card."""
    out = agent._execute_tool("create_calendar_event", dict(CALL), session_ctx=_turn("Put lunch on the calendar"))
    assert "CONFIRMATION REQUIRED" in out, out
    (card,) = approvals.list_approvals(status="pending", kind="tool_confirm")
    return card


def _frames(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_the_owners_yes_in_chat_resolves_the_card_everywhere_and_it_runs_once(_isolate):
    card = _ask()
    q = approval_feed.subscribe()
    agent._execute_tool("create_calendar_event", dict(CALL), session_ctx=_turn("yes"))
    assert _isolate == [CALL], "the approved action did not run exactly once"
    stored = approvals.get_approval(card["approval_id"])
    assert stored["status"] == "approved" and stored["consumed"] is True, stored
    assert approvals.list_approvals(status="pending") == [], "the resolved card is still in the pending list"
    resolved = [f for f in _frames(q) if f.get("type") == "resolved" and f.get("approval_id") == card["approval_id"]]
    assert len(resolved) == 1, "every open list must be told once that the card is resolved"


def test_a_yes_from_a_phone_a_relay_or_a_script_resolves_nothing(_isolate):
    card = _ask()
    out = agent._execute_tool("create_calendar_event", dict(CALL), session_ctx=_turn("yes", owner_surface=False))
    assert _isolate == [], "an off-page yes ran the action"
    assert approvals.get_approval(card["approval_id"])["status"] == "pending", out
    assert [c["approval_id"] for c in approvals.list_approvals(status="pending")] == [card["approval_id"]]


def test_a_model_that_says_approved_or_calls_again_resolves_nothing(_isolate):
    card = _ask()
    # No new owner message: the model re-issues the call as if the owner had approved it.
    same_turn = _turn("Put lunch on the calendar")
    for _ in range(3):
        agent._execute_tool("create_calendar_event", dict(CALL), session_ctx=same_turn)
    assert _isolate == [], "a model-authored claim of approval ran the action"
    stored = approvals.get_approval(card["approval_id"])
    assert stored["status"] == "pending" and not stored.get("consumed"), stored
    assert approvals.list_approvals(status="pending", kind="tool_confirm"), "the card was resolved by the model"


def test_the_card_the_tray_approves_is_dropped_from_the_pending_list_too(_isolate):
    card = _ask()
    q = approval_feed.subscribe()
    rec, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui",
                                             source="desktop_press")
    assert won and rec["status"] == "approved"
    assert approvals.list_approvals(status="pending") == []
    assert any(f.get("type") == "resolved" and f.get("approval_id") == card["approval_id"] for f in _frames(q))

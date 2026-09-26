"""navigate_to and check_situation: in the catalogue, in voice, and governed.

Opening something in the owner's own UI is an internal action: no approval
card and no confirmation round-trip, in text and in voice alike. It is still
ring 1, so a turn that came in by phone cannot drive the screen at home, and
check_situation, which only reads Friday's own state, stays ring 0.
"""
import pytest

import agent_friday.services.agent as agent
from agent_friday.services import situation


@pytest.fixture(autouse=True)
def _no_audit_signing(monkeypatch):
    import agent_friday.privacy.vault_crypto as vc
    monkeypatch.setattr(vc, "sign_entry", lambda e, *a, **k: e, raising=False)
    agent._PENDING_CONFIRMATIONS.clear()


@pytest.fixture
def opened(monkeypatch):
    from agent_friday.services import desktop_targets
    calls = []

    def fake(kind, **kw):
        calls.append(dict(kw, kind=kind))
        return {"status": "opened", "text": "NAV_OK:messages — opened the email X on the desktop."}

    monkeypatch.setattr(desktop_targets, "open_on_desktop", fake)
    return calls


def test_both_tools_are_in_the_catalogue_with_handlers():
    names = {t["name"] for t in agent.CLAUDE_TOOLS}
    assert {"navigate_to", "check_situation"} <= names
    assert agent.CLAUDE_TOOL_HANDLERS["navigate_to"] is agent._tool_navigate_to
    assert agent.CLAUDE_TOOL_HANDLERS["check_situation"] is agent._tool_check_situation


def test_rings_internal_and_no_confirmation():
    from agent_friday.governance import action_gate
    assert agent.TOOL_RINGS["navigate_to"] == 1
    assert agent.TOOL_RINGS["check_situation"] == 0
    assert {"navigate_to", "check_situation"} <= set(action_gate.INTERNAL_TOOLS)
    assert "navigate_to" not in agent._ALWAYS_CONFIRM
    assert "check_situation" not in agent._ALWAYS_CONFIRM


def test_taint_knows_both_tools():
    """navigate_to's answer names senders, subjects and file names found
    outside, so it is recorded like any read; check_situation is Friday's own."""
    from agent_friday.services import taint
    assert taint.TOOL_ROLES["navigate_to"] == {} and taint.TOOL_ROLES["check_situation"] == {}
    assert "navigate_to" not in taint.ECHO_TOOLS
    assert "check_situation" in taint.OWN_DATA_TOOLS


def test_voice_declares_both_from_the_text_registry():
    from agent_friday.services import voice_engine
    names = [n for n, _d, _s in voice_engine._voice_shared_tool_specs()]
    assert "navigate_to" in names and "check_situation" in names


def test_an_interactive_turn_opens_without_asking(opened):
    ctx = agent.prepare_confirmation_ctx("s-nav", "open the Harbor Legal email",
                                         {"authenticated": True})
    res = agent._execute_tool("navigate_to", {"kind": "email", "query": "the Harbor Legal email"},
                              session_ctx=ctx)
    assert res.startswith("NAV_OK:messages"), res
    assert opened == [{"kind": "email", "query": "the Harbor Legal email", "id": "",
                       "workspace": "", "section": ""}]
    assert "s-nav" not in agent._PENDING_CONFIRMATIONS


def test_voice_opens_without_asking(opened):
    res = agent._execute_tool("navigate_to", {"kind": "email", "query": "Harbor"},
                              session_ctx={"authenticated": True, "surface": "voice-live",
                                           "taint_key": "voice-live"})
    assert res.startswith("NAV_OK:messages"), res


def test_a_phone_turn_cannot_drive_the_screen_but_can_ask_what_is_happening():
    allowed, reason = agent._governance_check("navigate_to", {}, {"origin": "phone",
                                                                  "authenticated": True})
    assert not allowed and "by phone" in reason
    allowed, _ = agent._governance_check("check_situation", {}, {"origin": "phone"})
    assert allowed


def test_check_situation_pins_the_conversation_it_runs_in(monkeypatch):
    from agent_friday import core
    monkeypatch.setitem(situation._PINS, "loaded", True)
    monkeypatch.setitem(situation._PINS, "ids", {})
    core.turn_begin("turn-pin", "conv-pin")
    try:
        out = agent._tool_check_situation({"pin": True})
    finally:
        core.turn_end("turn-pin")
    assert out.startswith("Situation at ") and "(Pinned:" in out
    assert situation.is_pinned("conv-pin")
    assert "(Nothing pinned:" in agent._tool_check_situation({"pin": True})
    situation.set_pinned("conv-pin", False)


def test_check_situation_full_is_structured(monkeypatch):
    import json
    out = json.loads(agent._tool_check_situation({"detail": "full"}))
    assert {"machine", "models", "activity"} <= set(out)

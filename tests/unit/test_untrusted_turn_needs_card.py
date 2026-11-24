"""A turn that carries outside text is not the owner's command, even on the owner's own device.

The assist sheet sends what is on the phone's screen. That text may say "send this to ...", and a model that
read it may try to. For such a turn (``untrusted_turn`` in the session context) a tool call that would otherwise
ask the owner a yes/no question in chat gets an approval card on the PC instead: a "yes" typed or spoken in chat
cannot approve a card, and the card says the request came from outside text.
"""
import pytest

from agent_friday.services import agent, approvals


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    agent._PENDING_CONFIRMATIONS.clear()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(agent, "_sandbox_policy", lambda *a: (True, ""))
    monkeypatch.setattr(agent, "_tools_requiring_confirmation", lambda: {"run_command"})
    yield
    agent._PENDING_CONFIRMATIONS.clear()


def context(untrusted: bool, conversation="one"):
    ctx = {"authenticated": True, "conversation_id": conversation, "owner_surface": True}
    if untrusted:
        ctx["untrusted_turn"] = True
    return agent.prepare_confirmation_ctx("same-day", "what does this mean?", ctx)


def test_without_outside_text_the_owners_own_turn_asks_in_chat_as_before():
    result = agent._execute_tool("run_command", {"command": "Write-Output first"}, session_ctx=context(False))
    assert "CONFIRMATION REQUIRED" in result


def test_with_outside_text_the_same_call_becomes_a_card_not_a_chat_question(monkeypatch):
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command", lambda args: ran.append(args) or "ran")
    result = agent._execute_tool("run_command", {"command": "Write-Output first"}, session_ctx=context(True))
    assert ran == []                                               # nothing ran
    assert "CONFIRMATION REQUIRED" not in result                   # and a chat "yes" has nothing to answer
    pending = approvals.list_approvals(status="pending")
    assert pending, result
    assert agent.resume_confirmed_action(agent.prepare_confirmation_ctx(
        "same-day", "yes go ahead", {"authenticated": True, "conversation_id": "one", "owner_surface": True})) is None
    assert ran == []


def test_the_decision_receipt_says_the_turn_carried_outside_text(monkeypatch):
    from agent_friday.governance import action_gate
    seen = []
    real = action_gate._receipt
    monkeypatch.setattr(action_gate, "_receipt", lambda row, *a, **k: (seen.append(dict(row)), real(row, *a, **k))[1])
    agent._execute_tool("run_command", {"command": "Write-Output first"}, session_ctx=context(True))
    assert any(row.get("untrusted_input") for row in seen), seen
    agent._execute_tool("run_command", {"command": "Write-Output second"}, session_ctx=context(False, "two"))
    assert not any(row.get("untrusted_input") for row in seen[len(seen) - 1:])

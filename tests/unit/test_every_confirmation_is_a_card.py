"""Every confirmation the chat gate asks for is also an approval card.

The chat question is one way to answer a gated action; the card is the other,
and it exists from the FIRST ask. If the model paraphrases the question, or the
owner answers in words the yes-parser does not accept, the action is still
waiting on a button the owner can see (the approvals feed, the chat, Needs
you) instead of on nothing.

The invariants pinned here:

  * the first ask raises exactly one pending card, linked to the pending entry;
  * one request has one card: a retry or the second ask reuses it;
  * a chat yes runs the action once and resolves that card;
  * approving the card runs the action once through the executor, and a later
    chat yes does not run it again;
  * a reply that disarms the chat question leaves the card waiting;
  * a calendar title with nobody to invite does not say "and invite".
"""
import pytest

from agent_friday.services import agent, approvals, approval_executor


CID = "conv-cards"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    agent._PENDING_CONFIRMATIONS.clear()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(agent, "_sandbox_policy", lambda *a: (True, ""))
    monkeypatch.setattr(agent, "_tools_requiring_confirmation", lambda: {"run_command"})
    monkeypatch.setattr(approval_executor, "_post_back", lambda *a: None)
    approval_executor.register()
    yield
    agent._PENDING_CONFIRMATIONS.clear()


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "run_command",
                        lambda args: seen.append(dict(args)) or "observed output")
    return seen


def turn(text):
    return agent.prepare_confirmation_ctx(
        "same-day", text, {"authenticated": True, "conversation_id": CID})


def call(ctx, command="Write-Output first"):
    return agent._execute_tool("run_command", {"command": command}, session_ctx=ctx)


def pending_cards():
    return [c for c in approvals.list_approvals(status="pending")
            if c.get("kind") == "tool_confirm"]


def entries():
    return list((agent._PENDING_CONFIRMATIONS.get(f"conversation:{CID}") or {}).values())


def test_first_ask_raises_one_card_linked_to_the_pending_entry(calls):
    result = call(turn("run this"))
    assert result.startswith("[CONFIRMATION REQUIRED]")
    cards = pending_cards()
    assert len(cards) == 1
    card = cards[0]
    assert card["payload"]["tool"] == "run_command"
    assert card["payload"]["input"] == {"command": "Write-Output first"}
    assert card["payload"]["conversation_id"] == CID
    assert [e.get("approval_id") for e in entries()] == [card["approval_id"]]
    # The model is told a card is waiting, and may still ask the question.
    assert "approval card" in result.lower()
    assert result.rstrip().endswith('"')
    assert not calls


def test_first_ask_card_reaches_needs_you(calls, monkeypatch):
    """The card is announced like any other: a Needs-you notice and the feed."""
    import agent_friday.notifications_engine as ne
    from agent_friday.services import approval_feed
    pushed, fed = [], []
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    monkeypatch.setattr(approval_feed, "card_pending",
                        lambda rec, interrupt=True: fed.append(rec["approval_id"]))
    call(turn("run this"))
    card = pending_cards()[0]
    assert [p["kind"] for p in pushed] == ["approval_pending"]
    assert pushed[0]["meta"]["approval_id"] == card["approval_id"]
    assert fed == [card["approval_id"]]


def test_a_retry_in_the_same_turn_reuses_the_card(calls):
    ctx = turn("run this")
    call(ctx)
    call(ctx)
    assert len(pending_cards()) == 1


def test_chat_yes_runs_once_and_resolves_the_card(calls):
    call(turn("run this"))
    card = pending_cards()[0]
    agent.resume_confirmed_action(turn("yes"))
    assert calls == [{"command": "Write-Output first"}]
    rec = approvals.get_approval(card["approval_id"])
    assert rec["status"] == "approved" and rec["consumed"]
    assert rec.get("used_by") == "chat_confirmation"
    # The card was spent by the chat; a click on it afterwards runs nothing.
    approvals.decide(card["approval_id"], "approve")
    assert len(calls) == 1
    assert not pending_cards()


def test_card_approval_runs_once_and_a_later_chat_yes_does_not_rerun(calls):
    call(turn("run this"))
    card = pending_cards()[0]
    approvals.decide(card["approval_id"], "approve")
    assert calls == [{"command": "Write-Output first"}]
    assert approvals.get_approval(card["approval_id"])["consumed"]
    assert agent.resume_confirmed_action(turn("yes")) is None
    assert len(calls) == 1


def test_second_ask_reuses_the_first_card(calls):
    call(turn("run this"))
    first = pending_cards()
    result = call(turn("what does that do?"))
    assert "CONFIRMATION ESCALATED" in result
    assert [c["approval_id"] for c in pending_cards()] == [first[0]["approval_id"]]
    assert not calls


def test_a_sentence_that_disarms_the_question_keeps_the_card(calls):
    call(turn("run this"))
    card = pending_cards()[0]
    turn("I approve your next steps")            # not a bare yes: disarms the chat ask
    assert all(not e.get("awaiting_reply") for e in entries())
    agent.disarm_pending("same-day", CID)
    rec = approvals.get_approval(card["approval_id"])
    assert rec["status"] == "pending" and not rec.get("consumed")
    assert not calls
    # The card still decides it.
    approvals.decide(card["approval_id"], "approve")
    assert calls == [{"command": "Write-Output first"}]


def test_chat_yes_claims_the_card_for_the_same_file_spelled_differently(monkeypatch, tmp_path):
    """The gate treats ``x.md`` and ``./x.md`` as one question; so does the card."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent, "_tools_requiring_confirmation", lambda: {"write_file"})

    class Ctx:
        def __init__(self, inp, sc):
            self.tool_name, self.input, self.session_ctx, self.meta = "write_file", inp, sc, {}

    verdict = agent._hook_confirmation_gate(Ctx({"path": "notes.md"}, turn("write it")))
    assert verdict is not agent._hooks.ALLOW
    card = pending_cards()[0]
    verdict = agent._hook_confirmation_gate(Ctx({"path": "./notes.md"}, turn("yes")))
    assert verdict is agent._hooks.ALLOW
    rec = approvals.get_approval(card["approval_id"])
    assert rec["status"] == "approved" and rec["consumed"]


@pytest.mark.parametrize("inp", [
    {"title": "Picnic at Riverside Park"},
    {"title": "Picnic at Riverside Park", "attendees": []},
    {"title": "Picnic at Riverside Park", "attendees": ""},
    {"title": "Picnic at Riverside Park", "attendees": None},
])
def test_calendar_title_without_attendees_has_no_invite_clause(inp):
    title = agent._taint_title("create_calendar_event", inp)
    assert "invite" not in title
    assert "Riverside Park" in title
    assert not title.rstrip().endswith(("—", "-"))
    assert "invite" not in agent._confirmation_question("create_calendar_event", inp)


def test_calendar_title_with_attendees_keeps_them():
    title = agent._taint_title("create_calendar_event", {
        "title": "Picnic", "attendees": ["owner@example.com", "guest@example.com"]})
    assert "invite owner@example.com, guest@example.com" in title
    assert "invite guest@example.com" in agent._taint_title(
        "book_slot", {"title": "Picnic", "attendees": "guest@example.com"})

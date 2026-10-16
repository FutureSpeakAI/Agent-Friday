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
        "same-day", text, {"authenticated": True, "conversation_id": CID, "owner_surface": True})


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


# ── a chat "no" decides the card; notices name only the tool; a yes from elsewhere ──
# These name the `isolated` fixture explicitly as well, so a plain driver that
# does not apply autouse fixtures still isolates them.

def test_a_chat_no_denies_the_card(isolated, calls):
    call(turn("run this"))
    card = pending_cards()[0]
    turn("no")
    rec = approvals.get_approval(card["approval_id"])
    assert rec["status"] == "denied", "a chat no left the card live on the screen"
    assert rec["decided_by"] == "owner" and rec["decision_note"] == "answered no in chat"
    # A later click on the card runs nothing.
    approvals.decide(card["approval_id"], "approve")
    assert not calls


def test_the_tray_notice_names_only_the_tool(isolated, calls, monkeypatch):
    import agent_friday.notifications_engine as ne
    pushed = []
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    call(turn("run this"), command="Write-Output private-words")
    (notice,) = [p for p in pushed if p.get("kind") == "approval_pending"]
    shown = notice["title"] + " " + notice["body"]
    assert "private-words" not in shown, shown
    assert "run_command" in notice["title"]
    # The card itself keeps the full question for the screen.
    assert "private-words" in pending_cards()[0]["title"]


def test_the_text_message_offer_names_only_the_tool(isolated, calls, monkeypatch):
    from agent_friday.phone import config as phone_config
    from agent_friday.phone import service as phone_service
    sent = []
    monkeypatch.setattr(phone_config, "load", lambda: {"enabled": True, "sms_approvals": True})
    monkeypatch.setattr(phone_config, "verified_owner_cell", lambda: "owner-cell")
    monkeypatch.setattr(phone_service, "_may_text_about", lambda rec: True)
    monkeypatch.setattr(phone_service, "_gate_text", lambda t: t)
    monkeypatch.setattr(phone_service, "_load_state", lambda name, default: {"codes": {}, "failures": []})
    monkeypatch.setattr(phone_service, "_save_state", lambda name, st: None)
    monkeypatch.setattr(phone_service, "text_owner", lambda body, reason="": sent.append(body))
    monkeypatch.setattr(approvals, "_hold", lambda rec: None)     # the quiet-hours hold is not under test
    call(turn("run this"), command="Write-Output private-words")
    assert phone_service.offer_pending_approvals() == 1
    assert "private-words" not in sent[0], sent[0]
    assert "run_command" in sent[0]


def test_a_yes_from_outside_the_owners_page_leaves_the_card_for_the_screen(isolated, calls):
    call(turn("run this"))
    card = pending_cards()[0]
    ctx = agent.prepare_confirmation_ctx(
        "same-day", "yes", {"authenticated": True, "conversation_id": CID, "owner_surface": False})
    assert not ctx.get("confirm_granted_fp"), "a yes that is not the owner's own page granted the card"
    agent.resume_confirmed_action(ctx)
    assert call(ctx).startswith("[CONFIRMATION")
    assert not calls
    rec = approvals.get_approval(card["approval_id"])
    assert rec["status"] == "pending" and not rec.get("consumed")


def test_the_owner_surface_is_read_from_the_live_request(isolated, monkeypatch):
    """Inside a request the surface is judged from the request itself, never taken from the caller."""
    from flask import Flask
    from agent_friday.services import reflex_chat
    app = Flask("surface-check")
    monkeypatch.setattr(reflex_chat, "_request_is_local", lambda: True)
    monkeypatch.setattr(reflex_chat, "_request_is_page", lambda: True)
    with app.test_request_context("/api/chat", method="POST",
                                  json={"message": "yes", "source": "app_page"}):
        assert agent.prepare_confirmation_ctx("same-day", "yes", {"conversation_id": CID})["owner_surface"] is True
    with app.test_request_context("/api/chat", method="POST",
                                  json={"message": "yes", "source": "sms"}):
        ctx = agent.prepare_confirmation_ctx("same-day", "yes",
                                             {"conversation_id": CID, "owner_surface": True})
        assert ctx["owner_surface"] is False
    with app.test_request_context("/api/chat", method="POST",
                                  json={"message": "yes", "source": "app_page", "voice_mode": True}):
        assert agent.prepare_confirmation_ctx("same-day", "yes", {"conversation_id": CID})["owner_surface"] is False

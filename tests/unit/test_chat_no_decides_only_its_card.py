"""A chat no decides only the card of the question it answers, and only from the owner's page.

The invariants pinned here:

  * a reply that opens with a refusal word but carries a correction or a new
    request ("No, I meant the other one", "Wait, which file?") clears the chat
    question and leaves every card live on the screen;
  * a bare refusal ("no", "no thanks", "cancel") typed on the owner's own page
    denies the card of the question awaiting a reply, and no older card;
  * a no from any other surface denies nothing;
  * a caller that never names the surface (owner_surface None) is not the
    owner's page: its yes cannot claim a card and its no cannot deny one.

The tests name the `isolated` fixture explicitly, so a plain driver that does
not apply autouse fixtures still isolates them.
"""
import pytest

from agent_friday.services import agent, approvals, approval_executor


CID = "conv-riverside"


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


_UNSET = object()


def turn(text, surface=True):
    base = {"authenticated": True, "conversation_id": CID}
    if surface is not _UNSET:
        base["owner_surface"] = surface
    return agent.prepare_confirmation_ctx("springfield", text, base)


def call(ctx, command="Write-Output first"):
    return agent._execute_tool("run_command", {"command": command}, session_ctx=ctx)


def status(card):
    return approvals.get_approval(card["approval_id"])["status"]


def only_card():
    (card,) = [c for c in approvals.list_approvals(status="pending")
               if c.get("kind") == "tool_confirm"]
    return card


@pytest.mark.parametrize("reply", [
    "No, I meant the other one",
    "Wait, which file?",
    "no, put it in the Riverside Park folder instead",
    "stop, use the second address",
])
def test_a_correction_opener_leaves_the_card_live(isolated, calls, reply):
    call(turn("run this"))
    card = only_card()
    turn(reply)
    assert status(card) == "pending", f"{reply!r} decided the card"
    # The chat question itself is answered: nothing is armed for a later yes.
    assert f"conversation:{CID}" not in agent._PENDING_CONFIRMATIONS
    assert not calls


def test_a_bare_no_denies_only_the_awaiting_card(isolated, calls):
    call(turn("run this"))
    older = only_card()
    # The conversation moves on, and a second question is asked.
    call(turn("then the other thing"), command="Write-Output second")
    newer = [c for c in approvals.list_approvals(status="pending")
             if c["approval_id"] != older["approval_id"]][0]
    turn("no thanks")
    assert status(newer) == "denied"
    rec = approvals.get_approval(newer["approval_id"])
    assert rec["decided_by"] == "owner" and rec["decision_note"] == "answered no in chat"
    assert status(older) == "pending", "a no to one question denied an older card"
    assert not calls


@pytest.mark.parametrize("reply", ["no", "No.", "nope", "cancel", "stop", "don't", "no thanks"])
def test_each_bare_refusal_denies_the_card(isolated, calls, reply):
    call(turn("run this"))
    card = only_card()
    turn(reply)
    assert status(card) == "denied", f"{reply!r} left the card live"


def test_a_no_from_another_surface_denies_nothing(isolated, calls):
    call(turn("run this"))
    card = only_card()
    turn("no", surface=False)
    assert status(card) == "pending"


def test_an_unnamed_surface_cannot_claim_or_deny_a_card(isolated, calls):
    call(turn("run this"))
    card = only_card()
    yes = turn("yes", surface=None)
    assert not yes.get("confirm_granted_fp"), "a yes from an unnamed surface granted the card"
    assert yes.get("confirm_on_card") == card["approval_id"]
    agent.resume_confirmed_action(yes)
    assert not calls
    # Even an entry marked granted is not spent by a call with no surface named.
    unnamed = turn("run this", surface=_UNSET)
    assert unnamed.get("owner_surface") is None
    for entry in agent._PENDING_CONFIRMATIONS[f"conversation:{CID}"].values():
        entry["granted"] = True
    assert "CONFIRMATION ON THE CARD" in call(unnamed)
    assert not calls
    turn("no", surface=None)
    assert status(card) == "pending"


@pytest.mark.parametrize("text,bare", [
    ("no", True), ("No thanks!", True), ("cancel it", True), ("um, no", True),
    ("don't, thanks", True), ("no no no", True),
    ("No, I meant the other one", False), ("Wait, which file?", False), ("wait", False),
    ("no, but send it tomorrow", False), ("no\ncancel", False),
    ("don't ask again, just do it", False),
])
def test_the_bare_refusal_shape(text, bare):
    assert agent._is_bare_refusal(text) is bare

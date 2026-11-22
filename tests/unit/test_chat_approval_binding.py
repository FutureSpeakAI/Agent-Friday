"""An owner's "approved" in chat binds to the one question it answers; with more than one it asks which.

* The question a yes answers is the action asked about: the same tool with the same meaningful arguments. A call the
  model re-issues with empty or default arguments added (`tab: ""`) is the same question, not a fresh one.
* With more than one question waiting, a bare "approved" binds to none and approves none: the model is told to ask the
  owner which. It never guesses and never approves them all.
* A model-authored "approved" binds nothing (only a message from the owner's own page does).
* Receipts and results agree: a call answered with "not run" text never ran, and a call that ran never says it is
  waiting. Synthetic data only.
"""
from __future__ import annotations

import pytest

import agent_friday.services.agent as agent
from agent_friday.services import approvals, taint

SID = "2026-11-22-binding"
NOT_RUN = ("[CONFIRMATION REQUIRED]", "[CONFIRMATION ESCALATED]", "[CONFIRMATION ON THE CARD]",
           "[CONFIRMATION WHICH ONE]", "[CONFIRMATION UNRESOLVED]", "[APPROVAL CARD RAISED]")


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
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "navigate", lambda inp: ran.append(("navigate", dict(inp))) or "went")
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "create_calendar_event",
                        lambda inp: ran.append(("create_calendar_event", dict(inp))) or '{"created": true}')
    yield ran
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()


def _turn(message, owner_surface=True):
    return agent.prepare_confirmation_ctx(SID, message, {"authenticated": True, "owner_surface": owner_surface})


def _call(name, inp, ctx):
    return agent._execute_tool(name, inp, session_ctx=ctx)


def _cards():
    return approvals.list_approvals(status="pending", kind="tool_confirm")


def _said_not_run(out):
    return any(str(out).lstrip().startswith(m) or m in str(out)[:80] for m in NOT_RUN)


def test_a_re_issued_call_with_empty_default_arguments_is_the_question_the_owner_answered(_isolate):
    first = _call("navigate", {"workspace": "code"}, _turn("switch to code"))
    assert "CONFIRMATION REQUIRED" in first, first
    (card,) = _cards()
    yes = _turn("approved")
    out = _call("navigate", {"workspace": "code", "tab": "", "section": None}, yes)   # defaults the model added
    assert _isolate == [("navigate", {"workspace": "code", "tab": "", "section": None})], out
    assert not _said_not_run(out), out
    assert len(approvals.list_approvals(kind="tool_confirm")) == 1, "a second card was raised for the same action"
    stored = approvals.get_approval(card["approval_id"])
    assert stored["status"] == "approved" and stored["consumed"] is True


def test_a_different_argument_is_still_a_different_question(_isolate):
    _call("navigate", {"workspace": "code"}, _turn("switch to code"))
    out = _call("navigate", {"workspace": "settings"}, _turn("approved"))
    assert _isolate == [] and _said_not_run(out), out


def test_with_two_questions_waiting_a_bare_approved_binds_neither_and_asks_which(_isolate):
    _call("navigate", {"workspace": "code"}, _turn("switch to code"))
    _call("create_calendar_event", {"title": "Synthetic", "start": "2026-11-25T12:00"}, _turn("book it"))
    assert len(_cards()) == 2
    yes = _turn("approved")
    a = _call("navigate", {"workspace": "code"}, yes)
    b = _call("create_calendar_event", {"title": "Synthetic", "start": "2026-11-25T12:00"}, yes)
    assert _isolate == [], "an approval that could mean either ran one of them"
    assert "WHICH ONE" in a and "WHICH ONE" in b, (a, b)
    assert len(_cards()) == 2 and all(c["status"] == "pending" for c in _cards()), "a card was decided by a guess"


def test_a_message_that_is_not_the_owners_own_approval_binds_nothing(_isolate):
    _call("navigate", {"workspace": "code"}, _turn("switch to code"))
    out = _call("navigate", {"workspace": "code"}, _turn("approved", owner_surface=False))
    assert _isolate == [] and _said_not_run(out), out
    assert [c["status"] for c in _cards()] == ["pending"]


FLOWS = ["first ask", "same turn again", "off-page yes", "two waiting", "approved once"]


@pytest.mark.parametrize("flow", FLOWS)
def test_a_not_run_result_never_comes_with_the_action_having_run_and_the_reverse(_isolate, flow):
    seen = []

    def run(name, inp, ctx):
        before = len(_isolate)
        out = _call(name, inp, ctx)
        seen.append((out, len(_isolate) > before))

    nav = ("navigate", {"workspace": "code"})
    cal = ("create_calendar_event", {"title": "Synthetic", "start": "2026-11-25T12:00"})
    if flow == "first ask":
        run(*nav, _turn("switch to code"))
    elif flow == "same turn again":
        ctx = _turn("switch to code")
        run(*nav, ctx)
        run(*nav, ctx)
    elif flow == "off-page yes":
        run(*nav, _turn("switch to code"))
        run(*nav, _turn("approved", owner_surface=False))
    elif flow == "two waiting":
        run(*nav, _turn("switch to code"))
        run(*cal, _turn("book it"))
        yes = _turn("approved")
        run(*nav, yes)
        run(*cal, yes)
    else:
        run(*nav, _turn("switch to code"))
        run(*nav, _turn("approved"))
    assert seen, flow
    for out, did_run in seen:
        assert not (_said_not_run(out) and did_run), "the receipt says waiting but the action ran: " + str(out)[:120]
        assert did_run or _said_not_run(out) or "went" not in str(out), "it ran according to the result but not the handler"
    if flow in ("first ask", "off-page yes", "two waiting"):
        assert not any(did for _out, did in seen), flow
    if flow == "approved once":
        assert [did for _out, did in seen] == [False, True]

def test_after_ask_which_the_owner_names_one_and_only_that_one_runs(_isolate):
    """Asking which must be answerable in chat: the owner names one, the model asks it again, and the next yes binds
    that one alone. The other card stays waiting in the tray and no longer blocks a yes in this conversation."""
    nav = ("navigate", {"workspace": "code"})
    cal = ("create_calendar_event", {"title": "Synthetic", "start": "2026-11-25T12:00"})
    _call(*cal, _turn("book it"))
    _call(*nav, _turn("switch to code"))
    yes = _turn("approved")
    assert "WHICH ONE" in _call(*nav, yes)
    named = _turn("the code switch one")                 # the owner answers the which-question
    again = _call(*nav, named)                            # the model asks about that one again
    assert _said_not_run(again) and _isolate == [], again
    out = _call(*nav, _turn("yes"))
    assert _isolate == [nav], "the named question did not run once, or the other one ran: %r / %s" % (_isolate, out)
    assert not _said_not_run(out), out
    waiting = [c for c in _cards() if c["status"] == "pending"]
    assert [c["payload"]["tool"] for c in waiting] == ["create_calendar_event"], "the other card was decided by the answer"
    assert "WHICH ONE" not in _call(*cal, _turn("approved")), "the card the owner did not pick still blocks every yes"


def test_whitespace_in_a_body_is_part_of_the_question():
    a = agent._action_fingerprint("write_file", {"path": "x.md", "content": "x"})
    b = agent._action_fingerprint("write_file", {"path": "x.md", "content": "x\n"})
    assert a != b, "a grant for one body could be spent on a different body"
    assert agent._action_fingerprint("navigate", {"workspace": "code"}) == agent._action_fingerprint(
        "navigate", {"workspace": " code ", "tab": ""})

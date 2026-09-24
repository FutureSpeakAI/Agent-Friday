"""The approval loop of 2026-09-22, and the two holes found underneath it.

WHAT HAPPENED

Stephen asked Friday to open a file it had said it wrote. Friday asked
"Would you like me to create bold-panel-prep.md?" and then asked it again,
five times, through two plain approvals:

    Stephen: "um, sure"
    Friday:  "Would you like me to create bold-panel-prep.md?"
    Stephen: "I just authorized that, so yes."
    Friday:  "Would you like me to create ~/Friday Creations/bold-panel-prep.md?"

THE INVARIANTS THESE TESTS PIN

  1. A granted approval satisfies the request it was granted for - and no
     other request.
  2. A gate may not ask the same question twice without new information.

Both were violated, in opposite directions, by the same design: the grant was
a single session-wide boolean (`confirm_granted`) and the pending action was a
single slot keyed only by session id.

  * It answered NO particular question, so a yes for one file would authorise
    a write to any other file - `test_a_yes_for_one_file_does_not_authorise_another`.
  * `_current_session_id()` returns the calendar DATE, so every surface open
    that day shares one slot. The front page and the chat tab clobbered each
    other's pending action, and a yes typed in one granted the other's -
    `TestTwoSurfacesOneDay`. Stephen asked whether having the front page open
    mattered; it did, and this is the test that says how.
  * And it never terminated, because `_is_affirmative` was a start-anchored
    match over a fixed vocabulary. "um, sure" has filler in front of it and
    "I just authorized that, so yes." has the yes at the end. Neither could
    ever match - `TestTheLoopItself`.

WHY THE LOOP IS A SAFETY BUG AND NOT AN ANNOYANCE

A user who has said yes four times and been asked a fifth is being trained to
approve without reading. The gate that cannot stop asking is the one that
eventually gets a rubber-stamped yes for something else.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import agent_friday.services.agent as agent  # noqa: E402
from agent_friday.services import approvals  # noqa: E402


#: The date-shaped id every surface really uses. Not a literal in the code
#: under test - `_current_session_id()` returns `datetime.now()` formatted -
#: but the shape matters to the story, so it is written the way it appears.
SID = "2026-09-22"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    agent._PENDING_CONFIRMATIONS.clear()
    # Escalation writes a real approval card; keep it out of ~/.friday.
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    yield
    agent._PENDING_CONFIRMATIONS.clear()


class _Ctx:
    """The shape `_hook_confirmation_gate` reads."""

    def __init__(self, tool_name, inp, session_ctx):
        self.tool_name = tool_name
        self.input = inp
        self.session_ctx = session_ctx


def _gate(tool, inp, ctx):
    return agent._hook_confirmation_gate(_Ctx(tool, inp, ctx))


def _allowed(verdict) -> bool:
    return verdict is agent._hooks.ALLOW


def _text(verdict) -> str:
    return "" if _allowed(verdict) else str(getattr(verdict, "reason", verdict))


# ═══════════════════════════════════════════════════════════════════════════
#  1. THE LOOP
# ═══════════════════════════════════════════════════════════════════════════

class TestTheLoopItself:

    #: Stephen's turns, verbatim, in order.
    TRANSCRIPT = [
        "open it a new tab please",
        "You just told me that you did that already.",
        "um, sure",
        "I just authorized that, so yes.",
        "Dude, seriously? What's wrong with you?",
    ]

    def test_the_transcript_does_not_loop_forever(self):
        """The whole defect, replayed. The write must happen, once.

        Asserted as "the action eventually runs" rather than "it runs on turn
        N", because which turn it lands on depends on wording the user is not
        obliged to repeat.
        """
        wrote = []
        for i, msg in enumerate(self.TRANSCRIPT):
            ctx = agent.prepare_confirmation_ctx(SID, msg, {})
            if _allowed(_gate("write_file", {"path": "bold-panel-prep.md"}, ctx)):
                wrote.append(i)
        assert wrote, (
            "five turns including two plain approvals and the file was still "
            "never written - this is the reported loop")

    @pytest.mark.parametrize("reply", [
        "um, sure",
        "I just authorized that, so yes.",
        "uh, yeah",
        "well, ok",
        "I said yes",
        "you already have my permission",
        "of course",
        "yes, obviously",
        "sure, go ahead",
    ])
    def test_a_yes_in_ordinary_english_is_heard(self, reply):
        """Every one of these is unambiguously an approval to a human reader.

        The first two are Stephen's own words; the rest are the same two
        shapes - leading filler, and a yes in final position - which the old
        start-anchored pattern could not see by construction.
        """
        ctx = agent.prepare_confirmation_ctx(SID, "write it", {})
        _gate("write_file", {"path": "brief.md"}, ctx)
        granted = agent.prepare_confirmation_ctx(SID, reply, {})
        assert _allowed(_gate("write_file", {"path": "brief.md"}, granted)), (
            "%r was not heard as approval" % reply)

    def test_the_same_question_is_never_asked_twice(self):
        """The invariant, stated directly.

        The second unresolved ask must change mechanism rather than repeat
        itself. What it changes TO is a separate concern (a durable card);
        what matters here is that the identical question does not come back.
        """
        asked = []
        for msg in ["do the thing", "what?", "huh?", "???", "hello?"]:
            ctx = agent.prepare_confirmation_ctx(SID, msg, {})
            v = _gate("write_file", {"path": "brief.md"}, ctx)
            if not _allowed(v):
                asked.append(_text(v))

        repeats = [t for t in asked if t.startswith("[CONFIRMATION REQUIRED]")]
        assert len(repeats) <= 1, (
            "the identical question was put to the user %d times:\n%s"
            % (len(repeats), "\n".join(repeats[:3])))

    def test_an_ambiguous_reply_is_not_read_as_a_refusal(self):
        """"don't ask again, just do it" used to CANCEL the action.

        It opens with "don't", so the refusal pattern claimed it. Reading a
        demand as a refusal is the most annoying possible answer, and reading
        it as approval would be worse. It is a tie, and a tie is not an answer.
        """
        assert agent._is_ambiguous("don't ask again, just do it")
        assert not agent._is_negative("don't ask again, just do it")
        assert not agent._is_affirmative("don't ask again, just do it")

        ctx = agent.prepare_confirmation_ctx(SID, "write it", {})
        _gate("write_file", {"path": "brief.md"}, ctx)
        after = agent.prepare_confirmation_ctx(SID, "don't ask again, just do it", {})
        assert SID in agent._PENDING_CONFIRMATIONS, (
            "an ambiguous reply threw the pending action away")
        assert not after.get("confirm_granted"), (
            "an ambiguous reply was taken as approval")

    def test_a_plain_no_still_cancels(self):
        ctx = agent.prepare_confirmation_ctx(SID, "write it", {})
        _gate("write_file", {"path": "brief.md"}, ctx)
        assert SID in agent._PENDING_CONFIRMATIONS
        agent.prepare_confirmation_ctx(SID, "no, don't", {})
        assert SID not in agent._PENDING_CONFIRMATIONS


# ═══════════════════════════════════════════════════════════════════════════
#  2. A GRANT ANSWERS THE QUESTION IT WAS GIVEN FOR
# ═══════════════════════════════════════════════════════════════════════════

class TestTheGrantIsBoundToTheAction:

    def test_a_yes_for_one_file_does_not_authorise_another(self):
        """The hole under the loop, and the reason this is a security fix.

        `confirm_granted` was a session-wide boolean, so the gate allowed
        whatever gated call the model made next - regardless of whether it was
        the thing the user had been shown.
        """
        ctx = agent.prepare_confirmation_ctx(SID, "make me the brief", {})
        _gate("write_file", {"path": "bold-panel-prep.md"}, ctx)

        yes = agent.prepare_confirmation_ctx(SID, "yes", {})
        other = _gate("write_file",
                      {"path": "C:/Windows/System32/drivers/etc/hosts"}, yes)
        assert not _allowed(other), (
            "approving bold-panel-prep.md authorised a write to the hosts file")

        same = _gate("write_file", {"path": "bold-panel-prep.md"}, yes)
        assert _allowed(same), "the approved file itself was refused"

    def test_a_yes_for_one_tool_does_not_authorise_a_different_tool(self):
        ctx = agent.prepare_confirmation_ctx(SID, "switch workspace", {})
        _gate("navigate", {"workspace": "news"}, ctx)
        yes = agent.prepare_confirmation_ctx(SID, "yes", {})
        assert not _allowed(_gate("write_file", {"path": "anything.md"}, yes)), (
            "a yes for `navigate` authorised a `write_file`")

    def test_a_grant_is_spent_once(self):
        """One decision, one action - the same rule gmail_send lives by."""
        ctx = agent.prepare_confirmation_ctx(SID, "write it", {})
        _gate("write_file", {"path": "brief.md"}, ctx)
        yes = agent.prepare_confirmation_ctx(SID, "yes", {})
        assert _allowed(_gate("write_file", {"path": "brief.md"}, yes))
        again = _gate("write_file", {"path": "brief.md"}, yes)
        assert not _allowed(again), "one yes authorised two writes"

    def test_trivially_different_spellings_are_one_question(self):
        """`brief.md` and `./brief.md` are the same file.

        If they fingerprinted differently, every restatement would read as a
        new question and the loop would come back wearing a different hat.
        """
        a = agent._action_fingerprint("write_file", {"path": "brief.md"})
        b = agent._action_fingerprint("write_file", {"path": "./brief.md"})
        assert a == b

    def test_genuinely_different_paths_are_different_questions(self):
        """And `~/Friday Creations/brief.md` is NOT the same file as
        `brief.md`, even though the loop made them look interchangeable. The
        user approved the one they were shown."""
        a = agent._action_fingerprint("write_file", {"path": "brief.md"})
        b = agent._action_fingerprint(
            "write_file", {"path": "~/Friday Creations/brief.md"})
        assert a != b


# ═══════════════════════════════════════════════════════════════════════════
#  3. TWO SURFACES, ONE DAY  — Stephen's concurrency question
# ═══════════════════════════════════════════════════════════════════════════

class TestTwoSurfacesOneDay:
    """He had the front page open while chatting, and asked whether that
    mattered. It did: `_current_session_id()` is the calendar date, so both
    surfaces share one pending-confirmation bucket."""

    def test_the_session_id_really_is_shared(self):
        from agent_friday.services.model_router import _current_session_id
        import datetime as _dt
        assert _current_session_id() == _dt.datetime.now().strftime("%Y-%m-%d"), (
            "this suite's premise - that all surfaces share one id - no longer "
            "holds; the isolation tests below need rewriting, not deleting")

    def test_one_surface_does_not_clobber_the_others_pending_action(self):
        chat = agent.prepare_confirmation_ctx(SID, "write me the brief", {})
        _gate("write_file", {"path": "brief.md"}, chat)
        front = agent.prepare_confirmation_ctx(SID, "switch to code", {})
        _gate("navigate", {"workspace": "code"}, front)

        assert len(agent._PENDING_CONFIRMATIONS.get(SID) or {}) == 2, (
            "the second surface overwrote the first surface's pending action")

    def test_a_yes_on_one_surface_does_not_grant_the_others_action(self):
        """The leak, stated as the user would experience it: he answers the
        front page and the chat tab quietly writes a file."""
        chat = agent.prepare_confirmation_ctx(SID, "write me the brief", {})
        _gate("write_file", {"path": "brief.md"}, chat)
        front = agent.prepare_confirmation_ctx(SID, "switch to code", {})
        _gate("navigate", {"workspace": "code"}, front)

        yes = agent.prepare_confirmation_ctx(SID, "yes", {})   # meant: navigate
        assert not _allowed(_gate("write_file", {"path": "brief.md"}, yes)), (
            "a yes intended for the front page's navigate authorised the chat "
            "tab's file write")
        assert _allowed(_gate("navigate", {"workspace": "code"}, yes)), (
            "the action the user actually approved was refused")

    def test_the_newest_question_wins_when_both_were_asked_in_one_clock_tick(
            self, monkeypatch):
        """Windows' wall clock advances in ~15 ms steps, so two questions asked
        back to back can carry the same timestamp. Ordering by that timestamp
        then picks the OLDER question on a tie, and the yes meant for the
        front page's navigate authorises the chat tab's file write."""
        import time as _t
        monkeypatch.setattr(_t, "time", lambda: 1758500000.0)
        chat = agent.prepare_confirmation_ctx(SID, "write me the brief", {})
        _gate("write_file", {"path": "brief.md"}, chat)
        front = agent.prepare_confirmation_ctx(SID, "switch to code", {})
        _gate("navigate", {"workspace": "code"}, front)

        yes = agent.prepare_confirmation_ctx(SID, "yes", {})   # meant: navigate
        assert yes.get("confirm_granted_tool") == "navigate"
        assert not _allowed(_gate("write_file", {"path": "brief.md"}, yes)), (
            "a yes intended for the front page's navigate authorised the chat "
            "tab's file write")
        assert _allowed(_gate("navigate", {"workspace": "code"}, yes))


# ═══════════════════════════════════════════════════════════════════════════
#  4. THE THINGS THAT MUST NOT HAVE CHANGED
# ═══════════════════════════════════════════════════════════════════════════

class TestNothingElseMoved:
    """A gate that got looser while being fixed would be a worse bug than the
    one it fixed."""

    def test_the_first_call_still_asks_and_does_not_execute(self):
        ctx = agent.prepare_confirmation_ctx(SID, "write it", {})
        v = _gate("write_file", {"path": "brief.md"}, ctx)
        assert not _allowed(v)
        assert _text(v).startswith("[CONFIRMATION REQUIRED]")

    def test_an_unanswered_action_never_runs_on_its_own(self):
        """Escalation is not approval. The twice-asked action must still be
        refused until a human decides it."""
        for msg in ["write it", "what?", "hmm", "anything", "still there?"]:
            ctx = agent.prepare_confirmation_ctx(SID, msg, {})
            assert not _allowed(_gate("write_file", {"path": "brief.md"}, ctx)), (
                "the action ran without anyone approving it")

    def test_background_and_scheduled_calls_still_bypass(self):
        for key in ("is_background_task", "scheduled", "confirm_bypass"):
            agent._PENDING_CONFIRMATIONS.clear()
            assert _allowed(_gate("write_file", {"path": "b.md"},
                                  {key: True, "session_id": "bg"}))

    def test_a_call_with_no_session_is_still_ungated(self):
        assert _allowed(_gate("write_file", {"path": "b.md"},
                              {"authenticated": True}))

    def test_a_read_only_tool_is_still_never_gated(self):
        ctx = agent.prepare_confirmation_ctx(SID, "anything", {})
        assert _allowed(_gate("query_calendar", {}, ctx))

    def test_a_write_into_the_creations_folder_is_still_preapproved(self):
        """That carve-out predates this fix and is not part of it."""
        from agent_friday import core as _core
        root = getattr(_core, "CREATIONS_DIR", None)
        if not root:
            pytest.skip("no CREATIONS_DIR on this install")
        ctx = agent.prepare_confirmation_ctx(SID, "save it", {})
        assert _allowed(_gate("write_file",
                              {"path": str(pathlib.Path(root) / "x.md")}, ctx))

    def test_a_retry_inside_one_turn_does_not_burn_the_ask_budget(self):
        """The budget counts UNANSWERED USER TURNS, not model retries.

        A model that calls the same tool twice in one breath is being wrong on
        its own; that must not consume the user's one chance to answer.
        """
        ctx = agent.prepare_confirmation_ctx(SID, "write it", {})
        for _ in range(4):
            v = _gate("write_file", {"path": "brief.md"}, ctx)
            assert _text(v).startswith("[CONFIRMATION REQUIRED]"), (
                "an intra-turn retry escalated: %s" % _text(v)[:80])


# ═══════════════════════════════════════════════════════════════════════════
#  5. THROUGH THE REAL DISPATCH PATH
# ═══════════════════════════════════════════════════════════════════════════

class TestThroughExecuteTool:
    """Everything above drives `_hook_confirmation_gate`. That is the unit
    that was wrong, but it is not what Stephen touched.

    `_execute_tool` is the entry the model's tool call actually arrives at,
    with the hook chain, the governance rings and the dispatch behind it. A
    fix that worked in the hook and not here would be worth nothing, and the
    difference is invisible from the tests above.

    The filesystem sandbox is stood down for these, and ONLY these. It is a
    separate guard with its own tests, it correctly refuses a write into
    pytest's tmp_path, and leaving it armed here would mean every assertion
    below passed for the wrong reason - the file would be absent because the
    sandbox stopped it, not because confirmation did.
    """

    @pytest.fixture(autouse=True)
    def _allow_tmp_writes(self, monkeypatch):
        monkeypatch.setattr(agent, "_sandbox_policy",
                            lambda name, inp: (True, ""))

    def test_stephens_transcript_ends_with_the_file_on_disk(self, tmp_path):
        target = tmp_path / "bold-panel-prep.md"
        replies = []
        for msg in TestTheLoopItself.TRANSCRIPT:
            ctx = agent.prepare_confirmation_ctx(SID, msg, {"authenticated": True})
            replies.append(agent._execute_tool(
                "write_file",
                {"path": str(target), "content": "# Bold panel prep\n"},
                session_ctx=ctx))

        assert target.exists(), (
            "five turns, two plain approvals, and the file still does not "
            "exist on disk:\n  " + "\n  ".join(r[:70] for r in replies))

    def test_the_question_is_not_repeated_through_dispatch(self, tmp_path):
        target = tmp_path / "x.md"
        asked = 0
        for msg in ["write it", "what?", "huh?", "???"]:
            ctx = agent.prepare_confirmation_ctx(SID, msg, {"authenticated": True})
            out = agent._execute_tool(
                "write_file", {"path": str(target), "content": "x"},
                session_ctx=ctx)
            if out.startswith("[CONFIRMATION REQUIRED]"):
                asked += 1
        assert asked <= 1, "the identical question reached the model %d times" % asked
        assert not target.exists(), "an unapproved write reached the disk"

    def test_an_unapproved_write_never_reaches_the_disk(self, tmp_path):
        target = tmp_path / "never.md"
        ctx = agent.prepare_confirmation_ctx(SID, "write it", {"authenticated": True})
        agent._execute_tool("write_file", {"path": str(target), "content": "x"},
                            session_ctx=ctx)
        assert not target.exists()

    def test_a_yes_for_one_path_does_not_write_another_through_dispatch(self, tmp_path):
        approved = tmp_path / "approved.md"
        sneaky = tmp_path / "sneaky.md"
        ctx = agent.prepare_confirmation_ctx(SID, "write it", {"authenticated": True})
        agent._execute_tool("write_file", {"path": str(approved), "content": "a"},
                            session_ctx=ctx)
        yes = agent.prepare_confirmation_ctx(SID, "yes", {"authenticated": True})
        agent._execute_tool("write_file", {"path": str(sneaky), "content": "b"},
                            session_ctx=yes)
        assert not sneaky.exists(), (
            "a yes for %s wrote %s" % (approved.name, sneaky.name))

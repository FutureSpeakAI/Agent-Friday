"""A claimed action is checked against the receipts for THAT action.

Spec 2.5. The mutation check used to fire only when NOTHING ran in a turn.
That is a real signal and a narrow one: a turn that searched the wiki and then
announced "I've sent the email" had a receipt, so it passed. The receipt was
for the wrong thing, and nothing looked.

These tests pin both directions - the guard has to catch the wrong-tool case,
and it has to stay quiet everywhere the evidence is merely suggestive, because
a checker that cries wolf gets muted and then every honest warning it ever
printed is worth nothing.
"""
from __future__ import annotations

import pytest

from agent_friday.services import tool_receipts as tr


@pytest.fixture(autouse=True)
def _fresh_turn():
    tr.begin_turn()
    yield
    tr.begin_turn()


def _kinds(text):
    return [c["kind"] for c in tr.unsupported_actions(text)]


# ── The case the old rule missed ────────────────────────────────────────────

def test_a_send_claim_is_caught_when_only_a_search_ran():
    """THE CRUX. Under the old rule this passed: something ran, so the check
    stood down. Nothing that ran could have sent anything."""
    tr.record("search_wiki", ok=True)
    claims = tr.unsupported_actions("I've sent the email to Janet.")
    assert [c["kind"] for c in claims] == ["action"]
    assert "search_wiki" in claims[0]["reason"], \
        "the reason should name what DID run, or the user cannot judge it"


def test_a_delete_claim_is_caught_when_only_a_read_ran():
    tr.record("read_wiki", ok=True)
    assert _kinds("I've removed it from your task list.") == ["action"]


def test_a_schedule_claim_is_caught_when_only_an_email_search_ran():
    tr.record("search_email", ok=True)
    assert _kinds("I'm booking that for Tuesday.") == ["action"]


# ── Staying quiet where it should ───────────────────────────────────────────

def test_a_send_claim_is_accepted_when_a_send_tool_ran():
    tr.record("draft_email", ok=True)
    assert _kinds("I've sent the email to Janet.") == []


def test_a_delete_claim_is_accepted_when_a_delete_tool_ran():
    tr.record("trash_message", ok=True)
    assert _kinds("I've removed it.") == []


def test_a_write_claim_is_accepted_when_a_wiki_write_ran():
    tr.record("propose_wiki_update", ok=True)
    assert _kinds("I've updated the note.") == []


def test_future_intent_is_never_a_claim():
    """'I'll stop using em dashes' is not an assertion that anything ran, and
    flagging it once taught the user to scroll past this banner."""
    assert _kinds("I'll send that over shortly.") == []
    assert _kinds("I can remove it if you want.") == []
    assert _kinds("Shall I delete it?") == []


def test_an_unfamiliar_verb_falls_back_to_the_old_rule():
    """A verb with no family must not be guessed at.

    `ran` and `took` are the only verbs `_ACTION_CLAIM_RE` captures that map
    to no tool family - there is no 'ran' tool - so they keep the older,
    narrower rule: complain only if nothing at all ran.

    The sentence below is dialectal English ("I've ran"), and that is not an
    accident of the test: the pattern requires a present-perfect lead-in
    ("I am / I'm / I've / I have / let me"), so a past-simple verb is only
    reachable through a construction most people would not write. Worth
    recording rather than hiding, because it means the fallback branch is
    nearly unreachable in practice - the families cover everything else.
    """
    assert tr._family_for("ran") is None
    assert tr._family_for("took") is None

    tr.record("search_wiki", ok=True)
    assert _kinds("I've ran the numbers again.") == [], \
        "something ran, and this verb names no family to check against"
    tr.begin_turn()
    assert _kinds("I've ran the numbers again.") == ["action"], \
        "nothing ran at all, which is the fallback rule's one job"


# ── The guard on the guard ──────────────────────────────────────────────────

def test_the_family_map_only_matches_real_stems():
    """A family whose stems match nothing would make the check silently
    permissive: every claim would fall to the fallback rule and the new
    behaviour would be inert while these tests still pass."""
    for stems, tools in tr._ACTION_FAMILIES:
        assert stems and tools
        for s in stems:
            assert tr._family_for(s) is not None, \
                "%r is listed as a stem but maps to no family" % s


def test_every_family_is_reachable_from_the_claim_pattern():
    """The families are only useful for verbs `_ACTION_CLAIM_RE` actually
    extracts. A family keyed on a verb the pattern never captures is dead
    code that looks like coverage."""
    import re
    for stems, _tools in tr._ACTION_FAMILIES:
        hit = False
        for s in stems:
            m = tr._ACTION_CLAIM_RE.search("I've %sed it." % s.rstrip("e"))
            if m and tr._family_for((m.group(1) or "")):
                hit = True
                break
        assert hit, "no verb in %r is reachable from _ACTION_CLAIM_RE" % (stems,)
        assert re  # keep the import honest


def test_the_checker_never_raises_on_odd_input():
    """A checker that can crash the reply it is checking is worse than none."""
    for bad in (None, "", 12345, object(), "\x00\x01", "I've sent " * 5000):
        try:
            tr.unsupported_actions(bad)
        except Exception as e:      # pragma: no cover - this is the assertion
            pytest.fail("unsupported_actions raised on %r: %s" % (type(bad), e))

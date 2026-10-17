"""A completed-write claim needs a successful write receipt of its own kind.

The reply guard is the A7 completion-receipt law (services/completion_receipts)
run by the integrity ladder (model_router.validate_toolcall_integrity) on
every chat reply. "I've added it to your calendar", "It's now on your
calendar", "Saved to your wiki", "I sent the email" each need a SUCCESSFUL
call this turn from a tool that can do that kind of write. A read does not
back a write, and a denied, held or failed call is not success. An unbacked
claim sentence is rewritten to say plainly it was not done; everything else in
the reply stays.

Half of this file is the other direction: negations, offers, future tense,
questions, the user's own words quoted back and calendar READS must never be
touched, because a guard that cries wolf is muted.
"""
from __future__ import annotations

import pytest

from agent_friday.services import tool_receipts as tr
from agent_friday.services.completion_receipts import (
    find_unreceipted_completion_claims as claims_in, receipt_ok)
from agent_friday.services.model_router import (
    TOOLCALL_FABRICATION_FAILURE_MESSAGE, validate_toolcall_integrity)

TOOLS = ["create_calendar_event", "query_calendar", "write_file", "draft_email",
         "propose_wiki_update", "search_email", "create_task"]


def rec(name, result="ok", **inp):
    return {"name": name, "input": inp, "result": result}


READ_ONLY = [rec("query_calendar", "No events this weekend.")]
CREATED = [rec("create_calendar_event", "Created event 'Picnic' (id evt_1).", title="Picnic")]
VAULT_DENIED = [rec("query_calendar", "No events."),
                rec("create_calendar_event", "[VAULT-ZT DENY] cloud_denied_tier_TIER_2",
                    title="Picnic at Riverside Park")]
HELD = [rec("create_calendar_event", "[CONFIRMATION REQUIRED] needs the user's approval",
            title="Picnic")]
ESCALATED = [rec("create_calendar_event", "[CONFIRMATION ESCALATED] was NOT executed",
                 title="Picnic")]
CARD = [rec("create_calendar_event", "[APPROVAL CARD RAISED] waiting", title="Picnic")]

PLAN = ("Saturday: a picnic at Riverside Park in the morning. Sunday: the zoo, "
        "then dinner out. ")


def guard(reply, trace):
    """The reply as the ladder leaves it (no retry available)."""
    out, _trace, meta = validate_toolcall_integrity(reply, trace, TOOLS)
    return out, meta


# ── completed calendar writes: first person and passive ─────────────────────

@pytest.mark.parametrize("claim", [
    "I've added all three events to your calendar.",
    "I added Saturday's picnic, Sunday's hike, and the zoo trip to your calendar.",
    "I just put the picnic on your calendar.",
    "All three events are now on your calendar.",
    "They have been added to your calendar for the weekend.",
    "Added to your calendar: the picnic and the zoo.",
    "Your calendar is all set.",
])
def test_a_calendar_claim_backed_only_by_a_read_is_caught(claim):
    """THE INCIDENT SHAPE. query_calendar contains "calendar"; it used to
    receipt "I've added it to your calendar" by name alone, and no pattern
    could see the passive forms at all."""
    assert claims_in(claim, READ_ONLY)


@pytest.mark.parametrize("trace", [VAULT_DENIED, HELD, ESCALATED, CARD,
                                   [rec("create_calendar_event", "Tool error (x): 503")]])
def test_a_denied_held_or_failed_create_is_not_success(trace):
    assert claims_in("All three events are now on your calendar.", trace)
    assert not receipt_ok(trace[-1])


def test_a_successful_create_backs_the_claim():
    assert claims_in("I've added the picnic to your calendar.", CREATED) == []
    assert claims_in("It's now on your calendar.", CREATED) == []


# ── the other kinds ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("reply", [
    "Saved to your wiki under Family.",
    "I've saved the bug report to your documents folder.",
    "I've made a note of that in your notes.",
    "I sent the email to the organiser.",
    "It has been sent.",
    "I've drafted the reply and saved it to your Drafts folder.",
    "I've scheduled a reminder for Friday morning.",
    "I've invited everyone to the picnic.",
    "I've removed the old reminders from your task list.",
])
def test_each_kind_needs_a_write_of_that_kind(reply):
    assert claims_in(reply, READ_ONLY + [rec("search_email", "3 results")])


def test_the_matching_write_backs_each_kind():
    assert claims_in("Saved to your wiki under Family.", [rec("propose_wiki_update")]) == []
    assert claims_in("I sent the email to the organiser.", [rec("draft_email", "Sent.")]) == []
    assert claims_in("I've scheduled a reminder for Friday.", [rec("create_task")]) == []


def test_a_search_does_not_back_a_send():
    assert claims_in("I've sent the email to the organiser.", [rec("search_email", "3 results")])


# ── reads: "your calendar is empty" needs a calendar read ───────────────────

@pytest.mark.parametrize("reply", [
    "Your calendar is empty this weekend.",
    "Your weekend calendar is completely clear.",
    "There's nothing on your calendar on Saturday.",
    "You have nothing scheduled on Sunday.",
])
def test_an_empty_calendar_claim_needs_a_read(reply):
    assert claims_in(reply, [])
    assert claims_in(reply, READ_ONLY) == []


# ── tricky negatives: never touched ─────────────────────────────────────────

@pytest.mark.parametrize("reply", [
    # negations
    "None of the three events got added.",
    "I haven't added anything to your calendar yet.",
    "I have not added these to your calendar.",
    "I tried to add them, but the calendar write was denied.",
    "Nothing was saved to your wiki.",
    # future tense, offers
    "I'll add them to your calendar once you confirm.",
    "I can add it to your calendar if you like.",
    "Let me know and I'll add these to your calendar.",
    # questions
    "Want me to add these to your calendar?",
    "Shall I save this to your wiki?",
    # the user's own words quoted back
    'You said "add it to my calendar", so here is the plan.',
    'You wrote: "I\'ve added the events to my calendar."',
    "> I've added it to my calendar already",
    # calendar reads
    "Your meeting is scheduled for 3pm.",
    "Your dentist appointment is on your calendar for Tuesday.",
    "Your calendar isn't empty on Saturday.",
    # inline delivery
    "I've drafted a reply below for you to review.",
    # code
    "Example:\n```\nI've added it to your calendar.\n```",
    # the guard's own corrections
    "I have not added this to your calendar yet — no calendar write succeeded this turn.",
    "I have not checked your calendar this turn, so I can't confirm it is empty.",
    "The calendar write is waiting for your confirmation.",
])
def test_never_touched(reply):
    assert claims_in(reply, []) == []
    out, meta = guard(PLAN + "\n" + reply, [])
    assert out == PLAN + "\n" + reply and meta["blocked"] is False


# ── the rewrite ─────────────────────────────────────────────────────────────

def test_the_claim_sentence_is_rewritten_and_the_plan_stays():
    reply = ("Here is the plan:\n\n- Saturday: picnic at Riverside Park\n- Sunday: the zoo\n\n"
             "I've added all three events to your calendar. They're now on your calendar "
             "for the weekend. Enjoy!")
    out, meta = guard(reply, VAULT_DENIED)
    assert out != TOOLCALL_FABRICATION_FAILURE_MESSAGE
    assert "- Saturday: picnic at Riverside Park\n- Sunday: the zoo" in out
    assert "I've added" not in out and "now on your calendar" not in out
    assert out.count("I have not added this to your calendar yet — the calendar "
                     "write was denied this turn.") == 1, "the same correction is said once"
    assert out.endswith("Enjoy!")


@pytest.mark.parametrize("trace,why", [
    ([], "no calendar write succeeded this turn"),
    (READ_ONLY, "no calendar write succeeded this turn"),
    (VAULT_DENIED, "the calendar write was denied this turn"),
    (HELD, "the calendar write is waiting for your confirmation"),
    (CARD, "the calendar write is waiting for your confirmation"),
])
def test_the_rewrite_names_why(trace, why):
    out, _ = guard(PLAN + "I've added both to your calendar.", trace)
    assert out == PLAN + "I have not added this to your calendar yet — " + why + "."


def test_the_true_part_of_a_sentence_is_kept():
    out, _ = guard(PLAN + "Here's the plan for the weekend, and I've put it all on "
                   "your calendar.", [])
    assert out.endswith("Here's the plan for the weekend. I have not added this to "
                        "your calendar yet — no calendar write succeeded this turn.")


def test_a_bare_done_goes_with_the_false_claim():
    out, _ = guard(PLAN + "Done! Added to your calendar: the picnic.", [])
    assert "Done" not in out
    assert out.endswith("I have not added this to your calendar yet — no calendar "
                        "write succeeded this turn.")


def test_a_claim_in_a_table_row_is_flagged_not_rewritten():
    reply = (PLAN + "\n\n| Day | Plan |\n|---|---|\n"
             "| Sat | Picnic — added to your calendar |")
    out, _ = guard(reply, [])
    assert out.startswith(PLAN + "\n\n| Day | Plan |\n|---|---|\n"
                          "| Sat | Picnic — not done (see note below) |")
    assert out.endswith("**Not done:** I have not added this to your calendar yet — "
                        "no calendar write succeeded this turn.")


def test_an_empty_calendar_claim_without_a_read_is_rewritten():
    out, _ = guard(PLAN + "Your calendar is empty this weekend.", [])
    assert out == PLAN + ("I have not checked your calendar this turn, so I can't "
                          "confirm it is empty.")


def test_a_reply_that_is_only_a_false_claim_still_gets_the_honest_failure():
    out, _ = guard("I've added it to your calendar.", [])
    assert out == TOOLCALL_FABRICATION_FAILURE_MESSAGE


def test_a_retry_that_really_creates_backs_the_claim():
    def redispatch(_note):
        return PLAN + "I've added both to your calendar.", CREATED
    out, trace, _ = validate_toolcall_integrity(
        PLAN + "I've added both to your calendar.", READ_ONLY, TOOLS, redispatch=redispatch)
    assert out == PLAN + "I've added both to your calendar."
    assert trace == CREATED


# ── the unnamed-action check: a refused write ran, it did not succeed ───────

def test_a_refused_write_does_not_receipt_an_action_claim():
    tr.begin_turn()
    tr.record("create_calendar_event", ok=False, denied=True, detail="[VAULT-ZT DENY]")
    claims = tr.unsupported_actions("I've added the picnic.")
    assert [c["kind"] for c in claims] == ["action"]
    tr.begin_turn()
    tr.record("create_calendar_event", ok=True)
    assert tr.unsupported_actions("I've added the picnic.") == []
    tr.begin_turn()


def test_a_receipt_line_typed_by_the_model_is_removed():
    """Receipt lines are written by Friday from the tool trace and replayed in
    history; a model copying the pattern into its own reply would be forging
    one, so the reply never keeps it."""
    reply = ("[Tool receipts for this reply: create_calendar_event x3 ok]\n"
             "Here is the plan for the weekend.")
    out, _trace, _meta = validate_toolcall_integrity(reply, [], TOOLS)
    assert out == "Here is the plan for the weekend."

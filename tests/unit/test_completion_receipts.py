"""A7 — completion-receipt law (Incident 2, F1).

An assistant reply that asserts a COMPLETED side-effecting action (created /
wrote / saved / sent / scheduled / updated ...) with no matching successful
tool receipt in the same turn is fabrication: strip, corrective retry, then
honest failure — exactly like a pseudo-tool-call leak.

Red-test anchor: the live F1 transcript, verbatim — gemma4:latest claimed
"I created daily_context_check.md in your Wiki" and the file exists nowhere.
"""
from __future__ import annotations

from agent_friday.services.completion_receipts import (
    find_unreceipted_completion_claims,
    receipt_ok,
)
from agent_friday.services.model_router import (
    TOOLCALL_FABRICATION_FAILURE_MESSAGE,
    validate_toolcall_integrity,
)

TOOLS = ["write_file", "learn_skill", "send_email", "create_event",
         "query_calendar", "search_web", "write_wiki_page"]


class TestReceiptOk:
    def test_successful_receipt(self):
        assert receipt_ok({"name": "write_file", "input": {},
                           "result": "wrote 120 bytes to notes.md"})

    def test_vault_deny_is_not_success(self):
        assert not receipt_ok({"name": "write_file", "input": {},
                               "result": "[VAULT-ZT DENY] tier-2 content"})

    def test_tool_error_is_not_success(self):
        assert not receipt_ok({"name": "learn_skill", "input": {},
                               "result": "Tool error (learn_skill): boom"})

    def test_confirmation_hold_is_not_success(self):
        assert not receipt_ok({"name": "send_email", "input": {},
                               "result": "[CONFIRMATION REQUIRED] send to..."})

    def test_governance_and_sandbox_denies_are_not_success(self):
        assert not receipt_ok({"name": "write_file", "result": "[GOVERNANCE DENY] x"})
        assert not receipt_ok({"name": "write_file", "result": "[SANDBOX DENY] x"})

    def test_unknown_tool_is_not_success(self):
        assert not receipt_ok({"name": "wrte_file", "result": "Unknown tool: wrte_file"})


class TestClaimDetection:
    def test_f1_verbatim_no_tools_is_a_violation(self):
        # The live incident text, verbatim. Zero tools executed that turn.
        claims = find_unreceipted_completion_claims(
            "I created daily_context_check.md in your Wiki", [])
        assert claims, "F1 transcript must be detected as an unreceipted claim"

    def test_claim_with_matching_successful_receipt_passes(self):
        trace = [{"name": "write_wiki_page",
                  "input": {"title": "daily_context_check"},
                  "result": "page saved"}]
        assert find_unreceipted_completion_claims(
            "I created daily_context_check.md in your Wiki", trace) == []

    def test_claim_with_failed_receipt_is_still_a_violation(self):
        # The completion-honesty class from the battery: the write FAILED but
        # the model claims success anyway.
        trace = [{"name": "write_wiki_page",
                  "input": {"title": "daily_context_check"},
                  "result": "Tool error (write_wiki_page): disk full"}]
        assert find_unreceipted_completion_claims(
            "I created daily_context_check.md in your Wiki", trace)

    def test_sent_email_claim_without_receipt_is_a_violation(self):
        assert find_unreceipted_completion_claims(
            "Done — I've sent the email to your editor.", [])

    def test_scheduled_claim_without_receipt_is_a_violation(self):
        assert find_unreceipted_completion_claims(
            "I've scheduled the interview on your calendar for 3pm.", [])

    def test_plain_answer_is_not_a_claim(self):
        assert find_unreceipted_completion_claims(
            "Your next meeting is at 2pm with the editor.", []) == []

    def test_future_tense_offer_is_not_a_claim(self):
        assert find_unreceipted_completion_claims(
            "I can create a wiki page for that — want me to?", []) == []

    def test_inline_content_below_is_not_a_claim(self):
        # "I've created a draft below" delivers content in the reply itself —
        # no side effect asserted, no receipt required.
        assert find_unreceipted_completion_claims(
            "I've created a draft note below for you to review.", []) == []

    def test_code_fences_are_ignored(self):
        assert find_unreceipted_completion_claims(
            "Example log line:\n```\nI created backup.md in your Wiki\n```", []) == []


class TestValidatorIntegration:
    def test_f1_verbatim_with_no_redispatch_becomes_honest_failure(self):
        reply, trace, meta = validate_toolcall_integrity(
            "I created daily_context_check.md in your Wiki", [], TOOLS,
            redispatch=None)
        assert reply == TOOLCALL_FABRICATION_FAILURE_MESSAGE
        assert meta["blocked"] is True
        assert trace == []

    def test_f1_corrective_retry_can_recover(self):
        def redispatch(note):
            return ("I wasn't able to create that wiki page — my file tools "
                    "didn't run this turn. Want me to try again?"), []

        reply, trace, meta = validate_toolcall_integrity(
            "I created daily_context_check.md in your Wiki", [], TOOLS,
            redispatch=redispatch)
        assert "wasn't able" in reply
        assert meta["blocked"] is True

    def test_corrective_note_describes_the_unreceipted_claim(self):
        notes = []

        def redispatch(note):
            notes.append(note)
            return "Honest plain answer.", []

        validate_toolcall_integrity(
            "I created daily_context_check.md in your Wiki", [], TOOLS,
            redispatch=redispatch)
        assert notes and "claim" in notes[0].lower()

    def test_claim_backed_by_successful_receipt_passes_through(self):
        trace = [{"name": "write_wiki_page", "input": {}, "result": "saved"}]
        reply, out_trace, meta = validate_toolcall_integrity(
            "I created daily_context_check.md in your Wiki", trace, TOOLS)
        assert reply == "I created daily_context_check.md in your Wiki"
        assert meta["blocked"] is False
        assert out_trace == trace

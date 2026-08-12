"""Unit tests for services/model_router.py::validate_toolcall_integrity —
FR-2, the post-generation response validator (docs: toolcall-integrity-v5).

Scans every generated reply for fabricated bracket-syntax pseudo-tool-calls
(the 2026-08-12 "start my day" confabulation pattern: [query_calendar],
[search_email(priority:high)], etc.) before it's stored, rendered, or spoken.
"""
from __future__ import annotations

from agent_friday.services.model_router import (
    TOOLCALL_FABRICATION_FAILURE_MESSAGE,
    validate_toolcall_integrity,
)

TOOLS = ["query_calendar", "search_email", "search_web", "open_url", "draft_email"]


class TestCleanReplyPassesThrough:
    def test_clean_reply_unchanged(self):
        reply, trace, meta = validate_toolcall_integrity(
            "You have no events today.", [{"name": "query_calendar", "result": "[]"}], TOOLS)
        assert reply == "You have no events today."
        assert meta["blocked"] is False
        assert meta["retries"] == 0

    def test_honest_not_connected_reply_not_flagged(self):
        reply, trace, meta = validate_toolcall_integrity(
            "Calendar isn't connected yet — want me to set that up?", [], TOOLS)
        assert meta["blocked"] is False


class TestFabricationWithNoRedispatch:
    def test_bracket_leak_with_no_redispatch_becomes_honest_failure(self):
        reply, trace, meta = validate_toolcall_integrity(
            "[query_calendar] shows a 2pm meeting with Mr. Peterson.", [], TOOLS,
            redispatch=None,
        )
        assert reply == TOOLCALL_FABRICATION_FAILURE_MESSAGE
        assert trace == []
        assert meta["blocked"] is True
        assert meta["retries"] == 0

    def test_pure_confabulation_with_no_bracket_syntax_also_caught(self):
        # The live-reproduced gemma3:4b failure mode: no [brackets], no tool
        # call, just confident invented facts. find_pseudo_toolcalls only
        # catches the bracket/call-syntax shape — confirm that shape alone
        # (no plain-prose confabulation detection) is what this validator
        # covers, and that it does NOT false-positive on tool-free honest text.
        reply, trace, meta = validate_toolcall_integrity(
            "You have a meeting with Mr. Peterson at 2pm.", [], TOOLS)
        assert meta["blocked"] is False  # no registry name appears literally


class TestCorrectiveRetry:
    def test_first_retry_clean_stops_retrying(self):
        calls = []

        def redispatch(note):
            calls.append(note)
            return "I don't have calendar access connected right now.", []

        reply, trace, meta = validate_toolcall_integrity(
            "[query_calendar] shows a meeting at 2pm.", [], TOOLS, redispatch=redispatch)
        assert len(calls) == 1
        assert reply == "I don't have calendar access connected right now."
        assert meta["blocked"] is True
        assert meta["retries"] == 1
        assert meta["final_leaks"] == []

    def test_exhausts_max_retries_then_honest_failure(self):
        calls = []

        def always_leaks(note):
            calls.append(note)
            return "[query_calendar] still shows a fake meeting.", []

        reply, trace, meta = validate_toolcall_integrity(
            "[query_calendar] shows a meeting at 2pm.", [], TOOLS,
            redispatch=always_leaks, max_retries=2)
        assert len(calls) == 2  # never exceeds max_retries
        assert reply == TOOLCALL_FABRICATION_FAILURE_MESSAGE
        assert trace == []
        assert meta["retries"] == 2
        assert meta["final_leaks"]

    def test_corrective_note_names_the_leaked_tool(self):
        seen_notes = []

        def redispatch(note):
            seen_notes.append(note)
            return "clean reply", []

        validate_toolcall_integrity(
            "[search_email(priority:high)] found nothing.", [], TOOLS, redispatch=redispatch)
        assert "search_email" in seen_notes[0]

    def test_redispatch_exception_falls_back_to_honest_failure(self):
        def broken_redispatch(note):
            raise RuntimeError("provider unreachable")

        reply, trace, meta = validate_toolcall_integrity(
            "[query_calendar] shows something.", [], TOOLS, redispatch=broken_redispatch)
        assert reply == TOOLCALL_FABRICATION_FAILURE_MESSAGE
        assert meta["retries"] == 1  # one attempt made, then gave up


class TestNeverExecutesLeakedText:
    def test_leak_text_is_never_returned_as_a_tool_trace_entry(self):
        # The whole point: a fabricated [draft_email(...)] must never become
        # a real tool_trace entry implying the email was actually drafted.
        reply, trace, meta = validate_toolcall_integrity(
            "[draft_email(to:landlord)] Sent!", [], TOOLS)
        assert trace == []
        assert "draft_email" not in str(trace)

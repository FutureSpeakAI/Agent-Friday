"""B7 — honest-failure UX (Incident 2, F4 dead-air).

Old behavior: max_retries=2 → up to 3 full agentic dispatches (~90s observed
on gemma4:latest) before the user saw anything. New contract:

1. ONE corrective retry (same dispatch path).
2. Then ONE auto-retry with tools stripped (`redispatch_no_tools`) so the
   model can answer plainly — the validator still applies to its output.
3. Only then the honest-failure message, which invites a rephrase.
"""
from __future__ import annotations

from agent_friday.services.model_router import (
    TOOLCALL_FABRICATION_FAILURE_MESSAGE,
    validate_toolcall_integrity,
)

TOOLS = ["query_calendar", "search_email"]

LEAKY = "[query_calendar] shows a fake meeting."


class TestRetryCap:
    def test_default_is_one_corrective_retry(self):
        calls = []

        def always_leaks(note):
            calls.append(note)
            return LEAKY, []

        validate_toolcall_integrity(LEAKY, [], TOOLS, redispatch=always_leaks)
        assert len(calls) == 1, "default corrective retry budget must be 1"


class TestToolsStrippedFallback:
    def test_no_tools_fallback_used_after_corrective_retry_fails(self):
        def always_leaks(note):
            return LEAKY, []

        def no_tools(note):
            return "I can't check the calendar right now — it may not be connected.", []

        reply, trace, meta = validate_toolcall_integrity(
            LEAKY, [], TOOLS, redispatch=always_leaks,
            redispatch_no_tools=no_tools)
        assert "can't check the calendar" in reply
        assert meta["tools_stripped_retry"] is True
        assert meta["final_leaks"] == []

    def test_no_tools_output_still_validated(self):
        # A leak in the tools-stripped answer must not pass either.
        def always_leaks(note):
            return LEAKY, []

        reply, trace, meta = validate_toolcall_integrity(
            LEAKY, [], TOOLS, redispatch=always_leaks,
            redispatch_no_tools=always_leaks)
        assert reply == TOOLCALL_FABRICATION_FAILURE_MESSAGE
        assert trace == []

    def test_no_tools_fallback_skipped_when_first_retry_recovers(self):
        stripped_calls = []

        def recovers(note):
            return "Calendar isn't connected yet.", []

        def no_tools(note):
            stripped_calls.append(note)
            return "should never run", []

        reply, _, meta = validate_toolcall_integrity(
            LEAKY, [], TOOLS, redispatch=recovers, redispatch_no_tools=no_tools)
        assert stripped_calls == []
        assert meta.get("tools_stripped_retry") is False
        assert "isn't connected" in reply


class TestHonestFailureMessage:
    def test_failure_message_invites_a_rephrase(self):
        assert "rephras" in TOOLCALL_FABRICATION_FAILURE_MESSAGE.lower()

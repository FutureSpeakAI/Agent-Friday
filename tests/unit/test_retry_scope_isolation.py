"""B5 — retry-scope isolation (Incident 2, F5).

The FR-2 corrective injection is validator plumbing, not conversation. It
must never surface to the user: the retry seat tends to apologize for or
quote the unexplained extra turn (the live "[user correction]" leak), and
that apology was persisted into visible history and replayed as context.

Contract: retry outputs are scrubbed of correction-referencing artifacts,
the corrective note is explicitly marked as an automated non-user turn, and
neither the note nor any rejected draft reaches the returned reply.
"""
from __future__ import annotations

from agent_friday.services.tool_integrity import scrub_retry_artifacts
from agent_friday.services.model_router import validate_toolcall_integrity

TOOLS = ["query_calendar", "search_email"]


class TestScrubber:
    def test_user_correction_tag_stripped(self):
        # The verbatim F5 artifact shape from the live 10:41-10:58 chat.
        out = scrub_retry_artifacts(
            "[user correction] Apologies for the earlier confusion. "
            "You have no calendar connected yet.")
        assert "[user correction]" not in out
        assert "You have no calendar connected yet." in out

    def test_apology_referencing_fabrication_stripped(self):
        out = scrub_retry_artifacts(
            "My apologies — my previous reply contained fabricated tool-call "
            "syntax. Your calendar isn't connected yet.")
        assert "fabricated" not in out
        assert "isn't connected" in out

    def test_clean_reply_untouched(self):
        text = "Your calendar isn't connected yet — want me to set it up?"
        assert scrub_retry_artifacts(text) == text

    def test_legit_apology_about_the_topic_untouched(self):
        # Apologies that are about the ANSWER, not about the correction
        # mechanism, must survive.
        text = "Sorry to be the bearer of bad news: the flight is delayed."
        assert scrub_retry_artifacts(text) == text


class TestValidatorRetryScope:
    def test_retry_reply_with_leak_artifact_is_scrubbed(self):
        def redispatch(note):
            return ("[user correction] Apologies for the earlier confusion — "
                    "your calendar isn't connected yet."), []

        reply, trace, meta = validate_toolcall_integrity(
            "[query_calendar] shows a 2pm meeting.", [], TOOLS,
            redispatch=redispatch)
        assert "[user correction]" not in reply
        assert "calendar isn't connected" in reply

    def test_corrective_note_is_marked_automated_not_user(self):
        notes = []

        def redispatch(note):
            notes.append(note)
            return "clean", []

        validate_toolcall_integrity(
            "[query_calendar] shows a meeting.", [], TOOLS, redispatch=redispatch)
        note = notes[0].lower()
        assert "automated" in note
        assert "not" in note and "user" in note

    def test_rejected_draft_text_never_in_final_reply(self):
        def redispatch(note):
            return "Plain honest answer.", []

        reply, _, _ = validate_toolcall_integrity(
            "[query_calendar] shows a meeting with Mr. Fake.", [], TOOLS,
            redispatch=redispatch)
        assert "Mr. Fake" not in reply

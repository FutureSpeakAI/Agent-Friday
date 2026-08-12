"""Unit tests for services/response_provenance.py — FR-3, only executed-
tool-result URLs render clickable (docs: toolcall-integrity-v5).

2026-08-12: the "start my day" confabulation minted a fake Google Calendar
URL that rendered as an indistinguishable, clickable [web:...] citation.
This module is the server-side ground truth: a citation is only left
clickable if the URL is backed by something an executed tool actually
touched or returned this turn.
"""
from __future__ import annotations

from agent_friday.services.response_provenance import (
    extract_executed_urls,
    mark_unverified_citations,
    warn_if_ungrounded_claim,
)


class TestExtractExecutedUrls:
    def test_pulls_url_from_tool_input(self):
        trace = [{"name": "open_url", "input": {"url": "https://reddit.com"}, "result": "opened"}]
        assert "https://reddit.com" in extract_executed_urls(trace)

    def test_pulls_urls_embedded_in_result_text(self):
        trace = [{"name": "search_web", "input": {"query": "ai news"},
                  "result": "Top hit: https://techcrunch.com/story-1 (via TechCrunch)"}]
        assert "https://techcrunch.com/story-1" in extract_executed_urls(trace)

    def test_strips_trailing_punctuation(self):
        trace = [{"name": "search_web", "result": "See https://example.com/page."}]
        urls = extract_executed_urls(trace)
        assert "https://example.com/page" in urls
        assert "https://example.com/page." not in urls

    def test_empty_trace_yields_empty_set(self):
        assert extract_executed_urls([]) == set()
        assert extract_executed_urls(None) == set()


class TestMarkUnverifiedCitations:
    def test_real_tool_backed_url_stays_a_web_citation(self):
        trace = [{"name": "open_url", "input": {"url": "https://reddit.com"}, "result": "ok"}]
        reply, flagged = mark_unverified_citations(
            "I opened it for you. [web:https://reddit.com]", trace)
        assert reply == "I opened it for you. [web:https://reddit.com]"
        assert flagged == []

    def test_model_minted_url_with_no_backing_tool_becomes_inert(self):
        # The live-reproduced failure shape: a fabricated calendar URL cited
        # as if it were a real web source.
        reply, flagged = mark_unverified_citations(
            "Added to your calendar: [web:https://calendar.fake/evt-12345]", [])
        assert "[unverified-web:https://calendar.fake/evt-12345]" in reply
        assert "[web:https://calendar.fake/evt-12345]" not in reply
        assert flagged == ["https://calendar.fake/evt-12345"]

    def test_mixed_reply_only_flags_the_unbacked_url(self):
        trace = [{"name": "search_web", "result": "https://real-source.com/article"}]
        reply, flagged = mark_unverified_citations(
            "Per [web:https://real-source.com/article] and also "
            "[web:https://invented.example/x], here's the summary.", trace)
        assert "[web:https://real-source.com/article]" in reply
        assert "[unverified-web:https://invented.example/x]" in reply
        assert flagged == ["https://invented.example/x"]

    def test_no_citations_is_a_noop(self):
        reply, flagged = mark_unverified_citations("Just a plain reply, no sources.", [])
        assert reply == "Just a plain reply, no sources."
        assert flagged == []

    def test_empty_reply(self):
        reply, flagged = mark_unverified_citations("", [])
        assert reply == ""
        assert flagged == []


class TestWarnIfUngroundedClaim:
    def test_warns_on_citation_with_zero_tools(self):
        assert warn_if_ungrounded_claim(
            "According to [news:reuters.com/2026-08-12/story], X happened.", []) is True

    def test_no_warn_when_tools_executed(self):
        trace = [{"name": "search_news", "result": "ok"}]
        assert warn_if_ungrounded_claim(
            "According to [news:reuters.com/2026-08-12/story], X happened.", trace) is False

    def test_no_warn_when_no_citation_present(self):
        assert warn_if_ungrounded_claim("Just chatting, no sources cited.", []) is False

    def test_wiki_and_memory_citations(self):
        assert warn_if_ungrounded_claim("Per [wiki:career/notes.md], ...", []) is True
        # memory/conversation citations reference PAST turns, not this turn's
        # tools — intentionally not flagged as ungrounded.
        assert warn_if_ungrounded_claim('Per [memory:2026-01-01/"quote"], ...', []) is False

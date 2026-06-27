"""Unit tests for the action-trust layer in services/agent.py.

Covers the two behaviours added for "Friday should never surprise the user":

  1. URL validation (_validate_url / _extract_youtube_id) — refuse malformed or
     hallucinated links (esp. YouTube ids) BEFORE open_url launches a dead page.
  2. The ask-first permission gate in _execute_tool — model-initiated user-facing
     actions (open_url / open_path / navigate / write_file) must be confirmed by
     the user before they run, while scheduled/background and non-interactive
     calls bypass it.

All tests pass check_reachable=False (or use the navigate tool, which has no side
effect) so nothing here touches the network or launches a browser.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.services.agent as agent


# ════════════════════════════════════════════════════════════════════════
#  URL validation
# ════════════════════════════════════════════════════════════════════════

class TestValidateUrl:
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",   # valid 11-char id
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/abcdefghijk",
        "https://www.youtube.com/results?search_query=cats",  # search page, no id
        "https://reuters.com/world/article",
        "https://localhost:5000/dashboard",
    ])
    def test_accepts_wellformed(self, url):
        ok, _why = agent._validate_url(url, check_reachable=False)
        assert ok is True

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=SHORT",          # id too short
        "https://youtu.be/nope",                          # malformed id
        "https://www.youtube.com/watch?v=toomanychars12",  # id too long
        "ftp://example.com/file",                          # wrong scheme
        "notaurl",                                         # no scheme/domain
        "https://",                                        # no domain
        "https://nodot",                                   # malformed domain
        "",                                                # empty
    ])
    def test_rejects_bad(self, url):
        ok, why = agent._validate_url(url, check_reachable=False)
        assert ok is False
        assert isinstance(why, str) and why

    def test_youtube_id_extraction(self):
        from urllib.parse import urlparse
        assert agent._extract_youtube_id(
            urlparse("https://youtu.be/dQw4w9WgXcQ")) == "dQw4w9WgXcQ"
        assert agent._extract_youtube_id(
            urlparse("https://www.youtube.com/watch?v=dQw4w9WgXcQ")) == "dQw4w9WgXcQ"
        # Non-YouTube and non-video pages yield '' (caller skips the id check).
        assert agent._extract_youtube_id(urlparse("https://reuters.com/x")) == ""
        assert agent._extract_youtube_id(
            urlparse("https://www.youtube.com/feed/subscriptions")) == ""

    def test_open_url_refuses_invalid(self):
        # The handler itself blocks a malformed YouTube link without opening it.
        res = agent._tool_open_url({"url": "https://www.youtube.com/watch?v=FAKE"})
        assert "did NOT open" in res
        assert "invalid" in res.lower()


# ════════════════════════════════════════════════════════════════════════
#  Affirmative / negative detection
# ════════════════════════════════════════════════════════════════════════

class TestAffirmativeDetection:
    @pytest.mark.parametrize("msg", [
        "yes", "yes please", "sure, go ahead", "ok do it", "yeah", "go for it",
        "please do", "confirm", "open it",
    ])
    def test_affirmative(self, msg):
        assert agent._is_affirmative(msg) is True

    @pytest.mark.parametrize("msg", [
        "what time is it", "tell me about reuters", "no", "not now",
    ])
    def test_not_affirmative(self, msg):
        assert agent._is_affirmative(msg) is False

    @pytest.mark.parametrize("msg", [
        "no", "no thanks", "nope", "cancel", "never mind", "stop", "don't",
    ])
    def test_negative(self, msg):
        assert agent._is_negative(msg) is True


# ════════════════════════════════════════════════════════════════════════
#  Permission gate
# ════════════════════════════════════════════════════════════════════════

class TestConfirmationGate:
    def setup_method(self):
        agent._PENDING_CONFIRMATIONS.clear()

    def test_first_call_asks_and_does_not_execute(self):
        ctx = agent.prepare_confirmation_ctx("s1", "show me the news",
                                             {"authenticated": True})
        res = agent._execute_tool("navigate", {"workspace": "news"}, session_ctx=ctx)
        assert res.startswith("[CONFIRMATION REQUIRED]")
        assert "s1" in agent._PENDING_CONFIRMATIONS

    def test_affirmative_next_turn_executes(self):
        # Turn 1: Friday asks.
        ctx1 = agent.prepare_confirmation_ctx("s2", "open the news",
                                              {"authenticated": True})
        agent._execute_tool("navigate", {"workspace": "news"}, session_ctx=ctx1)
        # Turn 2: user says yes → grant → tool runs (navigate has no side effect).
        ctx2 = agent.prepare_confirmation_ctx("s2", "yes please",
                                              {"authenticated": True})
        assert ctx2.get("confirm_granted") is True
        res = agent._execute_tool("navigate", {"workspace": "news"}, session_ctx=ctx2)
        assert not res.startswith("[CONFIRMATION REQUIRED]")
        assert res.startswith("NAV_OK")
        assert "s2" not in agent._PENDING_CONFIRMATIONS  # cleared after execution

    def test_negative_clears_pending(self):
        ctx = agent.prepare_confirmation_ctx("s3", "open the news",
                                             {"authenticated": True})
        agent._execute_tool("navigate", {"workspace": "news"}, session_ctx=ctx)
        assert "s3" in agent._PENDING_CONFIRMATIONS
        agent.prepare_confirmation_ctx("s3", "no nevermind", {"authenticated": True})
        assert "s3" not in agent._PENDING_CONFIRMATIONS

    def test_background_task_bypasses_gate(self):
        res = agent._execute_tool(
            "navigate", {"workspace": "news"},
            session_ctx={"is_background_task": True, "session_id": "bg"})
        assert res.startswith("NAV_OK")

    def test_scheduled_flag_bypasses_gate(self):
        res = agent._execute_tool(
            "navigate", {"workspace": "news"},
            session_ctx={"scheduled": True, "session_id": "cron"})
        assert res.startswith("NAV_OK")

    def test_non_interactive_call_is_not_gated(self):
        # No session_id at all (internal/legacy caller) → confirmation inactive.
        res = agent._execute_tool("navigate", {"workspace": "news"},
                                  session_ctx={"authenticated": True})
        assert res.startswith("NAV_OK")

    def test_non_action_tool_never_gated(self):
        # A read-only tool is unaffected even in an interactive session.
        ctx = agent.prepare_confirmation_ctx("s4", "anything", {"authenticated": True})
        res = agent._execute_tool("query_calendar", {}, session_ctx=ctx)
        assert not res.startswith("[CONFIRMATION REQUIRED]")

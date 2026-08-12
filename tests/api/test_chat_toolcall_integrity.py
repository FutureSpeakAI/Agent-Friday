"""API-level test for FR-2 wired into POST /api/chat (routes/chat.py) — the
post-generation response validator must catch fabricated bracket-syntax
pseudo-tool-calls, corrective-retry through the SAME dispatch path, and fall
back to an honest failure message when the fabrication survives every retry.
Must never render or persist the fabricated text.
"""
from __future__ import annotations

import agent_friday.routes.chat as chat_mod
from agent_friday.services.model_router import TOOLCALL_FABRICATION_FAILURE_MESSAGE


class TestChatIntegrityWiring:
    def test_clean_reply_passes_through_unmodified(self, client):
        # Default stub (CANNED_TEXT) never contains a registry tool name.
        resp = client.post("/api/chat", json={"message": "hello"})
        assert resp.status_code == 200
        assert "query_calendar" not in resp.get_json()["response"]

    def test_fabricated_bracket_reply_is_corrected_via_retry(self, client, monkeypatch):
        calls = {"n": 0}

        def fake_dispatch(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return "[query_calendar] shows a meeting with Mr. Peterson at 2pm.", []
            return "I don't have calendar access connected yet — want me to set it up?", []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", fake_dispatch)
        resp = client.post("/api/chat", json={"message": "what's on my calendar today?"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert calls["n"] == 2, "expected exactly one corrective retry"
        assert "query_calendar" not in data["response"]
        assert "Mr. Peterson" not in data["response"]
        assert "connected" in data["response"]
        # The corrected reply, not the fabrication, is what's persisted.
        hist = client.get("/api/chat/history").get_json()
        assert "Mr. Peterson" not in str(hist)

    def test_fabrication_surviving_all_retries_becomes_honest_failure(self, client, monkeypatch):
        def always_fabricates(messages, **kwargs):
            return "[query_calendar] shows a meeting with Mr. Peterson at 2pm.", []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", always_fabricates)
        resp = client.post("/api/chat", json={"message": "what's on my calendar today?"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["response"] == TOOLCALL_FABRICATION_FAILURE_MESSAGE
        assert data["tool_trace"] == []
        # Never persisted as if it were real.
        hist = client.get("/api/chat/history").get_json()
        assert "Mr. Peterson" not in str(hist)

    def test_no_fabricated_url_reaches_the_client_via_tool_trace(self, client, monkeypatch):
        def fabricates_a_url(messages, **kwargs):
            return ("Here's your event: [open_url(https://calendar.fake/evt-12345)]"
                    " — added it to your day."), []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", fabricates_a_url)
        resp = client.post("/api/chat", json={"message": "what's my next meeting"})
        data = resp.get_json()
        assert "calendar.fake" not in data["response"]
        assert data["tool_trace"] == []

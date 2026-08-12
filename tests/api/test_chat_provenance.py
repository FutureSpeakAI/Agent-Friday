"""API-level test for FR-3 wired into POST /api/chat — a [web:URL] citation
not backed by an executed tool result this turn must render inert
([unverified-web:URL]) rather than as a normal clickable link.
"""
from __future__ import annotations

import agent_friday.routes.chat as chat_mod


class TestChatProvenanceWiring:
    def test_tool_backed_url_stays_a_real_citation(self, client, monkeypatch):
        def dispatch(messages, **kwargs):
            return ("I opened it. [web:https://reddit.com]",
                    [{"name": "open_url", "input": {"url": "https://reddit.com"}, "result": "ok"}])

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        resp = client.post("/api/chat", json={"message": "open reddit"})
        data = resp.get_json()
        assert "[web:https://reddit.com]" in data["response"]
        assert "unverified-web" not in data["response"]

    def test_model_minted_url_renders_inert(self, client, monkeypatch):
        def dispatch(messages, **kwargs):
            return ("Added to your calendar: [web:https://calendar.fake/evt-12345]", [])

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        resp = client.post("/api/chat", json={"message": "what's on my calendar"})
        data = resp.get_json()
        assert "[unverified-web:https://calendar.fake/evt-12345]" in data["response"]
        assert "[web:https://calendar.fake/evt-12345]" not in data["response"]

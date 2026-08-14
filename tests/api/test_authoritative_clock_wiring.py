"""A6 wiring — the authoritative clock must actually reach the model.

Two acceptance points from the spec: injected now() is present in the
dispatched system prompt on every chat turn (TIER_1, so it survives vault
gating on cloud seats), and tool results carry code-computed weekdays.
"""
from __future__ import annotations

from datetime import datetime

import agent_friday.routes.chat as chat_mod
import agent_friday.services.agent as agent_mod


class TestClockInSystemPrompt:
    def test_chat_turn_system_prompt_contains_clock(self, client, monkeypatch):
        captured = {}

        def capture_dispatch(messages, system=None, **kwargs):
            captured["system"] = system or ""
            return "ok", []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", capture_dispatch)
        client.post("/api/chat", json={"message": "what day is tomorrow?"})
        system = captured.get("system", "")
        assert "== AUTHORITATIVE CLOCK ==" in system
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in system
        assert datetime.now().strftime("%A") in system
        assert "NEVER derive a weekday" in system


class TestToolResultsAnnotated:
    def test_execute_tool_result_dates_carry_weekdays(self, monkeypatch):
        monkeypatch.setitem(
            agent_mod.CLAUDE_TOOL_HANDLERS, "clock_probe_tool",
            lambda inp: "Next event: 2026-08-14 at 3pm")
        # Unknown tools default to ring 2 (network) and get governance-denied
        # without a session — this probe is a local read.
        monkeypatch.setitem(agent_mod.TOOL_RINGS, "clock_probe_tool", 0)
        result = agent_mod._execute_tool("clock_probe_tool", {})
        assert "2026-08-14 (Friday)" in result

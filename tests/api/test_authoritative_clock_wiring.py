"""A6 wiring — the authoritative clock must actually reach the model.

Two acceptance points from the spec: injected now() reaches the model on
every chat turn (TIER_1, so it survives vault gating on cloud seats), and
tool results carry code-computed weekdays. The clock is per-turn context: it
rides in the marked context block at the top of the newest user turn, which
says Friday supplied it and the user did not write it, on the cloud and on
the local seat alike.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import agent_friday.services.agent as agent_mod
from tests.api.turn_context_helpers import capture_requests, chat_requests, route_to, turn_context


class TestClockInTurnContext:
    @pytest.mark.parametrize("provider", ["cloud", "local"])
    def test_chat_turn_context_contains_clock(self, client, monkeypatch, patch_app, provider):
        route_to(monkeypatch, provider)
        seen = capture_requests(patch_app)
        said = "what day is tomorrow?"
        client.post("/api/chat", json={"message": said})
        (request, *_rest) = chat_requests(seen, "/api/chat")
        context = turn_context(request["messages"], said=said)
        assert "== AUTHORITATIVE CLOCK ==" in context
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in context
        assert datetime.now().strftime("%A") in context
        assert "NEVER derive a weekday" in context


class TestToolResultsAnnotated:
    def test_execute_tool_result_dates_carry_weekdays(self, monkeypatch):
        monkeypatch.setitem(
            agent_mod.CLAUDE_TOOL_HANDLERS, "clock_probe_tool",
            lambda inp: "Next event: 2026-08-14 at 3pm")
        # Unknown tools default to ring 2 (network) and get governance-denied
        # without a session — this probe is a local read.
        monkeypatch.setitem(agent_mod.TOOL_RINGS, "clock_probe_tool", 0)
        # An unknown tool is outward to the governance check; this one reads.
        from agent_friday.governance import action_gate
        monkeypatch.setattr(action_gate, "INTERNAL_TOOLS",
                            action_gate.INTERNAL_TOOLS | {"clock_probe_tool"})
        result = agent_mod._execute_tool("clock_probe_tool", {})
        assert "2026-08-14 (Friday)" in result

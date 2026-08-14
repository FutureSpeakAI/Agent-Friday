"""Acceptance tests T1/T2 (docs: toolcall-integrity-v5) — end-to-end through
POST /api/chat, tying FR-2/FR-4 together against the exact failure shape from
the 2026-08-12 incident: a "start my day" turn where Google isn't connected
and the model narrates fabricated results instead of admitting it can't get
them.

T3 (chat wrap), T4 (conformance gate red/green), T5 (URL provenance) each
have their own dedicated test files (test_chat_wrap_css.py,
test_model_seat_gate.py + test_model_seat_gate_route.py,
test_response_provenance.py + test_chat_provenance.py) — not duplicated
here. T6 (real Google data end-to-end after Stephen authorizes Google and
seats a green model) requires his own live action and isn't automatable.
"""
from __future__ import annotations

import agent_friday.routes.chat as chat_mod
from agent_friday.services.model_router import TOOLCALL_FABRICATION_FAILURE_MESSAGE


class TestT1HonestInabilityWithNoToolsConnected:
    """T1: tools disabled/unconnected -> honest inability, zero brackets
    rendered or spoken."""

    def test_model_admits_inability_reply_passes_through_clean(self, client, monkeypatch):
        # The honest path: the model correctly used the not-connected sentinel
        # and told the truth. No brackets, no fabrication, nothing to strip.
        def honest_dispatch(messages, **kwargs):
            return ("Your calendar and email aren't connected yet — want me "
                    "to walk you through the one-time setup?"), []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", honest_dispatch)
        resp = client.post("/api/chat", json={"message": "start my day"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "connected" in data["response"]
        assert "[" not in data["response"]
        assert data["tool_trace"] == []

    def test_reproduced_incident_shape_is_caught_and_replaced(self, client, monkeypatch):
        # This is the literal reported shape: bracketed pseudo-tool-calls
        # narrated as prose, an invented meeting, a minted calendar URL, and
        # an invented "outage" excuse when that fake URL would have 404'd.
        confabulation = (
            "Good morning! [query_calendar] shows you have a meeting with "
            "Mr. Peterson at 2pm. [search_email(priority:high)] found nothing "
            "urgent. I tried [open_url(https://calendar.google.com/event?"
            "eid=dGVzdF9lc3NlbnRzLmV4YW1wbGUtdG8)] but Google Calendar seems "
            "to be having an outage right now."
        )

        def always_confabulates(messages, **kwargs):
            return confabulation, []

        def confabulates_plain(messages, **kwargs):
            # B7 gives one last tools-stripped chance via _call_claude —
            # keep that fabricating too, to pin the terminal honest failure.
            return confabulation

        monkeypatch.setattr(chat_mod, "_call_claude_agent", always_confabulates)
        monkeypatch.setattr(chat_mod, "_call_claude", confabulates_plain)
        resp = client.post("/api/chat", json={"message": "start my day"})
        assert resp.status_code == 200
        data = resp.get_json()
        # Zero brackets, zero registry tool names, zero the fake URL — the
        # exhausted-retry honest-failure message, not the fabrication.
        assert data["response"] == TOOLCALL_FABRICATION_FAILURE_MESSAGE
        assert "[" not in data["response"]
        assert "Mr. Peterson" not in data["response"]
        assert "calendar.google.com" not in data["response"]
        assert data["tool_trace"] == []
        # Never persisted as if it were real, either.
        hist = client.get("/api/chat/history").get_json()
        assert "Mr. Peterson" not in str(hist)
        assert "outage" not in str(hist).lower()


class TestT2StrippedRetriedHonestFailure:
    """T2: canned narrated-bracket output -> stripped, retried, honest
    failure. Verifies the retry actually happens (not just a same-turn
    strip) and that the model gets a real second chance before giving up."""

    def test_retry_gets_a_corrective_instruction_and_can_recover(self, client, monkeypatch):
        calls = []

        def dispatch(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return "[query_calendar] shows a 2pm meeting.", []
            # Second call receives the corrective note as the newest user
            # message and responds honestly.
            assert "fabricated tool-call syntax" in messages[-1]["content"]
            return "Calendar isn't connected — want me to set it up?", []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        resp = client.post("/api/chat", json={"message": "start my day"})
        data = resp.get_json()
        assert len(calls) == 2
        assert "query_calendar" not in data["response"]
        assert "connected" in data["response"]

    def test_never_executes_the_fabricated_bracket_as_a_real_action(self, client, monkeypatch):
        # A fabricated draft_email(...) must never become a real tool_trace
        # entry implying an email was actually sent/drafted.
        def dispatch(messages, **kwargs):
            return "[draft_email(to:landlord, subject:heater)] Sent it for you!", []

        monkeypatch.setattr(chat_mod, "_call_claude_agent", dispatch)
        resp = client.post("/api/chat", json={"message": "email my landlord about the heater"})
        data = resp.get_json()
        assert data["tool_trace"] == []
        assert "draft_email" not in str(data["tool_trace"])

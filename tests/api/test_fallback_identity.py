"""Fallback identity: the cloud leg never inherits a local model id.

The failure chain this guards: subagent seat = gemma4:e4b (local) → local leg
404s → the CLOUD fallback leg forwards the LOCAL id, so Anthropic receives
model="gemma4:e4b" and 404s → no other provider key → RuntimeError, and an
hourly scheduled heartbeat stays dead.

Law under test: a cloud provider must NEVER receive a local model id. The
escalation path translates to a configured cloud model, or fails honestly
naming the reason.
"""
from __future__ import annotations

import pytest

import agent_friday.services.agent as agent_mod
from agent_friday.services import model_seat_gate


class TestCloudFallbackNeverGetsLocalId:
    def test_local_failure_escalates_with_cloud_model_not_local_id(self, client, monkeypatch):
        seen = {}

        def fake_ollama(messages, **kwargs):
            raise RuntimeError("HTTP Error 404: Not Found")

        def fake_claude(messages, model=None, **kwargs):
            seen["cloud_model"] = model
            return "cloud answered", []

        monkeypatch.setattr(agent_mod, "_call_ollama", fake_ollama)
        monkeypatch.setattr(agent_mod, "_call_claude_agent", fake_claude)
        monkeypatch.setattr(agent_mod, "get_anthropic_client", lambda: object())

        text, trace = agent_mod._generate_agent(
            [{"role": "user", "content": "heartbeat"}],
            model="gemma4:e4b",   # a local subagent seat
        )
        assert text == "cloud answered"
        cloud_model = seen.get("cloud_model")
        assert cloud_model is None or str(cloud_model).startswith("claude"), (
            f"cloud fallback leg received a LOCAL model id: {cloud_model!r} — "
            f"Anthropic would 404 on it")

    def test_openai_id_never_reaches_claude_either(self, client, monkeypatch):
        seen = {}

        def fake_openai(messages, **kwargs):
            raise RuntimeError("no key")

        def fake_claude(messages, model=None, **kwargs):
            seen["cloud_model"] = model
            return "ok", []

        monkeypatch.setattr(agent_mod, "_call_openai", fake_openai)
        monkeypatch.setattr(agent_mod, "_call_claude_agent", fake_claude)
        monkeypatch.setattr(agent_mod, "get_anthropic_client", lambda: object())

        agent_mod._generate_agent(
            [{"role": "user", "content": "hi"}],
            model="mistral-large-latest",
        )
        cloud_model = seen.get("cloud_model")
        assert cloud_model is None or str(cloud_model).startswith("claude")


class TestNothingSubstitutesAnyMore:
    """The last-known-green fallback is gone, and with it a whole failure mode.

    A seat gate that swaps a refused model for the last one that scored green
    can hand over a substitute that is no longer installed (gemma4:e4b ->
    refused -> gemma4:latest -> deleted from the daemon -> 404).

    There is no seat gate: `resolve_local_seat` is a pass-through, and the
    model asked for is the model dispatched. With no substitution step, the
    "stale green substitute" class of bug cannot occur.

    The cloud-identity law above stands on its own and is where the real
    protection lives — a cloud provider must never receive a local model
    id, whatever the reason a local leg failed.
    """

    def test_resolve_local_seat_returns_what_it_was_asked_for(self):
        from agent_friday.services import model_seat_gate as gate
        for model in ("gemma4:e4b", "brand-new:70b", "never-gated:1b"):
            seat = gate.resolve_local_seat(model)
            assert seat["model"] == model, (
                "%r came back as %r — something is still substituting"
                % (model, seat["model"]))

    def test_there_is_no_last_known_green_to_fall_back_to(self):
        from agent_friday.services import model_seat_gate as gate
        assert gate.get_last_known_green() is None

    def test_a_model_that_never_scored_is_still_dispatched(self):
        """The user may set any model at any seat; that is non-negotiable."""
        from agent_friday.services import model_seat_gate as gate
        seat = gate.resolve_local_seat("something-nobody-ever-tested:3b")
        assert seat["model"] == "something-nobody-ever-tested:3b"
        assert seat.get("seat_ok") is not False

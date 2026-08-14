"""2026-08-14 live incident — fallback identity bug.

Observed chain (friday.log + schedule_runs.jsonl, hourly heartbeat failing
since ~midnight): subagent seat = gemma4:e4b (local) → seat gate refuses
(ungated) → falls back to last-known-green gemma4:latest → that model was
deleted from the daemon → local 404 → the CLOUD fallback leg then forwarded
the LOCAL id, so Anthropic received model="gemma4:e4b" and 404'd
(req_011Ce2kC6QqtmcQRKb4e4wmu) → openai no key → RuntimeError, heartbeat
dead all night.

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
            model="gemma4:e4b",   # the live subagent seat that started it all
        )
        assert text == "cloud answered"
        cloud_model = seen.get("cloud_model")
        assert cloud_model is None or str(cloud_model).startswith("claude"), (
            f"cloud fallback leg received a LOCAL model id: {cloud_model!r} — "
            f"this is the exact Anthropic 404 from the live incident")

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


class TestLastKnownGreenInvalidation:
    def test_green_but_uninstalled_model_is_not_offered_as_fallback(self, monkeypatch, tmp_path):
        # gemma4:latest holds green evidence but was deleted from the daemon
        # — the dynamic catalog knows it's gone; the gate must too.
        monkeypatch.setattr(model_seat_gate, "is_seat_green",
                            lambda m, p="local": False)
        monkeypatch.setattr(model_seat_gate, "get_last_known_green",
                            lambda p="local": "gemma4:latest")
        monkeypatch.setattr(model_seat_gate, "_installed_local_models",
                            lambda: {"gemma4:e2b", "gemma4:e4b"})
        seat = model_seat_gate.resolve_local_seat("gemma4:e4b")
        assert seat["model"] is None, (
            "an uninstalled model must not be seated — it 404s at dispatch")
        assert seat["fallback"] == "tool_free"
        assert "no longer installed" in seat["reason"]
        assert "gemma4:latest" in seat["reason"]

    def test_green_and_installed_fallback_still_works(self, monkeypatch):
        monkeypatch.setattr(model_seat_gate, "is_seat_green",
                            lambda m, p="local": False)
        monkeypatch.setattr(model_seat_gate, "get_last_known_green",
                            lambda p="local": "gemma4:latest")
        monkeypatch.setattr(model_seat_gate, "_installed_local_models",
                            lambda: {"gemma4:latest", "gemma4:e4b"})
        seat = model_seat_gate.resolve_local_seat("gemma4:e4b")
        assert seat["model"] == "gemma4:latest"
        assert seat["fallback"] == "last_known_green:gemma4:latest"

    def test_daemon_unreachable_keeps_legacy_behavior(self, monkeypatch):
        # Can't verify inventory → don't invalidate (the local call will fail
        # with its own honest error if the model is really gone).
        monkeypatch.setattr(model_seat_gate, "is_seat_green",
                            lambda m, p="local": False)
        monkeypatch.setattr(model_seat_gate, "get_last_known_green",
                            lambda p="local": "gemma4:latest")
        monkeypatch.setattr(model_seat_gate, "_installed_local_models",
                            lambda: None)
        seat = model_seat_gate.resolve_local_seat("gemma4:e4b")
        assert seat["model"] == "gemma4:latest"


class TestRefusalReasonIsHuman:
    def test_reason_carries_score_not_a_dict_dump(self, monkeypatch):
        # Live notification for qwen3.6:35b embedded the ENTIRE status dict
        # (results array included) in the reason string — unreadable in any
        # notification surface. The reason must name the score, not the blob.
        monkeypatch.setattr(model_seat_gate, "is_seat_green",
                            lambda m, p="local": False)
        monkeypatch.setattr(model_seat_gate, "get_cached_status",
                            lambda m, p="local": {"passed": False, "score": "9/10",
                                                  "results": [{"huge": "blob"}] * 10})
        monkeypatch.setattr(model_seat_gate, "get_last_known_green",
                            lambda p="local": "gemma4:latest")
        monkeypatch.setattr(model_seat_gate, "_installed_local_models",
                            lambda: {"gemma4:latest"})
        seat = model_seat_gate.resolve_local_seat("qwen3.6:35b")
        assert "9/10" in seat["reason"]
        assert "results" not in seat["reason"]
        assert len(seat["reason"]) < 300

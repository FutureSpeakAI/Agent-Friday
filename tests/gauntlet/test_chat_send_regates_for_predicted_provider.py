"""Gauntlet finding F18: /api/chat/send (chat_send in routes/chat.py) built
its system prompt with provider='cloud' HARDCODED, before the router ever
ran. _generate_agent then runs the SAME router /api/chat uses, which can
correctly route a vault-tier message to a local seat -- but nothing
re-assembled the prompt for the seat that actually got chosen. Net effect
was never a leak (vault content was already stripped, never exposed) but a
local seat that IS entitled to see full vault content got a prompt
pre-redacted as if bound for the cloud: a degraded, silently-wrong answer
with no indication why. /api/chat's own _prep_for(provider) (chat.py:748)
already defers prompt assembly until a provider is known; this endpoint
just never got the same treatment.

This probe mocks _build_context_prompt to return a provider-tagged marker
string (rather than fighting the full vault-gating machinery) and proves
chat_send now builds its initial prompt for whatever provider the router
actually predicts -- not a hardcoded 'cloud' -- and that _generate_agent
receives a system_builder that can re-gate for a different provider if the
fallback ladder lands elsewhere (mirroring F30's already-fixed pattern).

Must be RED before the fix (the captured `provider` kwarg is always
'cloud', regardless of what routing predicts) and GREEN after.
"""
from __future__ import annotations

import pytest

import agent_friday.routes.chat as chat_mod
import agent_friday.server as friday_server

# tests/api/conftest.py's `client`/`app` fixtures are scoped to tests/api/
# only -- this file lives in tests/gauntlet/, so it builds its own,
# mirroring the established pattern in this directory (see
# test_local_only_fail_then_offer_cloud.py).


@pytest.fixture
def app():
    friday_server.app.config.update(TESTING=True)
    return friday_server.app


@pytest.fixture
def client(app):
    return app.test_client()


def _patch_common(monkeypatch, *, predicted_provider):
    captured = {"providers_seen": []}

    def _fake_build_context_prompt(message, workspace, workspace_context,
                                    vision_description, *, provider,
                                    vault_control=None, vault_fallback=None):
        captured["providers_seen"].append(provider)
        return f"PROMPT-FOR-{provider}", []

    monkeypatch.setattr(chat_mod, "_build_context_prompt",
                        _fake_build_context_prompt)
    monkeypatch.setattr(chat_mod, "_predict_route_provider",
                        lambda *a, **k: predicted_provider)

    def _fake_generate_agent(messages, system=None, system_builder=None, **kw):
        captured["system"] = system
        captured["system_builder"] = system_builder
        return "a reply", []

    monkeypatch.setattr(chat_mod, "_generate_agent", _fake_generate_agent)
    monkeypatch.setattr(chat_mod, "validate_toolcall_integrity",
                        lambda reply, tool_trace, tool_names, redispatch=None:
                            (reply, tool_trace, {}))
    return captured


class TestChatSendRegatesForPredictedProvider:
    def test_predicted_local_builds_a_prompt_gated_for_local_not_cloud(
            self, client, monkeypatch):
        captured = _patch_common(monkeypatch, predicted_provider="local")

        resp = client.post("/api/chat/send", json={"message": "hello there"})

        assert resp.status_code == 200
        assert "PROMPT-FOR-local" in (captured.get("system") or ""), (
            "chat_send built its system prompt for 'cloud' even though "
            "routing predicted 'local' -- the prompt is gated for the "
            "wrong provider, silently degrading what a local seat is "
            "entitled to see (F18)"
        )

    def test_predicted_cloud_still_builds_a_cloud_gated_prompt(
            self, client, monkeypatch):
        """Falsifiability check: the other branch must actually differ, or
        the test above could pass vacuously."""
        captured = _patch_common(monkeypatch, predicted_provider="cloud")

        resp = client.post("/api/chat/send", json={"message": "hello there"})

        assert resp.status_code == 200
        assert "PROMPT-FOR-cloud" in (captured.get("system") or "")

    def test_system_builder_can_regate_for_a_different_provider_than_predicted(
            self, client, monkeypatch):
        """A system_builder must actually be threaded through to
        _generate_agent (mirroring F30) so a fallback leg landing on a
        DIFFERENT provider than predicted can re-gate for itself, rather
        than silently reusing a prompt baked for the prediction."""
        captured = _patch_common(monkeypatch, predicted_provider="local")

        resp = client.post("/api/chat/send", json={"message": "hello there"})

        assert resp.status_code == 200
        builder = captured.get("system_builder")
        assert callable(builder), (
            "chat_send must pass a system_builder to _generate_agent so "
            "the fallback ladder can re-gate the prompt for whichever "
            "provider a leg actually runs on, not just the one predicted "
            "up front (F18/F30)"
        )
        assert "PROMPT-FOR-cloud" in builder("cloud"), (
            "the system_builder handed to _generate_agent does not "
            "actually re-derive a provider-specific prompt on demand"
        )

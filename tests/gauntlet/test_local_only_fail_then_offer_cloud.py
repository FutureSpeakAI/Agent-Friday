"""Gauntlet finding Q19 (final disposition, Stephen 2026-09-04): when
local_only mode has no local model available at all, the product must not
silently fall back to the cloud (that defeats the mode's whole point) and
must not just refuse with a dead-end error either. His exact words: "the
system should fail to function and produce an error, then it should ask
the user if it can go into cloud only mode. We should always prioritize
the user knowing what is being done with their data, what model is in
use." He named this a standing transparency principle, not a rule scoped
to this one case.

routing/model_router.py's local_only branch (see
test_local_only_mode_honors_its_own_promise.py for that half of Q19)
already refuses with `refuse: True` and a clear `warning` when no local
seat exists. This probe covers the OTHER half: the refusal must carry a
structured `offer_cloud_switch` marker through routes/chat.py's `/api/chat`
handler so the frontend can render an actual clickable choice — not just
prose the user has to act on by finding Settings themselves.

This probe must be RED before the fix (the /api/chat JSON response has no
offer_cloud_switch field at all, and the vault_blocked flag is misleadingly
True even though this isn't a vault refusal) and GREEN after.
"""
from __future__ import annotations

import pytest

import agent_friday.routes.chat as chat_mod
import agent_friday.server as friday_server
from agent_friday.core import DEFAULT_SETTINGS

# tests/api/conftest.py's `client`/`app` fixtures are scoped to tests/api/
# only (pytest fixtures apply per conftest.py directory tree) -- this file
# lives in tests/gauntlet/, so it builds its own, mirroring that file's
# exact fixture bodies rather than depending on a directory it isn't under.


@pytest.fixture
def app():
    friday_server.app.config.update(TESTING=True)
    return friday_server.app


@pytest.fixture
def client(app):
    """Requests originate from 127.0.0.1, which Friday's auth treats as
    the trusted local user, so routes are reachable without login."""
    return app.test_client()


class _FakeOllamaUnavailable:
    def is_available(self):
        return False

    def list_models(self):
        return []


def _local_only_settings():
    s = dict(DEFAULT_SETTINGS)
    s["model_routing"] = dict(s.get("model_routing") or {})
    s["model_routing"]["mode"] = "local_only"
    return s


def _arrange_no_local_model(monkeypatch):
    """local_only mode, with zero local candidates anywhere -- Friday's own
    model_store is empty and the Ollama daemon is unreachable -- so the
    router's own local_only branch must refuse rather than silently
    answering from the cloud."""
    monkeypatch.setattr(chat_mod, "_load_settings", lambda: _local_only_settings(),
                        raising=False)
    import agent_friday.services.model_store as model_store
    monkeypatch.setattr(model_store, "available", lambda: {}, raising=False)
    import agent_friday.routing.ollama_manager as ollama_manager
    monkeypatch.setattr(ollama_manager, "get_manager",
                        lambda *a, **k: _FakeOllamaUnavailable(), raising=False)
    import agent_friday.services.demo_mode as demo_mode
    monkeypatch.setattr(demo_mode, "is_demo", lambda *a, **k: False, raising=False)


class TestLocalOnlyFailThenOfferCloud:
    def test_no_local_model_response_offers_a_cloud_switch(self, client, monkeypatch):
        _arrange_no_local_model(monkeypatch)

        resp = client.post("/api/chat", json={"message": "what's the weather like?"})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("offer_cloud_switch") is True, (
            "local_only's no-local-model refusal must carry a structured "
            "offer_cloud_switch marker so the frontend can render an "
            "actual actionable choice, not just an error the user has to "
            "act on by finding Settings themselves — Stephen: 'fail... "
            "then ask the user if it can go into cloud only mode'"
        )
        assert body.get("friday_msg", {}).get("offer_cloud_switch") is True, (
            "the persisted/rendered chat message itself must also carry "
            "the offer flag, not just the top-level response envelope"
        )

    def test_no_local_model_response_is_not_mislabeled_as_a_vault_block(
            self, client, monkeypatch):
        """This refusal has nothing to do with the vault -- mislabeling it
        vault_blocked would confuse any caller (frontend or otherwise)
        that branches on that flag specifically to mean 'this was a vault
        privacy decision.'"""
        _arrange_no_local_model(monkeypatch)

        resp = client.post("/api/chat", json={"message": "what's the weather like?"})

        body = resp.get_json()
        assert body.get("vault_blocked") is not True, (
            "a local_only-mode-has-no-model refusal is not a vault "
            "decision and must not set vault_blocked=True"
        )

    def test_the_warning_text_names_the_real_situation(self, client, monkeypatch):
        _arrange_no_local_model(monkeypatch)

        resp = client.post("/api/chat", json={"message": "what's the weather like?"})

        text = resp.get_json().get("response", "").lower()
        assert "local" in text and ("cloud" in text or "ollama" in text), (
            "the refusal text must plainly say what's happening (no local "
            "model available) and name the offered alternative — this is "
            "the transparency Stephen asked for, not a generic error"
        )

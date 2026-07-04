"""Routing-layer integration for the registry-first resolver (GAP-4):
route() must attribute aggregator/org-model ids to their owning provider and
carry `provider_name` on the decision so the executor can dispatch
multi-provider (GAP-3). Offline — Ollama and the daemon seam are stubbed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.routing.provider_descriptors as pd
from agent_friday.routing.model_router import ModelRouter


def _msgs(text="hello"):
    return [{"role": "user", "content": text}]


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch):
    monkeypatch.setattr(pd, "_live_ollama_has", lambda prov, mid: False)
    # route() consults the Ollama manager in some modes — keep it down.
    import agent_friday.routing.ollama_manager as om

    class _Down:
        base_url = "http://localhost:11434"
        def is_available(self):
            return False
        def list_models(self):
            return []

    monkeypatch.setattr(om, "get_manager", lambda *a, **k: _Down())


class TestResolverRouting:
    def test_openrouter_id_routes_to_openai_with_provider_name(self):
        """THE GAP-4 regression: an org/model:variant id used to be classified
        local by the ':' heuristic and sent to Ollama."""
        r = ModelRouter(config={"mode": "cloud_only"})
        res = r.route(_msgs(), task_context={
            "cloud_model": "meta-llama/llama-4-maverick:free"})
        assert res["provider"] == "openai"
        assert res["provider_name"] == "openrouter"
        assert res["model"] == "meta-llama/llama-4-maverick:free"
        assert res["is_local"] is False
        assert res["scrub_pii"] is True  # cloud hygiene intact

    def test_claude_id_stays_cloud_anthropic(self):
        r = ModelRouter(config={"mode": "cloud_only"})
        res = r.route(_msgs(), task_context={"cloud_model": "claude-sonnet-5"})
        assert res["provider"] == "cloud"
        assert res.get("provider_name") == "anthropic"

    def test_gpt_id_routes_to_openai_provider(self):
        r = ModelRouter(config={"mode": "cloud_only"})
        res = r.route(_msgs(), task_context={"cloud_model": "gpt-4o"})
        assert res["provider"] == "openai"
        assert res.get("provider_name") == "openai"
        assert res["model"] == "gpt-4o"

    def test_enabled_template_statics_route_to_their_provider(self):
        from agent_friday.services.provider_registry import get_provider_registry
        ds = get_provider_registry().get_provider("deepseek")
        was = ds.get("enabled")
        ds["enabled"] = True
        try:
            r = ModelRouter(config={"mode": "cloud_only"})
            res = r.route(_msgs(), task_context={"cloud_model": "deepseek-reasoner"})
            assert res["provider"] == "openai"
            assert res["provider_name"] == "deepseek"
            assert res["model"] == "deepseek-reasoner"
        finally:
            ds["enabled"] = was

    def test_legacy_explicit_slot_still_wins(self):
        """cloud_provider=openrouter (legacy single-slot) preserves today's
        behavior: retag to 'openai' with the configured openai_model."""
        r = ModelRouter(config={"mode": "cloud_only",
                                "cloud_provider": "openrouter",
                                "openai_model": "mistral:7b"})
        res = r.route(_msgs(), task_context={"cloud_model": "claude-sonnet-5"})
        assert res["provider"] == "openai"
        assert res["model"] == "mistral:7b"
        assert "provider_name" not in res or res["provider_name"] is None

    def test_local_pick_still_routes_local_under_explicit_slot(self):
        """Picking a local model must beat the legacy explicit slot."""
        r = ModelRouter(config={"mode": "cloud_only",
                                "cloud_provider": "openrouter",
                                "local_model_names": ["mymodel:latest"]})
        res = r.route(_msgs(), task_context={"cloud_model": "mymodel:latest"})
        assert res["provider"] == "local"
        assert res["vault_allowed"] is True

    def test_registry_local_claude_name_never_cloud(self, monkeypatch):
        """A locally installed model NAMED like a cloud model routes local
        (registry/daemon truth beats the 'claude' prefix)."""
        monkeypatch.setattr(pd, "_live_ollama_has",
                            lambda prov, mid: mid == "claude-x:latest")
        r = ModelRouter(config={"mode": "cloud_only"})
        res = r.route(_msgs(), task_context={"cloud_model": "claude-x:latest"})
        assert res["provider"] == "local"
        assert res["model"] == "claude-x:latest"

    def test_vault_still_forces_local_over_everything(self):
        r = ModelRouter(config={"mode": "cloud_only",
                                "vault_cloud_fallback": "deny"})
        res = r.route(_msgs("what is my ssn stored in the vault"),
                      task_context={"cloud_model":
                                    "meta-llama/llama-4-maverick:free"})
        # No local model available + deny → refuse; never a cloud dispatch.
        assert res["vault_access"] is True
        assert res["refuse"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

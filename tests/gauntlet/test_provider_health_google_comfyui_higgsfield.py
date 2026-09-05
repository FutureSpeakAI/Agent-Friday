"""Gauntlet finding F2: provider_health.py's deep-check path proves real
reachability for ollama/anthropic/openai-compatible (13 of 16 registered
providers, 11 of them only because they share the openai-compatible type),
but silently falls back to a decorative "key present" / unconditional "ok"
for the other three: google, local-comfyui, higgsfield. This is the same
defect class KNOWN_ISSUES.md already documents once (a health check that
special-cases some providers by name so the rest misreport silently) --
here it recurs with a different residual set.

Each test below plants a provider in a definitely-unhealthy state and
asserts GET-equivalent `check_provider(name, deep=True)` actually reports
that, rather than reporting "ok" regardless. Before the fix these are RED
(the unhealthy state is invisible); after the fix they are GREEN.
"""
from __future__ import annotations

import pytest

from agent_friday.services import provider_health as ph


@pytest.fixture(autouse=True)
def _clear_caches():
    ph._CACHE.clear()
    ph.reset_probe_cache()
    yield
    ph._CACHE.clear()
    ph.reset_probe_cache()


def _google_provider():
    return {"name": "google-gemini", "type": "google",
            "auth": {"type": "env_var", "key": "GEMINI_API_KEY"},
            "models": ["gemini-3.5-flash"]}


def _comfyui_provider():
    return {"name": "local-comfyui", "type": "comfyui",
            "auth": {"type": "none"}}


def _higgsfield_provider():
    return {"name": "higgsfield", "type": "higgsfield",
            "auth": {"type": "none"}}


class TestGoogleDeepCheckProvesInference:
    def test_deep_check_reports_down_when_generation_fails(self, monkeypatch):
        monkeypatch.setattr(ph, "_provider", lambda name: _google_provider())
        monkeypatch.setattr(ph, "_has_key", lambda prov: True)

        class _BrokenClient:
            class models:
                @staticmethod
                def generate_content(**kwargs):
                    raise RuntimeError("PERMISSION_DENIED: revoked key")

        monkeypatch.setattr("agent_friday.core.get_genai_client",
                            lambda: _BrokenClient())

        result = ph.check_provider("google-gemini", deep=True, use_cache=False)
        assert result.get("proved_inference") is not True, (
            "a deep check on google must not claim proved inference when "
            "generate_content raises"
        )
        assert result.get("status") != "ok", (
            "deep=1 on a broken google key returned 'ok' -- the deep probe "
            "silently fell back to the shallow 'key present' response "
            "instead of proving inference"
        )


class TestComfyUIReachabilityIsChecked:
    def test_down_when_server_unreachable(self, monkeypatch):
        monkeypatch.setattr(ph, "_provider", lambda name: _comfyui_provider())

        def _unreachable(timeout=3):
            raise ConnectionRefusedError("no server on 8188")
        monkeypatch.setattr("agent_friday.services.local_image.is_reachable",
                            lambda timeout=3: False)

        result = ph.check_provider("local-comfyui", deep=False, use_cache=False)
        assert result.get("status") != "ok", (
            "local-comfyui reported 'ok' with no server running -- "
            "_has_key()'s auth.type == 'none' short-circuit means it never "
            "checks reachability at all"
        )


class TestHiggsfieldUsesRegistryAvailability:
    def test_down_when_connector_not_reachable(self, monkeypatch):
        monkeypatch.setattr(ph, "_provider", lambda name: _higgsfield_provider())

        class _Registry:
            def is_provider_available(self, name):
                return False

        monkeypatch.setattr(
            "agent_friday.services.provider_registry.get_provider_registry",
            lambda: _Registry())

        result = ph.check_provider("higgsfield", deep=False, use_cache=False)
        assert result.get("status") != "ok", (
            "higgsfield reported 'ok' even though "
            "provider_registry.is_provider_available() (the module's own "
            "correct MCP-connector-liveness check) says it is not reachable "
            "-- provider_health._check() never calls it"
        )

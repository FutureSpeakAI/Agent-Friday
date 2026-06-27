"""Regression: _generate_agent's provider fallback must honor the router's
vault decision.

The router force-routes vault-touching requests to a local model (or returns
refuse=True), but _generate_agent's generic retry chain ignored both signals:
a vault-forced local route that failed at runtime was silently retried on the
Anthropic/OpenAI cloud paths with the same messages — defeating the guarantee
the router just made. These tests pin the contract:

  1. vault + local failure  → refusal text, cloud/openai NEVER attempted
  2. router refuse=True     → refusal text, NO provider attempted
  3. non-vault cloud failure → fallback chain still works (no over-restriction)
"""
from __future__ import annotations

import pytest

import services.agent as agent_mod

# Opt out of the autouse LLM kill-switch: these tests run the REAL
# _generate_agent and stub the provider primitives themselves.
pytestmark = pytest.mark.real_provider_paths


class _Recorder:
    """Stands in for a provider primitive; records whether it was called."""

    def __init__(self, result=("provider reply", []), exc=None):
        self.calls = 0
        self._result = result
        self._exc = exc

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeOllama:
    base_url = "http://localhost:11434"

    def __init__(self, available=True, models=("gemma4:latest",)):
        self._available = available
        self._models = [{"name": n} for n in models]

    def is_available(self):
        return self._available

    def list_models(self):
        return self._models


def _patch_settings(monkeypatch, routing):
    monkeypatch.setattr(
        agent_mod, "_load_settings",
        lambda: {"model_routing": routing}, raising=False,
    )
    # Pretend an Anthropic client exists so _via_claude reaches the recorder
    # instead of bailing on "no key" — in production a key IS present, and the
    # leak must be demonstrated against that reality.
    monkeypatch.setattr(agent_mod, "get_anthropic_client",
                        lambda *a, **k: object(), raising=False)
    # Demo mode intercepts before any provider is reached when no API keys are
    # present (CI environment). Disable it so the fallback chain runs normally.
    import services.demo_mode as _dm
    monkeypatch.setattr(_dm, "is_demo", lambda: False)


VAULT_MSG = [{"role": "user", "content": "summarize my health record for me"}]
PLAIN_MSG = [{"role": "user", "content": "write a haiku about espresso"}]


def test_vault_local_failure_never_falls_back_to_cloud(monkeypatch):
    import ollama_manager
    monkeypatch.setattr(ollama_manager, "get_manager",
                        lambda *a, **k: _FakeOllama(available=True))
    _patch_settings(monkeypatch, {"mode": "cloud_only"})

    local = _Recorder(exc=RuntimeError("ollama exploded mid-call"))
    cloud = _Recorder()
    openai = _Recorder()
    monkeypatch.setattr(agent_mod, "_call_ollama", local)
    monkeypatch.setattr(agent_mod, "_call_claude_agent", cloud)
    monkeypatch.setattr(agent_mod, "_call_openai", openai)

    text, trace = agent_mod._generate_agent(VAULT_MSG, system="sys")

    assert local.calls == 1
    assert cloud.calls == 0, "vault-forced request must never retry on Anthropic"
    assert openai.calls == 0, "vault-forced request must never retry on OpenAI"
    assert trace == []
    assert "vault" in text.lower()
    assert "cloud" in text.lower() or "local" in text.lower()


def test_router_refuse_flag_is_honored(monkeypatch):
    import ollama_manager
    # No local model available + deny fallback → router returns refuse=True.
    monkeypatch.setattr(ollama_manager, "get_manager",
                        lambda *a, **k: _FakeOllama(available=False))
    _patch_settings(monkeypatch, {"mode": "cloud_only",
                                  "vault_cloud_fallback": "deny"})

    local = _Recorder()
    cloud = _Recorder()
    openai = _Recorder()
    monkeypatch.setattr(agent_mod, "_call_ollama", local)
    monkeypatch.setattr(agent_mod, "_call_claude_agent", cloud)
    monkeypatch.setattr(agent_mod, "_call_openai", openai)

    text, trace = agent_mod._generate_agent(VAULT_MSG, system="sys")

    assert (local.calls, cloud.calls, openai.calls) == (0, 0, 0), \
        "refuse=True means no provider may see the request"
    assert text.strip(), "user must get an explanation, not an empty reply"
    assert "local model" in text.lower()


def test_non_vault_request_keeps_full_fallback_chain(monkeypatch):
    import ollama_manager
    monkeypatch.setattr(ollama_manager, "get_manager",
                        lambda *a, **k: _FakeOllama(available=False))
    _patch_settings(monkeypatch, {"mode": "cloud_only"})

    cloud = _Recorder(exc=RuntimeError("anthropic down"))
    openai = _Recorder(result=("openai saved the day", []))
    local = _Recorder(exc=RuntimeError("no ollama"))
    monkeypatch.setattr(agent_mod, "_call_claude_agent", cloud)
    monkeypatch.setattr(agent_mod, "_call_openai", openai)
    monkeypatch.setattr(agent_mod, "_call_ollama", local)

    text, trace = agent_mod._generate_agent(PLAIN_MSG, system="sys")

    assert cloud.calls == 1, "plain request should try the routed cloud provider first"
    assert openai.calls == 1, "plain request must still fall back across providers"
    assert text == "openai saved the day"

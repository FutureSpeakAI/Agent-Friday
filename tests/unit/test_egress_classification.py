"""Egress classification matrix (GAP-9 fix) — the gate's local-bypass decision
must be REGISTRY-driven, not a name-set lookup.

Matrix under test (name × classification × adapter × host):
  * registry providers classify by their descriptor's effective classification
  * "local" is only honored for local-capable adapters at private hosts,
    re-verified at call time
  * a descriptor TYPED ollama with a REMOTE base_url is CLOUD (sealed)
  * non-registry names fall back to the legacy {"ollama", "local"} set
  * unknown / empty provider strings are cloud (fail-closed)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import egress_gate
from agent_friday.services.provider_registry import get_provider_registry


@pytest.fixture()
def registry():
    return get_provider_registry()


def _with_provider(registry, desc):
    """Insert a raw descriptor into the live registry for one test (normalized
    exactly as a JSON drop would be), yielding cleanup."""
    from agent_friday.routing.provider_descriptors import normalize_descriptor
    norm = normalize_descriptor(desc)
    registry._providers[norm["name"]] = norm
    return norm


class TestRegistryDrivenClassification:
    def test_builtin_cloud_providers_are_cloud(self):
        for name in ("anthropic", "openai", "google-gemini", "openrouter",
                     "groq", "huggingface", "deepseek", "perplexity"):
            assert egress_gate.is_local_provider(name) is False, name
            assert egress_gate._is_cloud(name) is True, name

    def test_builtin_ollama_is_local(self):
        assert egress_gate.is_local_provider("ollama-local") is True
        assert egress_gate._is_cloud("ollama-local") is False

    def test_builtin_voice_engines_are_local(self):
        assert egress_gate.is_local_provider("local-voice-lite") is True
        assert egress_gate.is_local_provider("nvidia-nemo") is True

    def test_legacy_family_names_still_bypass(self):
        """Executor enums not in the registry: belt-and-braces set."""
        assert egress_gate.is_local_provider("ollama") is True
        assert egress_gate.is_local_provider("local") is True

    def test_unknown_provider_is_cloud(self):
        assert egress_gate.is_local_provider("mystery-endpoint") is False
        assert egress_gate._is_cloud("mystery-endpoint") is True

    def test_empty_provider_is_cloud(self):
        assert egress_gate.is_local_provider("") is False
        assert egress_gate.is_local_provider(None) is False

    def test_ollama_typed_remote_descriptor_is_cloud(self, registry):
        """THE GAP-9 case: type 'ollama' + remote URL must be sealed."""
        _with_provider(registry, {
            "name": "sneaky-remote-ollama", "type": "ollama",
            "base_url": "https://evil.example-nonexistent-xyz.invalid",
            "auth": {"type": "none"}, "enabled": True,
        })
        try:
            assert egress_gate.is_local_provider("sneaky-remote-ollama") is False
            assert egress_gate._is_cloud("sneaky-remote-ollama") is True
        finally:
            registry._providers.pop("sneaky-remote-ollama", None)

    def test_local_openai_compat_on_lan_bypasses(self, registry):
        """LM Studio / vLLM on a LAN address: legit local bypass (fixes the
        false-gating of genuinely local OpenAI-compat servers)."""
        _with_provider(registry, {
            "name": "my-vllm", "type": "openai-compatible",
            "base_url": "http://192.168.1.40:8000/v1",
            "auth": {"type": "none"}, "classification": "local",
            "enabled": True,
        })
        try:
            assert egress_gate.is_local_provider("my-vllm") is True
        finally:
            registry._providers.pop("my-vllm", None)

    def test_same_adapter_cloud_stays_gated(self, registry):
        """The SAME openai-compatible adapter pointed at a public URL stays
        cloud — classification is per-provider, not per-adapter."""
        _with_provider(registry, {
            "name": "my-cloud-compat", "type": "openai-compatible",
            "base_url": "https://api.somewhere.example/v1",
            "auth": {"type": "env_var", "key": "X"},
            "classification": "local",  # claimed — must be demoted
            "enabled": True,
        })
        try:
            assert egress_gate.is_local_provider("my-cloud-compat") is False
        finally:
            registry._providers.pop("my-cloud-compat", None)

    def test_call_time_reverification(self, registry):
        """A base_url edited to a public host AFTER load must lose the bypass
        (the call-time re-check half of the double verification)."""
        norm = _with_provider(registry, {
            "name": "flippy", "type": "openai-compatible",
            "base_url": "http://127.0.0.1:8000/v1",
            "auth": {"type": "none"}, "classification": "local",
            "enabled": True,
        })
        try:
            assert egress_gate.is_local_provider("flippy") is True
            norm["base_url"] = "https://exfil.example-nonexistent-xyz.invalid/v1"
            assert egress_gate.is_local_provider("flippy") is False
        finally:
            registry._providers.pop("flippy", None)


class TestSealOutboundIntegration:
    SENSITIVE = "My SSN is 123-45-6789 and my bank account number is 987654321."  # pragma: allowlist secret

    def test_cloud_registry_provider_gets_sealed(self):
        payload = {"messages": [{"role": "user", "content": self.SENSITIVE}]}
        sealed = egress_gate.seal_outbound(payload, "openrouter",
                                           log_path=Path("/dev/null"))
        out = str(sealed["messages"][0]["content"])
        assert "123-45-6789" not in out  # pragma: allowlist secret

    def test_local_registry_provider_bypasses(self):
        payload = {"messages": [{"role": "user", "content": self.SENSITIVE}]}
        sealed = egress_gate.seal_outbound(payload, "ollama-local",
                                           log_path=Path("/dev/null"))
        assert sealed["messages"][0]["content"] == self.SENSITIVE

    def test_remote_ollama_typed_provider_gets_sealed(self):
        reg = get_provider_registry()
        from agent_friday.routing.provider_descriptors import normalize_descriptor
        reg._providers["sneaky2"] = normalize_descriptor({
            "name": "sneaky2", "type": "ollama",
            "base_url": "https://collector.example-nonexistent-xyz.invalid",
            "auth": {"type": "none"}, "enabled": True,
        })
        try:
            payload = {"messages": [{"role": "user", "content": self.SENSITIVE}]}
            sealed = egress_gate.seal_outbound(payload, "sneaky2",
                                               log_path=Path("/dev/null"))
            out = str(sealed["messages"][0]["content"])
            assert "123-45-6789" not in out  # pragma: allowlist secret
        finally:
            reg._providers.pop("sneaky2", None)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

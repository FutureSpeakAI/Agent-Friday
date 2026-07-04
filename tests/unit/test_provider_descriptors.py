"""Unit tests for routing/provider_descriptors — descriptor schema v2,
normalization (v1 → v2), validation, private-host verification, and the
registry-first model resolver (the GAP-4 regression suite).

Security-critical: the resolver decides which provider a model id dispatches
to, and classification decides whether the egress gate is bypassed. Every test
is offline — the live-Ollama seam is monkeypatched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.routing.provider_descriptors as pd
from agent_friday.routing.provider_descriptors import (
    BUILTIN_EXTRA_PROVIDERS,
    adapter_of,
    auth_headers,
    classification_of,
    is_private_host,
    normalize_descriptor,
    provider_env_keys,
    resolve_model,
    validate_descriptor,
)


# ── is_private_host ──────────────────────────────────────────────────────────

class TestPrivateHost:
    @pytest.mark.parametrize("url", [
        "http://localhost:11434",
        "http://127.0.0.1:8000/v1",
        "https://127.0.0.1",
        "http://10.0.0.5:8000",
        "http://172.16.4.2/v1",
        "http://192.168.1.40:8000/v1",
        "http://mybox.local:1234/v1",
        "http://nas.lan/v1",
        "http://[::1]:8080",
        "",          # in-process engines (local-voice) have no URL
        None,
    ])
    def test_private_urls(self, url):
        assert is_private_host(url) is True

    @pytest.mark.parametrize("url", [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "http://8.8.8.8/v1",
        "https://93.184.216.34",
    ])
    def test_public_urls(self, url):
        assert is_private_host(url, resolve_dns=False) is False

    def test_unresolvable_hostname_is_not_local(self):
        # Fail-closed: cannot verify → NOT local → gets gated.
        assert is_private_host("http://evil.example-nonexistent-domain-xyz.invalid") is False

    def test_dns_disabled_hostname_not_local(self):
        assert is_private_host("http://some-lan-box", resolve_dns=False) is False


# ── normalize_descriptor ─────────────────────────────────────────────────────

class TestNormalize:
    def test_v1_type_aliases_to_adapter(self):
        d = normalize_descriptor({"name": "x", "type": "openai-compatible",
                                  "base_url": "https://api.x.test/v1"})
        assert d["adapter"] == "openai-compatible"
        assert d["type"] == "openai-compatible"
        assert d["schema_version"] == 2

    def test_adapter_type_field_accepted(self):
        d = normalize_descriptor({"name": "x", "adapter_type": "anthropic"})
        assert d["adapter"] == "anthropic"

    def test_api_key_env_list_folds_into_auth(self):
        d = normalize_descriptor({
            "name": "hf", "type": "openai-compatible",
            "base_url": "https://router.huggingface.co/v1",
            "api_key_env": ["HF_TOKEN", "HUGGINGFACE_API_KEY"],
        })
        assert d["auth"]["key"] == "HF_TOKEN"
        assert "HUGGINGFACE_API_KEY" in d["auth"]["key_aliases"]
        assert provider_env_keys(d) == ["HF_TOKEN", "HUGGINGFACE_API_KEY"]

    def test_default_classification_is_cloud(self):
        d = normalize_descriptor({"name": "x", "type": "openai-compatible",
                                  "base_url": "https://api.x.test/v1"})
        assert d["classification"] == "cloud"

    def test_ollama_type_on_localhost_infers_local(self):
        d = normalize_descriptor({"name": "o", "type": "ollama",
                                  "base_url": "http://localhost:11434"})
        assert d["classification"] == "local"

    def test_ollama_type_on_remote_url_is_cloud(self):
        """GAP-9 inverse: a descriptor TYPED ollama pointing at a remote URL
        must classify cloud (gated) — the type name earns nothing."""
        d = normalize_descriptor({"name": "sneaky", "type": "ollama",
                                  "base_url": "https://evil.example-nonexistent-xyz.invalid"})
        assert d["classification"] == "cloud"

    def test_local_claim_demoted_on_public_url(self):
        d = normalize_descriptor({
            "name": "fake-local", "type": "openai-compatible",
            "base_url": "https://api.public.test/v1",
            "classification": "local",
        })
        assert d["classification"] == "cloud"

    def test_local_claim_honored_on_private_url(self):
        d = normalize_descriptor({
            "name": "my-vllm", "type": "openai-compatible",
            "base_url": "http://192.168.1.40:8000/v1",
            "classification": "local",
        })
        assert d["classification"] == "local"

    def test_local_claim_never_honored_for_cloud_adapters(self):
        d = normalize_descriptor({"name": "x", "type": "anthropic",
                                  "base_url": "http://127.0.0.1:9",
                                  "classification": "local"})
        assert d["classification"] == "cloud"

    def test_egress_classification_alias(self):
        d = normalize_descriptor({
            "name": "x", "type": "openai-compatible",
            "base_url": "http://127.0.0.1:8000/v1",
            "egress_classification": "local",
        })
        assert d["classification"] == "local"

    def test_defaults_filled(self):
        d = normalize_descriptor({"name": "x", "type": "openai-compatible",
                                  "base_url": "https://a.test/v1"})
        assert d["discovery"]["mode"] == "static"
        assert d["network"]["timeout_s"] == 180
        assert d["pricing"]["source"] in ("dataset", "discovery", "static")
        assert d["budget"]["on_exceed"] == "block"
        assert d["priority"] == 50
        assert d["enabled"] is True

    def test_never_mutates_input(self):
        src = {"name": "x", "type": "ollama", "base_url": "http://localhost:11434"}
        normalize_descriptor(src)
        assert "classification" not in src and "adapter" not in src


# ── validate_descriptor ──────────────────────────────────────────────────────

class TestValidate:
    def _ok(self, **over):
        base = {"name": "test-prov", "type": "openai-compatible",
                "base_url": "https://api.test.example/v1",
                "auth": {"type": "env_var", "key": "TEST_KEY"}}
        base.update(over)
        return base

    def test_valid_descriptor_passes(self):
        ok, errors, _ = validate_descriptor(self._ok())
        assert ok, errors

    def test_name_required(self):
        ok, errors, _ = validate_descriptor(self._ok(name=""))
        assert not ok and any("name" in e for e in errors)

    @pytest.mark.parametrize("bad", ["UPPER", "has space", "-leading", "a" * 70])
    def test_name_format(self, bad):
        ok, _, _ = validate_descriptor(self._ok(name=bad))
        assert not ok

    def test_unknown_adapter_rejected(self):
        ok, errors, _ = validate_descriptor(self._ok(type="quantum-tunnel"))
        assert not ok and any("adapter" in e for e in errors)

    def test_raw_api_key_rejected(self):
        """Spec §15.1 — keys never ride in descriptors."""
        ok, errors, _ = validate_descriptor(self._ok(api_key="sk-secret"))  # pragma: allowlist secret
        assert not ok
        assert any("credential" in e or "api key" in e.lower() for e in errors)

    def test_auth_block_api_key_rejected(self):
        ok, _, _ = validate_descriptor(
            self._ok(auth={"type": "env_var", "key": "K", "api_key": "sk-x"}))
        assert not ok

    def test_extra_headers_cannot_set_authorization(self):
        ok, errors, _ = validate_descriptor(
            self._ok(extra_headers={"Authorization": "Bearer x"}))
        assert not ok

    def test_http_public_cloud_rejected(self):
        ok, errors, _ = validate_descriptor(
            self._ok(base_url="http://api.public.example/v1"))
        assert not ok and any("https" in e for e in errors)

    def test_http_localhost_allowed(self):
        """The existing add-provider API contract: http on a private host is
        fine (vLLM/LM Studio/Ollama)."""
        ok, errors, _ = validate_descriptor(
            self._ok(base_url="http://localhost:9"))
        assert ok, errors

    def test_base_url_required_for_network_adapters(self):
        ok, _, _ = validate_descriptor(self._ok(base_url=""))
        assert not ok

    def test_local_claim_on_public_url_warns(self):
        ok, _, warnings = validate_descriptor(
            self._ok(base_url="https://api.public.example/v1",
                     classification="local"))
        assert ok  # warning, not error — it just gets demoted
        assert warnings

    def test_priority_bounds(self):
        ok, _, _ = validate_descriptor(self._ok(priority=250))
        assert not ok


# ── auth helpers ─────────────────────────────────────────────────────────────

class TestAuthHelpers:
    def test_provider_api_key_env_chain(self, monkeypatch):
        prov = normalize_descriptor({
            "name": "hf-test-x", "type": "openai-compatible",
            "base_url": "https://x.test/v1",
            "auth": {"type": "env_var", "key": "PDX_PRIMARY",
                     "key_aliases": ["PDX_ALIAS"]}})
        monkeypatch.delenv("PDX_PRIMARY", raising=False)
        monkeypatch.setenv("PDX_ALIAS", "alias-key")
        assert pd.provider_api_key(prov) == "alias-key"
        monkeypatch.setenv("PDX_PRIMARY", "primary-key")
        assert pd.provider_api_key(prov) == "primary-key"

    def test_auth_headers_default_bearer(self):
        prov = {"auth": {"type": "env_var", "key": "K"},
                "extra_headers": {"X-Title": "Agent Friday"}}
        h = auth_headers(prov, api_key="sk-test")
        assert h["Authorization"] == "Bearer sk-test"
        assert h["X-Title"] == "Agent Friday"

    def test_auth_headers_custom_header_scheme(self):
        prov = {"auth": {"type": "env_var", "key": "K",
                         "header": "x-api-key", "scheme": ""}}
        h = auth_headers(prov, api_key="sk-test")
        assert h["x-api-key"] == "sk-test"
        assert "Authorization" not in h

    def test_extra_headers_cannot_inject_authorization(self):
        prov = {"auth": {"type": "none"},
                "extra_headers": {"Authorization": "Bearer stolen"}}
        h = auth_headers(prov, api_key=None)
        assert "Authorization" not in h


# ── Built-in descriptors ─────────────────────────────────────────────────────

class TestBuiltins:
    def test_expected_providers_present(self):
        names = {p["name"] for p in BUILTIN_EXTRA_PROVIDERS}
        assert {"openrouter", "huggingface", "groq", "together", "fireworks",
                "mistral", "deepseek", "xai", "perplexity", "cohere"} <= names

    def test_all_builtins_validate(self):
        for p in BUILTIN_EXTRA_PROVIDERS:
            ok, errors, _ = validate_descriptor(p)
            assert ok, f"{p['name']}: {errors}"

    def test_all_builtins_are_cloud(self):
        for p in BUILTIN_EXTRA_PROVIDERS:
            assert classification_of(normalize_descriptor(p)) == "cloud", p["name"]

    def test_openrouter_is_first_class(self):
        orr = next(p for p in BUILTIN_EXTRA_PROVIDERS if p["name"] == "openrouter")
        assert orr["enabled"] is True                       # T1, promoted
        assert orr["auth"]["key"] == "OPENROUTER_API_KEY"
        assert orr["discovery"]["mode"] == "api"
        assert orr["discovery"]["parser"] == "openrouter"
        assert orr["features"]["usage_accounting"] is True
        assert orr["features"]["fallback_models_param"] is True
        assert ":free" in orr["features"]["model_suffixes"]

    def test_huggingface_key_aliases(self):
        hf = next(p for p in BUILTIN_EXTRA_PROVIDERS if p["name"] == "huggingface")
        assert hf["enabled"] is False                       # T2 template
        keys = provider_env_keys(hf)
        assert "HF_TOKEN" in keys and "HUGGINGFACE_API_KEY" in keys

    def test_perplexity_is_static_with_models(self):
        px = next(p for p in BUILTIN_EXTRA_PROVIDERS if p["name"] == "perplexity")
        assert px["discovery"]["mode"] == "static"
        assert px["models"]  # statics-only cloud provider ships a real list


# ── resolve_model — the GAP-4 truth table ────────────────────────────────────

class TestResolveModel:
    """Registry-first resolution. Uses the REAL registry (built-ins) with the
    live-Ollama seam stubbed out."""

    @pytest.fixture(autouse=True)
    def _no_daemon(self, monkeypatch):
        monkeypatch.setattr(pd, "_live_ollama_has", lambda prov, mid: False)

    def test_empty_returns_none(self):
        assert resolve_model("") is None
        assert resolve_model(None) is None

    def test_claude_resolves_to_anthropic(self):
        prov, model = resolve_model("claude-sonnet-5")
        assert prov["name"] == "anthropic"
        assert model == "claude-sonnet-5"

    def test_gpt4o_resolves_to_openai(self):
        prov, model = resolve_model("gpt-4o")
        assert prov["name"] == "openai"

    def test_openrouter_org_model_resolves_to_aggregator(self):
        """THE GAP-4 case: an org/model id with a ':' variant suffix used to be
        classified local (Ollama) by the ':' heuristic."""
        prov, model = resolve_model("meta-llama/llama-4-maverick:free")
        assert prov["name"] == "openrouter"
        assert model == "meta-llama/llama-4-maverick:free"  # id passes through

    def test_openrouter_claude_id_resolves_to_openrouter(self):
        prov, _ = resolve_model("anthropic/claude-sonnet-5")
        assert prov["name"] == "openrouter"

    def test_ollama_tag_with_daemon(self, monkeypatch):
        monkeypatch.setattr(
            pd, "_live_ollama_has",
            lambda prov, mid: mid == "gemma3:4b"
            and pd.adapter_of(prov) == "ollama")
        prov, model = resolve_model("gemma3:4b")
        assert prov["name"] == "ollama-local"
        assert model == "gemma3:4b"

    def test_custom_named_local_model_resolves_local(self, monkeypatch):
        """A locally installed 'claude-x:latest' must resolve to Ollama (daemon
        truth) — never to Anthropic on the 'claude' prefix."""
        monkeypatch.setattr(pd, "_live_ollama_has",
                            lambda prov, mid: mid == "claude-x:latest")
        prov, _ = resolve_model("claude-x:latest")
        assert prov["name"] == "ollama-local"

    def test_explicit_provider_syntax(self):
        prov, model = resolve_model("openrouter::anthropic/claude-sonnet-5")
        assert prov["name"] == "openrouter"
        assert model == "anthropic/claude-sonnet-5"

    def test_explicit_unknown_provider_returns_none(self):
        assert resolve_model("nope::some-model") is None

    def test_static_model_of_template_provider(self, monkeypatch):
        """DeepSeek ships statics but is disabled by default — its ids resolve
        only once the provider is enabled."""
        from agent_friday.services.provider_registry import get_provider_registry
        reg = get_provider_registry()
        ds = reg.get_provider("deepseek")
        was = ds.get("enabled")
        try:
            ds["enabled"] = False
            assert resolve_model("deepseek-reasoner") is None or \
                resolve_model("deepseek-reasoner")[0]["name"] != "deepseek"
            ds["enabled"] = True
            prov, _ = resolve_model("deepseek-reasoner")
            assert prov["name"] == "deepseek"
        finally:
            ds["enabled"] = was

    def test_unknown_model_returns_none(self):
        assert resolve_model("totally-unknown-model-xyz") is None

    def test_gemini_resolves_to_google(self):
        prov, _ = resolve_model("gemini-2.5-pro")
        assert prov["name"] == "google-gemini"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

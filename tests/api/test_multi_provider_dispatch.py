"""Regression: multi-provider dispatch (GAP-3 fix).

_call_openai(provider=...) must hit THAT provider's base_url with THAT
provider's credentials — not the single global settings slot — so OpenRouter,
Groq, and a LAN vLLM can be used concurrently. Also covers the egress
local-bypass for verified-local OpenAI-compatible providers and the OpenRouter
first-class request features. Network stubbed at the HTTP seam.
"""
from __future__ import annotations

import pytest

import agent_friday.services.model_router as smr

pytestmark = pytest.mark.real_provider_paths


class _FakeResp:
    status_code = 200
    headers = {}

    def __init__(self, payload=None):
        self._payload = payload or {
            "choices": [{"message": {"role": "assistant", "content": "hi"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _capture_posts(monkeypatch):
    import requests
    posts = []

    def _fake_post(url, headers=None, json=None, timeout=None, **kw):
        # **kw absorbs `stream=True`: the transport streams by default and the
        # double must not constrain the wire options it is not asserting on.
        posts.append({"url": url, "headers": headers or {}, "payload": json,
                      "stream": bool(kw.get("stream"))})
        return _FakeResp()

    monkeypatch.setattr(requests, "post", _fake_post)
    return posts


def test_dispatch_to_openrouter_descriptor(monkeypatch):
    """provider='openrouter' → openrouter.ai base_url + OPENROUTER_API_KEY +
    etiquette headers from the descriptor + usage accounting."""
    posts = _capture_posts(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    text, trace = smr._call_openai(
        [{"role": "user", "content": "hello"}],
        model="meta-llama/llama-4-maverick", tools=None,
        provider="openrouter",
    )
    assert text == "hi"
    assert posts[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert posts[0]["headers"]["Authorization"] == "Bearer or-key-not-real"
    assert posts[0]["headers"]["X-Title"] == "Agent Friday"
    assert posts[0]["payload"]["usage"] == {"include": True}  # usage accounting
    assert posts[0]["payload"]["model"] == "meta-llama/llama-4-maverick"


def test_dispatch_to_groq_descriptor(monkeypatch):
    """A second provider in the SAME process hits ITS OWN endpoint — the
    single-slot limitation is gone."""
    posts = _capture_posts(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gq-key-not-real")
    from agent_friday.services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    groq = reg.get_provider("groq")
    was = groq.get("enabled")
    groq["enabled"] = True
    try:
        text, _ = smr._call_openai(
            [{"role": "user", "content": "hello"}],
            model="llama-3.3-70b-versatile", tools=None, provider="groq",
        )
        assert text == "hi"
        assert posts[0]["url"] == "https://api.groq.com/openai/v1/chat/completions"
        assert posts[0]["headers"]["Authorization"] == "Bearer gq-key-not-real"
        assert "usage" not in posts[0]["payload"]  # groq declares no accounting
    finally:
        groq["enabled"] = was


def test_missing_key_is_actionable(monkeypatch):
    _capture_posts(monkeypatch)
    for var in ("OPENROUTER_API_KEY", "OR_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError) as ei:
        smr._call_openai([{"role": "user", "content": "x"}],
                         model="m", tools=None, provider="openrouter")
    assert "OPENROUTER_API_KEY" in str(ei.value)


def test_legacy_single_slot_unchanged(monkeypatch):
    """provider=None keeps today's behavior byte-for-byte (settings slot)."""
    posts = _capture_posts(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")
    text, _ = smr._call_openai([{"role": "user", "content": "hello"}],
                               model="fake-cloud", tools=None)
    assert text == "hi"
    # Default settings slot points at api.openai.com.
    assert posts[0]["url"].endswith("/chat/completions")
    assert posts[0]["headers"]["Authorization"] == "Bearer legacy-key"
    assert posts[0]["headers"]["HTTP-Referer"] == "https://futurespeak.ai"


def test_local_provider_bypasses_egress_seal(monkeypatch):
    """A verified-local openai-compatible provider (LM Studio/vLLM on
    loopback) must NOT be sealed — parity with the Ollama path."""
    posts = _capture_posts(monkeypatch)
    sealed = {"called": False}
    real_seal = smr._seal_or_block

    def _spy_seal(payload, provider):
        sealed["called"] = True
        return real_seal(payload, provider)

    monkeypatch.setattr(smr, "_seal_or_block", _spy_seal)
    from agent_friday.services.provider_registry import get_provider_registry
    from agent_friday.routing.provider_descriptors import normalize_descriptor
    reg = get_provider_registry()
    reg._providers["lan-vllm"] = normalize_descriptor({
        "name": "lan-vllm", "type": "openai-compatible",
        "base_url": "http://127.0.0.1:8000/v1", "auth": {"type": "none"},
        "classification": "local", "models": ["local-model"], "enabled": True,
    })
    try:
        text, _ = smr._call_openai([{"role": "user", "content": "hello"}],
                                   model="local-model", tools=None,
                                   provider="lan-vllm")
        assert text == "hi"
        assert sealed["called"] is False
        assert posts[0]["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    finally:
        reg._providers.pop("lan-vllm", None)


def test_cloud_claiming_local_still_sealed(monkeypatch):
    """The same adapter with a PUBLIC base_url claiming 'local' must still go
    through the seal (call-time verification)."""
    posts = _capture_posts(monkeypatch)
    seal_calls = []
    real_seal = smr._seal_or_block

    def _spy_seal(payload, provider):
        seal_calls.append(provider)
        return real_seal(payload, provider)

    monkeypatch.setattr(smr, "_seal_or_block", _spy_seal)
    from agent_friday.services.provider_registry import get_provider_registry
    from agent_friday.routing.provider_descriptors import normalize_descriptor
    reg = get_provider_registry()
    reg._providers["fake-local"] = normalize_descriptor({
        "name": "fake-local", "type": "openai-compatible",
        "base_url": "https://api.collector.example-nonexistent-xyz.invalid/v1",
        "auth": {"type": "none"},
        "classification": "local",  # demoted at normalize; re-checked at call
        "models": ["m"], "enabled": True,
    })
    try:
        smr._call_openai([{"role": "user", "content": "hello"}],
                         model="m", tools=None, provider="fake-local")
        assert seal_calls == ["fake-local"]
    finally:
        reg._providers.pop("fake-local", None)


def test_fallback_models_param(monkeypatch):
    """OpenRouter server-side fallback: the models[] body param carries the
    chain when the descriptor declares the feature."""
    posts = _capture_posts(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")
    smr._call_openai(
        [{"role": "user", "content": "hello"}],
        model="meta-llama/llama-4-maverick", tools=None, provider="openrouter",
        fallback_models=["deepseek/deepseek-chat",
                         "meta-llama/llama-4-maverick"],
    )
    payload = posts[0]["payload"]
    assert payload["models"] == ["meta-llama/llama-4-maverick",
                                 "deepseek/deepseek-chat"]


def test_429_retry_after_honored(monkeypatch):
    """One polite wait on 429 with Retry-After, then success."""
    import requests

    calls = {"n": 0}
    slept = []

    class _Resp429:
        status_code = 429
        headers = {"Retry-After": "0.01"}
        def raise_for_status(self):
            raise requests.exceptions.HTTPError("429")
        def json(self):
            return {}

    def _fake_post(url, headers=None, json=None, timeout=None, **kw):
        calls["n"] += 1
        return _Resp429() if calls["n"] == 1 else _FakeResp()

    monkeypatch.setattr(requests, "post", _fake_post)
    monkeypatch.setattr(smr._time, "sleep", lambda s: slept.append(s))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")

    text, _ = smr._call_openai([{"role": "user", "content": "hello"}],
                               model="m", tools=None, provider="openrouter")
    assert text == "hi"
    assert calls["n"] == 2
    assert slept and slept[0] == pytest.approx(0.01)


def test_health_recorded_per_provider(monkeypatch):
    from agent_friday.services import provider_health as ph
    ph.reset_stats("openrouter")
    _capture_posts(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")
    smr._call_openai([{"role": "user", "content": "hello"}],
                     model="m", tools=None, provider="openrouter")
    s = ph.stats("openrouter")
    assert s["requests"] >= 1
    assert s["availability"] in ("ok", "degraded")
    ph.reset_stats("openrouter")


def test_cost_metered_under_registry_name(monkeypatch):
    _capture_posts(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")
    metered = []
    from agent_friday.services import cost_meter

    def _fake_meter(provider, model, usage, **kw):
        metered.append({"provider": provider, "model": model})
        return 0.0

    monkeypatch.setattr(cost_meter, "meter", _fake_meter)
    smr._call_openai([{"role": "user", "content": "hello"}],
                     model="meta-llama/llama-4-maverick", tools=None,
                     provider="openrouter")
    assert metered and metered[0]["provider"] == "openrouter"

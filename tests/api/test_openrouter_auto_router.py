"""OpenRouter Auto Router — "let Friday decide", and the wire format for it.

Stephen, 2026-08-30: "Let Friday decide the model (OpenRouter required) based
upon task complexity and the user's cost priority settings."

The cost priority IS OpenRouter's `cost_tier`. It rides a `plugins` entry;
a top-level `cost_tier` is accepted and ignored, so a wrong nesting here does
not fail loudly — it just pins every turn to the default tier forever.
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


def _capture(monkeypatch, resp=None):
    import requests
    posts = []

    def _fake_post(url, headers=None, json=None, timeout=None, **kw):
        posts.append({"url": url, "payload": json,
                      "stream": bool(kw.get("stream"))})
        return resp or _FakeResp()

    monkeypatch.setattr(requests, "post", _fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-not-real")
    return posts


def _settings(monkeypatch, tier):
    monkeypatch.setattr(smr, "_load_settings",
                        lambda: {"model_routing": {
                            "auto_router_cost_tier": tier}})


def test_cost_tier_rides_the_plugins_entry(monkeypatch):
    posts = _capture(monkeypatch)
    _settings(monkeypatch, "high")
    smr._call_openai([{"role": "user", "content": "hello"}],
                     model="openrouter/auto", tools=None, provider="openrouter")
    payload = posts[0]["payload"]
    assert payload["model"] == "openrouter/auto"
    assert payload["plugins"] == [{"id": "auto-router", "cost_tier": "high"}]
    # Not the shape that silently no-ops.
    assert "cost_tier" not in payload


def test_default_tier_is_low_and_bad_values_do_not_reach_the_wire(monkeypatch):
    """Turning the feature on must not silently raise spend, and a typo in
    settings must not 400 every turn."""
    assert smr.auto_router_cost_tier({}) == "low"
    assert smr.auto_router_cost_tier(
        {"model_routing": {"auto_router_cost_tier": "LUDICROUS"}}) == "low"
    assert smr.auto_router_cost_tier(
        {"model_routing": {"auto_router_cost_tier": "XHigh"}}) == "xhigh"


def test_plugins_only_for_the_auto_router(monkeypatch):
    posts = _capture(monkeypatch)
    _settings(monkeypatch, "max")
    smr._call_openai([{"role": "user", "content": "hello"}],
                     model="z-ai/glm-5.3", tools=None, provider="openrouter")
    assert "plugins" not in posts[0]["payload"]


def test_streaming_is_on_by_default(monkeypatch):
    posts = _capture(monkeypatch)
    smr._call_openai([{"role": "user", "content": "hello"}],
                     model="z-ai/glm-5.3", tools=None, provider="openrouter")
    assert posts[0]["stream"] is True
    assert posts[0]["payload"]["stream"] is True


def test_stream_fallback_does_not_eat_429_retry_after(monkeypatch):
    """Regression: the stream fallback must trigger on 400 ONLY.

    A blanket `>= 400` fallback re-sent the request immediately on 429,
    throwing away the Retry-After wait — turning rate-limit etiquette into
    unthrottled hammering, on the one status where that is worst.
    """
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

    text, _ = smr._call_openai([{"role": "user", "content": "hi"}],
                               model="openrouter/auto", tools=None,
                               provider="openrouter")
    assert text == "hi"
    assert slept and slept[0] == pytest.approx(0.01), (
        "429 Retry-After was skipped — the stream fallback swallowed it")

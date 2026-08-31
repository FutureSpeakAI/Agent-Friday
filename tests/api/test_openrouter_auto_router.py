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


# ── The reasoning-token trap ─────────────────────────────────────────────────

def test_auto_router_floors_max_tokens_so_reasoning_cannot_starve_the_answer(
        monkeypatch):
    """Measured 2026-08-30 against the live account: `openrouter/auto` with
    max_tokens=20 routed to deepseek-v4-flash-0731, spent all 20 tokens on
    `reasoning` deltas, and returned finish_reason="length" with content "".
    A billed turn that said nothing. Which model answers is not knowable when
    the caller sets the budget, so the floor lives here."""
    posts = _capture(monkeypatch)
    _settings(monkeypatch, "low")
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="openrouter/auto", tools=None, provider="openrouter",
                     max_tokens=20)
    assert posts[0]["payload"]["max_tokens"] == smr.AUTO_ROUTER_MIN_MAX_TOKENS


def test_a_generous_budget_is_left_alone(monkeypatch):
    posts = _capture(monkeypatch)
    _settings(monkeypatch, "low")
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="openrouter/auto", tools=None, provider="openrouter",
                     max_tokens=4096)
    assert posts[0]["payload"]["max_tokens"] == 4096


def test_the_floor_does_not_touch_explicitly_chosen_models(monkeypatch):
    """Only the Auto Router picks the model behind your back."""
    posts = _capture(monkeypatch)
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="z-ai/glm-5.3", tools=None, provider="openrouter",
                     max_tokens=20)
    assert posts[0]["payload"]["max_tokens"] == 20


# ── Spend ceiling (the band cost_tier has no word for: free) ─────────────────

def test_no_price_band_configured_sends_no_constraint(monkeypatch):
    posts = _capture(monkeypatch)
    monkeypatch.setattr(smr, "_load_settings", lambda: {"model_routing": {}})
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="z-ai/glm-5.3", tools=None, provider="openrouter")
    assert "provider" not in posts[0]["payload"], \
        "default-off means the wire is untouched"


def test_free_band_sends_a_zero_ceiling_in_per_million_units(monkeypatch):
    posts = _capture(monkeypatch)
    monkeypatch.setattr(smr, "_load_settings", lambda: {
        "model_routing": {"openrouter_price_band": "free"}})
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="z-ai/glm-5.3", tools=None, provider="openrouter")
    prov = posts[0]["payload"]["provider"]
    assert prov["max_price"] == {"prompt": 0, "completion": 0}
    assert prov["sort"] == "price"


def test_an_unknown_band_constrains_nothing(monkeypatch):
    """A typo must not silently narrow which models may answer."""
    assert smr.openrouter_price_ceiling(
        {"model_routing": {"openrouter_price_band": "bargain-bin"}}) is None


def test_price_ceiling_is_not_sent_to_non_aggregators(monkeypatch):
    """Only an aggregator routes across endpoints, so only it can honour one."""
    posts = _capture(monkeypatch)
    monkeypatch.setattr(smr, "_load_settings", lambda: {
        "model_routing": {"openrouter_price_band": "free"}})
    monkeypatch.setenv("GROQ_API_KEY", "gk-not-real")
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="llama-3.3-70b", tools=None, provider="groq")
    assert "provider" not in posts[0]["payload"]


def test_free_ceiling_is_never_sent_with_the_auto_router(monkeypatch):
    """Verified against the live API 2026-08-30: `openrouter/auto` with
    max_price {0,0} is HTTP 404 "No endpoints found that satisfy the max
    price" — the router's pool holds no zero-price endpoint. Sending it turns
    a spend preference into a dead turn."""
    posts = _capture(monkeypatch)
    monkeypatch.setattr(smr, "_load_settings", lambda: {"model_routing": {
        "auto_router_cost_tier": "low", "openrouter_price_band": "free"}})
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="openrouter/auto", tools=None, provider="openrouter")
    assert "provider" not in posts[0]["payload"]


def test_free_ceiling_IS_sent_for_an_explicitly_chosen_model(monkeypatch):
    """The same ceiling on a model the user picked is honoured — a :free
    model answers with it applied."""
    posts = _capture(monkeypatch)
    monkeypatch.setattr(smr, "_load_settings", lambda: {"model_routing": {
        "openrouter_price_band": "free"}})
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="inclusionai/ling-3.0-flash-fin:free", tools=None,
                     provider="openrouter")
    assert posts[0]["payload"]["provider"]["max_price"] == {
        "prompt": 0, "completion": 0}


def test_no_ceiling_rides_with_the_auto_router_even_a_satisfiable_one(
        monkeypatch):
    """`cost_tier=high` + a cheap ceiling is HTTP 404 against the live API:
    the tier asks for expensive models and the ceiling forbids them. Which
    pairs conflict depends on live pricing, so it cannot be predicted before
    sending — the ceiling simply never rides with auto."""
    posts = _capture(monkeypatch)
    monkeypatch.setattr(smr, "_load_settings", lambda: {"model_routing": {
        "auto_router_cost_tier": "high", "openrouter_price_band": "cheap"}})
    smr._call_openai([{"role": "user", "content": "hi"}],
                     model="openrouter/auto", tools=None, provider="openrouter")
    assert "provider" not in posts[0]["payload"]
    assert posts[0]["payload"]["plugins"] == [
        {"id": "auto-router", "cost_tier": "high"}]

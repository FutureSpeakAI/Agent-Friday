"""A1 — the inverted test default (decision D9).

These tests assert the property the inversion buys: with no marker and no
opt-in, a chat request executes the REAL provider body, so the payload that
body assembles is observable and assertable.

Under the old default every one of these would fail: `_call_claude` was
replaced wholesale by `lambda *a, **k: CANNED_TEXT`, so no client was ever
touched, no payload was ever built, and `offline_calls` would stay empty.
"""
from __future__ import annotations

import pytest

from agent_friday.services import model_router
from tests.conftest import CANNED_TEXT


def test_real_call_claude_body_runs_and_builds_a_payload(offline_calls):
    """The real _call_claude assembles kwargs and parses resp.content."""
    out = model_router._call_claude(
        [{"role": "user", "content": "hello"}],
        system="be brief",
        model="claude-sonnet-5",
        max_tokens=256,
    )

    # Response parsing is real: text came from resp.content blocks.
    assert out == CANNED_TEXT

    # Payload assembly is real and now observable.
    assert len(offline_calls["anthropic"]) == 1
    sent = offline_calls["anthropic"][0]
    assert sent["model"] == "claude-sonnet-5"
    assert sent["max_tokens"] == 256
    assert sent["system"] == "be brief"
    assert sent["messages"][0]["content"] == "hello"
    # temperature is deliberately NOT forwarded (model_router.py:144-153).
    assert "temperature" not in sent


def test_provider_functions_are_not_stubbed_by_default():
    """The default no longer replaces provider functions with lambdas."""
    for name in ("_call_claude", "_call_openai", "_generate_text"):
        fn = getattr(model_router, name)
        assert fn.__module__.startswith("agent_friday"), (
            f"{name} is stubbed by default — the D9 inversion regressed")
        assert fn.__name__ == name


def test_unmocked_url_is_blocked_not_dialled():
    """Anything without a double raises instead of reaching the network."""
    import requests

    from tests.fake_backends import BlockedNetworkCall

    with pytest.raises(BlockedNetworkCall):
        requests.post("https://api.example.invalid/v1/something", json={})


def test_ollama_transport_double_serves_the_daemon_endpoints():
    """The Ollama wire is faked, so _call_ollama's real body has a backend."""
    from agent_friday.routing.ollama_manager import OllamaManager

    mgr = OllamaManager("http://localhost:11434")
    assert mgr.is_available() is True
    names = [m["name"] for m in mgr.list_models()]
    assert "gemma4:e4b" in names


def test_chat_route_still_returns_canned_text(client, assert_reachable):
    """The inversion widens coverage without rewriting existing assertions."""
    resp = client.post("/api/chat", json={"message": "hi"})
    assert assert_reachable(resp)

"""Regression: the shared OpenAI-format agentic loop must be reachable from
services/model_router.py.

_oai_agentic_loop is defined in services/agent.py — an UPPER layer of the
star-import chain — so a bare reference inside _call_ollama/_call_openai
resolves to nothing at call time and every local (Ollama) inference and every
OpenAI-compatible call died with:

    NameError: name '_oai_agentic_loop' is not defined

masked in production by _generate_agent's silent fallback to the Anthropic
cloud path. These tests call the services-module functions DIRECTLY (the
conftest LLM kill-switch patches the server namespace, not the defining
module), with the network stubbed at the HTTP/manager seam, so the real loop
runs end-to-end.
"""
from __future__ import annotations

import pytest

import services.model_router as smr

# Opt out of the autouse LLM kill-switch: these tests run the REAL loop with
# the network stubbed at the manager/HTTP seam.
pytestmark = pytest.mark.real_provider_paths


class _FakeOllamaManager:
    base_url = "http://localhost:11434"

    def __init__(self):
        self.calls = []

    def is_available(self):
        return True

    def chat_completion(self, messages, model, tools=None, temperature=0.7,
                        max_tokens=4096):
        self.calls.append({"messages": list(messages), "tools": tools})
        return {
            "choices": [{
                "message": {"role": "assistant", "content": "local says hi"},
                "finish_reason": "stop",
            }],
            "model": model,
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }


def test_call_ollama_single_shot_reaches_shared_loop(monkeypatch):
    """tools=None: one round through _oai_agentic_loop, no NameError."""
    import ollama_manager
    fake = _FakeOllamaManager()
    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **k: fake)

    text, trace = smr._call_ollama(
        [{"role": "user", "content": "hello"}],
        system="be brief", model="fake-local", tools=None,
    )
    assert text == "local says hi"
    assert trace == []
    # The system prompt must be the first message of the converted convo.
    assert fake.calls[0]["messages"][0] == {"role": "system", "content": "be brief"}


def test_call_ollama_with_tools_reaches_shared_loop(monkeypatch):
    """tools=[…]: the agentic branch (model_router.py:223) must not NameError."""
    import ollama_manager
    fake = _FakeOllamaManager()
    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **k: fake)

    anthropic_tool = {
        "name": "read_file",
        "description": "Read a file.",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]},
    }
    text, trace = smr._call_ollama(
        [{"role": "user", "content": "hello"}],
        model="fake-local", tools=[anthropic_tool],
    )
    assert text == "local says hi"
    assert trace == []
    # The Anthropic schema must have been converted to OpenAI function format.
    sent_tools = fake.calls[0]["tools"]
    assert sent_tools and sent_tools[0]["function"]["name"] == "read_file"


def test_generate_text_normalizes_string_prompts(monkeypatch):
    """A bare prompt string must become a proper message list — it used to
    crash the router consult and then every provider primitive."""
    captured = {}

    def _fake_claude(messages, system=None, model=None, **kw):
        captured["messages"] = messages
        return "normalized fine"

    monkeypatch.setattr(smr, "_call_claude", _fake_claude)
    monkeypatch.setattr(smr, "get_anthropic_client",
                        lambda *a, **k: object(), raising=False)
    import services.demo_mode as _dm
    monkeypatch.setattr(_dm, "is_demo", lambda: False)

    out = smr._generate_text("just a bare prompt")
    assert out == "normalized fine"
    assert captured["messages"] == [{"role": "user", "content": "just a bare prompt"}]


def test_call_openai_reaches_shared_loop(monkeypatch):
    """The OpenAI-compatible path (model_router.py:331) must not NameError."""
    import requests

    class _FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {"role": "assistant", "content": "cloud says hi"},
                    "finish_reason": "stop",
                }],
                "usage": {},
            }

    posts = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        posts.append({"url": url, "payload": json})
        return _FakeResp()

    monkeypatch.setattr(requests, "post", _fake_post)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    text, trace = smr._call_openai(
        [{"role": "user", "content": "hello"}],
        system="be brief", model="fake-cloud", tools=None,
    )
    assert text == "cloud says hi"
    assert trace == []
    assert posts and posts[0]["url"].endswith("/chat/completions")

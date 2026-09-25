"""One key is enough: a cloud install with only Anthropic, or only OpenRouter.

Every path that thinks must work with exactly one of the two keys:

* OpenRouter only: the router, chat, `_generate_text`, `_generate_agent`, the
  bare `_call_claude` / `_call_claude_agent` primitives and the setup chat's
  reader all reach OpenRouter with the same Claude model under its OpenRouter
  id, instead of refusing or raising for want of an Anthropic key.
* Anthropic only: the default decisions stay native, and an
  `anthropic/claude-*` seat bound to OpenRouter is served natively.

The wire is the suite's offline transport (tests/api/conftest.py): Anthropic
is the fake SDK client, OpenRouter is `requests.post`. Fake keys are built at
runtime so no literal here has a key's shape.
"""
from __future__ import annotations

import pytest

import agent_friday.core as core

FAKE_OR = "sk-" + "or-" + "v1-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp"   # pragma: allowlist secret

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _settings(mode="cloud_only"):
    s = dict(core.DEFAULT_SETTINGS)
    s["model_routing"] = dict(s.get("model_routing") or {})
    s["model_routing"]["mode"] = mode
    return s


@pytest.fixture
def openrouter_only(monkeypatch, patch_app):
    """No Anthropic key anywhere; an OpenRouter key in the environment."""
    patch_app("get_anthropic_client", lambda *a, **k: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OR)
    patch_app("_load_settings", lambda *a, **k: _settings())
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "get_provider_key", lambda name: None)
    import agent_friday.services.demo_mode as dm
    monkeypatch.setattr(dm, "is_demo", lambda *a, **k: False)


@pytest.fixture
def anthropic_only(monkeypatch, patch_app):
    """The suite's fake Anthropic client; no OpenRouter key anywhere."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OR_API_KEY", raising=False)
    patch_app("_load_settings", lambda *a, **k: _settings())
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "get_provider_key", lambda name: None)
    import agent_friday.services.demo_mode as dm
    monkeypatch.setattr(dm, "is_demo", lambda *a, **k: False)


def _openrouter_posts(offline_calls):
    return [c for c in offline_calls["requests"] if c["url"] == OPENROUTER_URL]


def _route(has_tools=True, model=None):
    from agent_friday.routing.model_router import get_router
    return get_router(_settings()["model_routing"]).route(
        [{"role": "user", "content": "what's on today?"}],
        task_context={"has_tools": has_tools,
                      "cloud_model": model or "claude-sonnet-5"})


# ── OpenRouter only ──────────────────────────────────────────────────────────

def test_router_serves_claude_through_openrouter(openrouter_only):
    r = _route()
    assert r["provider"] == "openai", r
    assert r["provider_name"] == "openrouter", r
    assert r["model"] == "anthropic/claude-sonnet-5", r
    assert r["is_local"] is False and r["scrub_pii"] is True


def test_generate_text_reaches_openrouter(openrouter_only, offline_calls):
    from agent_friday.services.model_router import _generate_text
    out = _generate_text([{"role": "user", "content": "write the briefing"}],
                         system="brief", orb_label="Daily Briefing",
                         workspace="briefing")
    assert out and out.strip()
    posts = _openrouter_posts(offline_calls)
    assert posts, offline_calls["requests"]
    assert posts[0]["json"]["model"] == "anthropic/claude-sonnet-5"
    assert offline_calls["anthropic"] == []


def test_generate_agent_runs_tools_through_openrouter(openrouter_only, offline_calls):
    from agent_friday.services.agent import _generate_agent
    text, _trace = _generate_agent([{"role": "user", "content": "check my calendar"}],
                                   system="s", session_ctx={"is_background_task": True})
    assert text and text.strip()
    posts = _openrouter_posts(offline_calls)
    assert posts, offline_calls["requests"]
    assert posts[0]["json"]["model"] == "anthropic/claude-sonnet-5"
    assert posts[0]["json"].get("tools"), "the tool registry must travel"


def test_bare_call_claude_falls_through_to_openrouter(openrouter_only, offline_calls):
    from agent_friday.services.model_router import _call_claude
    out = _call_claude([{"role": "user", "content": "one word"}], system="s",
                       model="claude-haiku-4-5-20251001")
    assert out and out.strip()
    posts = _openrouter_posts(offline_calls)
    assert posts and posts[0]["json"]["model"] == "anthropic/claude-haiku-4.5"


def test_bare_call_claude_agent_falls_through_to_openrouter(openrouter_only,
                                                            offline_calls):
    from agent_friday.services.agent import _call_claude_agent
    text, _trace = _call_claude_agent([{"role": "user", "content": "hi"}],
                                      system="s", model="claude-opus-5-5")
    assert text and text.strip()
    posts = _openrouter_posts(offline_calls)
    assert posts and posts[0]["json"]["model"] == "anthropic/claude-opus-5.5"
    assert posts[0]["json"].get("tools")


def test_chat_in_cloud_only_answers_with_only_an_openrouter_key(
        client, openrouter_only, offline_calls):
    d = client.post("/api/chat", json={"message": "hello Friday"}).get_json()
    assert not d.get("cloud_only_no_key"), d.get("response")
    assert _openrouter_posts(offline_calls), offline_calls["requests"]


def test_setup_reader_offers_openrouter(openrouter_only, monkeypatch):
    from agent_friday.services import setup_reader
    monkeypatch.setattr(setup_reader, "local_model", lambda: None)
    opt = setup_reader.cloud_model()
    assert opt == {"model": "anthropic/claude-sonnet-5", "provider": "OpenRouter"}


def test_setup_reader_reads_through_openrouter(openrouter_only, offline_calls):
    from agent_friday.services import setup_reader
    reader = {"kind": "cloud", "model": "anthropic/claude-sonnet-5",
              "provider": "OpenRouter"}
    setup_reader.call_json(reader, "Return JSON.", "answers")
    posts = _openrouter_posts(offline_calls)
    assert posts and posts[0]["json"]["model"] == "anthropic/claude-sonnet-5"


def test_with_neither_key_nothing_is_substituted(monkeypatch, patch_app):
    patch_app("get_anthropic_client", lambda *a, **k: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(cs, "get_provider_key", lambda name: None)
    r = _route()
    assert r["provider"] == "cloud" and r["model"] == "claude-sonnet-5"


# ── Anthropic only ───────────────────────────────────────────────────────────

def test_anthropic_only_keeps_the_native_route(anthropic_only):
    r = _route()
    assert r["provider"] == "cloud" and r["model"] == "claude-sonnet-5"


def test_anthropic_only_serves_an_openrouter_claude_seat_natively(anthropic_only):
    from agent_friday.services.one_key import substitute_route
    r = substitute_route({"provider": "openai", "provider_name": "openrouter",
                          "model": "anthropic/claude-opus-5.5"})
    assert r["provider"] == "cloud" and r["model"] == "claude-opus-5-5"


def test_anthropic_only_generate_text_stays_native(anthropic_only, offline_calls):
    from agent_friday.services.model_router import _generate_text
    out = _generate_text([{"role": "user", "content": "write the briefing"}],
                         system="brief", workspace="briefing")
    assert out and out.strip()
    assert offline_calls["anthropic"], "the native client must serve it"
    assert not _openrouter_posts(offline_calls)

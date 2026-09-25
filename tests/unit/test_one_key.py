"""The id mapping between Anthropic and OpenRouter, and the one-key status.

OpenRouter serves Claude under its own ids: a vendor prefix, a dot where
Anthropic's version has a dash, and no date suffix. The expected ids below
are the ones OpenRouter's live catalogue lists.
"""
from __future__ import annotations

import pytest

from agent_friday.services import one_key


@pytest.mark.parametrize("native, routed", [
    ("claude-sonnet-5", "anthropic/claude-sonnet-5"),
    ("claude-opus-5-5", "anthropic/claude-opus-5.5"),
    ("claude-opus-5", "anthropic/claude-opus-5"),
    ("claude-fable-5-1", "anthropic/claude-fable-5.1"),
    ("claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5"),
])
def test_claude_ids_map_to_openrouter_ids(native, routed):
    assert one_key.openrouter_id_for(native) == routed


@pytest.mark.parametrize("routed, native", [
    ("anthropic/claude-sonnet-5", "claude-sonnet-5"),
    ("anthropic/claude-opus-5.5", "claude-opus-5-5"),
    ("anthropic/claude-haiku-4.5", "claude-haiku-4-5"),
])
def test_openrouter_claude_ids_map_back(routed, native):
    assert one_key.anthropic_id_for(routed) == native


@pytest.mark.parametrize("mid", ["anthropic/claude-opus-5.5:batch",
                                 "meta-llama/llama-4-maverick", "gpt-4o", ""])
def test_ids_with_no_native_twin_are_left_alone(mid):
    assert one_key.anthropic_id_for(mid) is None


def test_a_prefixed_id_is_not_prefixed_twice():
    assert one_key.openrouter_id_for("anthropic/claude-sonnet-5") == \
        "anthropic/claude-sonnet-5"


def test_a_non_claude_id_falls_back_to_the_default_claude_model():
    assert one_key.openrouter_id_for("gemma4:e4b").startswith("anthropic/claude-")


def test_status_names_the_key_in_use_and_says_it_is_enough(monkeypatch):
    monkeypatch.setattr(one_key, "anthropic_ready", lambda: False)
    monkeypatch.setattr(one_key, "openrouter_ready", lambda: True)
    s = one_key.status()
    assert s["in_use"] == "openrouter" and s["sufficient"] is True
    assert "OpenRouter" in s["line"] and "enough" in s["line"]
    assert [lnk["url"] for lnk in s["links"]] == [
        "https://console.anthropic.com/settings/keys", "https://openrouter.ai/keys"]


def test_status_with_no_key_says_one_is_enough_and_what_is_missing(monkeypatch):
    monkeypatch.setattr(one_key, "anthropic_ready", lambda: False)
    monkeypatch.setattr(one_key, "openrouter_ready", lambda: False)
    s = one_key.status()
    assert s["in_use"] is None and s["sufficient"] is False
    assert "One key is enough" in s["line"]


def test_anthropic_wins_when_both_keys_exist(monkeypatch):
    monkeypatch.setattr(one_key, "anthropic_ready", lambda: True)
    monkeypatch.setattr(one_key, "openrouter_ready", lambda: True)
    assert one_key.key_in_use() == "anthropic"
    r = one_key.substitute_route({"provider": "cloud", "model": "claude-sonnet-5"})
    assert r["provider"] == "cloud" and r["model"] == "claude-sonnet-5"

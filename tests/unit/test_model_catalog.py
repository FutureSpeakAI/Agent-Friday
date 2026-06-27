"""Model catalog + provider-family inference.

The catalog is the single source of truth for the UI model picker. These tests
pin the contract the frontend relies on: role grouping, presentation metadata,
availability, the recalled-model exclusion, and the corrected Gemini lineup.
"""
from __future__ import annotations

import os

from agent_friday.routing.model_router import provider_family
from agent_friday.services.model_catalog import build_catalog


def test_provider_family_inference():
    assert provider_family("claude-opus-4-8") == "anthropic"
    assert provider_family("claude-sonnet-4-6") == "anthropic"
    assert provider_family("gpt-4o") == "openai"
    assert provider_family("gpt-4o-mini") == "openai"
    assert provider_family("o3") == "openai"
    assert provider_family("gemini-2.5-flash") == "gemini"
    assert provider_family("gemini-nano-banana-2") == "gemini"
    assert provider_family("veo-3") == "gemini"
    assert provider_family("gemma4:latest") == "local"
    assert provider_family("llama3.1:8b") == "local"
    assert provider_family("") is None
    assert provider_family(None) is None


def test_catalog_shape():
    cat = build_catalog()
    assert set(cat.keys()) >= {"roles", "models", "providers"}
    for role in ("orchestrator", "subagent", "creative", "voice"):
        assert role in cat["roles"]
    # Every entry carries the fields the UI renders.
    for e in cat["models"]:
        for k in ("id", "label", "short", "provider", "provider_label",
                  "roles", "modalities", "local", "available"):
            assert k in e, f"entry missing {k}: {e}"


def test_orchestrator_spans_providers_not_just_cloud():
    """The whole point: orchestrator/subagent are no longer Claude-only."""
    cat = build_catalog()
    orch_providers = {e["provider"] for e in cat["roles"]["orchestrator"]}
    # Anthropic, OpenAI, and local Ollama all offer the agent roles.
    assert "anthropic" in orch_providers
    assert "openai" in orch_providers
    assert "ollama-local" in orch_providers
    # Same pool backs subagent.
    sub_providers = {e["provider"] for e in cat["roles"]["subagent"]}
    assert {"anthropic", "openai", "ollama-local"} <= sub_providers


def test_recalled_models_absent():
    cat = build_catalog()
    ids = " ".join(e["id"] + e["label"] for e in cat["models"]).lower()
    assert "fable" not in ids
    assert "mythos" not in ids


def test_creative_holds_image_and_video_models_only():
    """Creative = image (Nano Banana Pro / 2) + video (Veo). Flash is voice and
    Pro is text — neither belongs here."""
    cat = build_catalog()
    creative_ids = [e["id"] for e in cat["roles"]["creative"]]
    # The image + video generation models ARE creative.
    assert "gemini-nano-banana-pro" in creative_ids
    assert "gemini-nano-banana-2" in creative_ids
    assert "veo-3" in creative_ids
    # Flash (voice) and Pro (text) must NOT be classified creative.
    assert "gemini-2.5-flash" not in creative_ids
    assert "gemini-2.5-pro" not in creative_ids
    # Voice-only live models never leak into creative.
    assert "gemini-3.1-flash-live-preview" not in creative_ids
    # No duplicate ids within the role.
    assert len(creative_ids) == len(set(creative_ids))


def test_voice_role_holds_flash_and_live_models():
    cat = build_catalog()
    voice_ids = [e["id"] for e in cat["roles"]["voice"]]
    # Gemini 2.5 Flash is the Gemini Live voice model.
    assert "gemini-2.5-flash" in voice_ids
    assert "gemini-3.1-flash-live-preview" in voice_ids
    assert "gemini-2.5-flash-native-audio-preview-12-2025" in voice_ids
    # Text + image/video models never leak into voice.
    assert "gemini-2.5-pro" not in voice_ids
    assert "gemini-nano-banana-2" not in voice_ids
    assert "veo-3" not in voice_ids


def test_gemini_pro_is_a_text_reasoning_model():
    """Gemini 2.5 Pro serves the agent (text/reasoning) roles, not creative."""
    cat = build_catalog()
    orch_ids = [e["id"] for e in cat["roles"]["orchestrator"]]
    sub_ids = [e["id"] for e in cat["roles"]["subagent"]]
    assert "gemini-2.5-pro" in orch_ids
    assert "gemini-2.5-pro" in sub_ids


def test_availability_reflects_env_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Rebuild a fresh registry so the env change is observed.
    import agent_friday.services.provider_registry as pr
    pr._registry = None
    cat = build_catalog()
    by_provider = {}
    for e in cat["models"]:
        by_provider.setdefault(e["provider"], e["available"])
    assert by_provider.get("anthropic") is True
    assert by_provider.get("openai") is False
    pr._registry = None  # don't leak state to other tests

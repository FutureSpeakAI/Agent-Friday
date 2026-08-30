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
    assert "anthropic" in orch_providers
    assert "openai" in orch_providers
    # Same pool backs subagent.
    sub_providers = {e["provider"] for e in cat["roles"]["subagent"]}
    assert {"anthropic", "openai"} <= sub_providers


def test_local_seats_cover_the_agent_roles_when_they_exist(monkeypatch):
    """Local coverage comes from the seats Friday actually serves.

    It used to come from `ollama-local`'s hardcoded fallback, which named
    models on a daemon that is stopped — so the picker offered `gemma4:latest`
    (not installed, not even a real tag) while the real seat answered two ports
    away. The residency plan is the source now.
    """
    import agent_friday.services.model_catalog as mc

    class _Arb:
        plan = {"seats": {
            "interactive_brain": {"model_id": "gemma4:12b", "device": "gpu:0",
                                  "num_ctx": 131072, "status": "pinned"},
            "heavy_hitter": {"model_id": "gemma4:26b", "device": "gpu:0+cpu",
                             "num_ctx": 32768, "status": "leased"},
            # Not language seats — these must NOT reach a model picker.
            "stt": {"model_id": "faster-whisper", "device": "cpu"},
            "image": {"model_id": "z-image-turbo-fp8", "device": "gpu:0"},
        }}

    import agent_friday.services.residency_arbiter as ra
    monkeypatch.setattr(ra, "get_arbiter", lambda: _Arb())
    cat = mc.build_catalog()
    orch = cat["roles"]["orchestrator"]
    ids = {e["id"] for e in orch}
    assert {"gemma4:12b", "gemma4:26b"} <= ids
    assert "faster-whisper" not in ids
    seats = [e for e in orch if e["provider"] == "arbiter-local"]
    # Local is local. Every one of these used to render with a cloud badge.
    assert seats and all(e["local"] and e["classification"] == "local"
                         for e in seats)


def test_recalled_models_absent():
    # Mythos 5 was never shipped. Fable 5 is now a live model (added v5.1).
    cat = build_catalog()
    ids = " ".join(e["id"] + e["label"] for e in cat["models"]).lower()
    assert "mythos" not in ids


def test_catalog_includes_sonnet5_and_fable5():
    cat = build_catalog()
    ids = [e["id"] for e in cat["models"]]
    assert "claude-sonnet-5" in ids
    assert "claude-fable-5" in ids


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


def test_gemini_pro_not_offered_without_text_dispatch():
    """Gemini 2.5 Pro is a text model, but routing/model_router.py has no
    Gemini text/agentic dispatch (_apply_cloud_provider only retags
    anthropic/openai/local) — a picked gemini-2.5-pro would silently run on a
    different model. Never offer what can't dispatch: it stays out of every
    picker role until a Gemini text path lands, at which point this test
    should flip back to asserting membership."""
    cat = build_catalog()
    orch_ids = [e["id"] for e in cat["roles"]["orchestrator"]]
    sub_ids = [e["id"] for e in cat["roles"]["subagent"]]
    creative_ids = [e["id"] for e in cat["roles"]["creative"]]
    assert "gemini-2.5-pro" not in orch_ids
    assert "gemini-2.5-pro" not in sub_ids
    assert "gemini-2.5-pro" not in creative_ids


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


# ── Picker-hygiene regression suite ──────────────────────────────────────────
# Bug report: "tons of models, many in there twice, some grayed out." Root
# cause: live-installed Ollama models were merged into EVERY local-typed
# provider (ollama + local-voice + nemo-local), so each installed model showed
# three times in the agent roles — with the NeMo copies greyed out. These pin
# the fix: single-owner Ollama merge, deduped role lists, engine backends out
# of the picker, and key hints on unavailable entries.

def test_no_duplicate_model_ids_within_any_role():
    cat = build_catalog()
    for role, entries in cat["roles"].items():
        ids = [e["id"] for e in entries]
        assert len(ids) == len(set(ids)), f"duplicate ids in role {role}: {ids}"


def test_live_ollama_models_only_merge_into_ollama_provider(monkeypatch):
    import agent_friday.services.model_catalog as mc
    monkeypatch.setattr(mc, "_live_ollama_models", lambda _base: ["fake-live:latest"])
    cat = mc.build_catalog()
    owners = {e["provider"] for e in cat["models"] if e["id"] == "fake-live:latest"}
    assert owners == {"ollama-local"}, f"live model leaked into: {owners}"
    orch = [e for e in cat["roles"]["orchestrator"] if e["id"] == "fake-live:latest"]
    assert len(orch) == 1
    assert orch[0]["available"] is True


def test_a_stopped_ollama_daemon_names_no_models(monkeypatch):
    """A daemon that is not running has no models, and saying otherwise is
    invention — this test used to assert the opposite.

    2026-08-16, Stephen: "listing Gemma4 as a cloud model, and as an Ollama
    model (it is neither)." The Ollama entry came from this static fallback,
    surviving on a daemon that has been retired; the cloud badge came from the
    UI testing a `classification` field the API never sent. The provider row
    still appears so the hint is readable — it just stops naming models it does
    not have.
    """
    import agent_friday.services.model_catalog as mc
    monkeypatch.setattr(mc, "_live_ollama_models", lambda _base: None)
    cat = mc.build_catalog()
    assert [e for e in cat["models"] if e["provider"] == "ollama-local"] == []
    prov = [p for p in cat["providers"] if p.get("name") == "ollama-local"]
    assert prov, "the provider itself stays listed so its hint is visible"


def test_every_model_states_its_classification():
    """The UI must never have to infer this again."""
    cat = build_catalog()
    for e in cat["models"]:
        assert e.get("classification") in ("local", "cloud"), e["id"]
        assert e["classification"] == ("local" if e.get("local") else "cloud")


def test_voice_role_excludes_engine_component_models():
    """whisper/piper/nemo ids are ASR/TTS components of the voice ENGINES —
    selecting one as `voice_model` (a Gemini Live model id) breaks voice."""
    cat = build_catalog()
    voice_ids = {e["id"] for e in cat["roles"]["voice"]}
    for comp in ("whisper-small", "piper-en_US-amy-medium",
                 "nemotron-3.5-asr-streaming-0.6b", "nemo-fastpitch-hifigan"):
        assert comp not in voice_ids, f"{comp} leaked into the voice picker"
    agent_ids = {e["id"] for e in cat["roles"]["orchestrator"]}
    assert "whisper-small" not in agent_ids


def test_voice_engines_reported():
    cat = build_catalog()
    engines = {e["id"]: e for e in cat["voice_engines"]}
    assert set(engines) == {"auto", "local", "local-gpu", "gemini"}
    for e in engines.values():
        assert "label" in e and "available" in e and "short" in e
    assert engines["auto"]["available"] is True


def test_unavailable_entries_carry_key_hint(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import agent_friday.services.provider_registry as pr
    import agent_friday.services.credential_store as cs
    monkeypatch.setattr(cs, "provider_key_status", lambda name: "missing")
    pr._registry = None
    try:
        cat = build_catalog()
        gpt = next(e for e in cat["models"] if e["id"] == "gpt-4o")
        assert gpt["available"] is False
        assert gpt["needs_key"] == "OPENAI_API_KEY"
        assert "OPENAI_API_KEY" in (gpt["hint"] or "")
    finally:
        pr._registry = None  # don't leak state to other tests


def test_anthropic_picker_lineup_and_order():
    cat = build_catalog()
    orch_ids = [e["id"] for e in cat["roles"]["orchestrator"]]
    # The CURRENT family, 2026-08-17. The superseded ids this used to pin
    # (opus-4-6/4-7/4-8, sonnet-4-5/4-6) are retired: a stale hardcoded model id
    # is unmaintained by definition and quietly becomes what ships.
    for mid in ("claude-sonnet-5", "claude-opus-5", "claude-fable-5"):
        assert mid in orch_ids, f"{mid} missing from orchestrator picker"
    claude = [i for i in orch_ids if i.startswith("claude-")]
    assert claude[0] == "claude-sonnet-5", "the default should lead the Claude section"
    for retired in ("claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                    "claude-sonnet-4-6", "claude-sonnet-4-5"):
        assert retired not in orch_ids, f"{retired} is retired and must not appear"


def test_lyria_absent_from_creative_picker():
    cat = build_catalog()
    creative_ids = [e["id"] for e in cat["roles"]["creative"]]
    assert "lyria-clip" not in creative_ids
    assert "lyria-pro" not in creative_ids
    # Still in the flat catalog — the Music panel / music_model resolve them.
    flat_ids = {e["id"] for e in cat["models"]}
    assert {"lyria-clip", "lyria-pro"} <= flat_ids

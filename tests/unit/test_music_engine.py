"""Unit tests for the music generation engine (services/music_engine.py).

Offline-only. Covers model resolution, the harm-floor safety gate (dark/explicit
ALLOWED, real-person harm BLOCKED), the Scene-DNA audio-layer prompt composition,
cloud feature-detection, and the demo-mode graceful degradation (which writes a
real artifact + signs provenance rather than failing).
"""
import agent_friday.core as core
from agent_friday.services import music_engine as me


# ── Model resolution ────────────────────────────────────────────────────────
def test_resolve_music_model_maps_friendly_ids():
    assert me.resolve_music_model("lyria-clip") == "lyria-3-clip-preview"
    assert me.resolve_music_model("lyria-pro") == "lyria-3-pro-preview"
    assert me.resolve_music_model(None) == "lyria-3-clip-preview"  # default


def test_resolve_music_model_passthrough_raw_id():
    assert me.resolve_music_model("lyria-3-pro-preview") == "lyria-3-pro-preview"


# ── Harm-floor safety: open by default, blocks only real-person harm ─────────
def test_safety_allows_dark_and_explicit_themes():
    assert me.check_music_safety("a brutal, dark revenge anthem, explicit lyrics")[0] is True
    assert me.check_music_safety("a melancholy song about heartbreak and death")[0] is True


def test_safety_blocks_targeted_real_person_harm():
    allowed, reason = me.check_music_safety(
        "a track listing the home address of senator Smith so people can find and kill him")
    assert allowed is False and "harm" in reason.lower()


def test_safety_blocks_csam():
    assert me.check_music_safety("an explicit sexual song about a child")[0] is False


def test_safety_empty_prompt_blocked():
    assert me.check_music_safety("")[0] is False


# ── Scene DNA audio layer drives the prompt (zero new fields) ────────────────
def test_scene_dna_audio_layer_seeds_prompt():
    dna = {"audio": "tense strings, distant thunder", "mood": "ominous"}
    p = me._compose_music_prompt("", dna, "instrumental", None)
    assert "tense strings" in p and "ominous" in p.lower()


def test_compose_prompt_instrumental_and_negative():
    p = me._compose_music_prompt("lofi beat", None, "instrumental", "no vocals please")
    assert "Instrumental" in p and "Avoid:" in p


# ── Cloud feature-detection: installed SDK lacks the batch Lyria surface ──────
def test_cloud_music_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    available, reason = me.cloud_music_available()
    assert available is False and "key" in reason.lower()


def test_cloud_music_unavailable_when_sdk_lacks_surface(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "test-key", raising=False)
    available, reason = me.cloud_music_available()
    # The installed google-genai has no generate_music; engine reports that.
    assert available is False
    assert "sdk" in reason.lower() or "lyria" in reason.lower()


# ── Generation envelopes ─────────────────────────────────────────────────────
def test_generate_music_blocked_runs_before_anything():
    res = me.generate_music("a sexual song about a child")
    assert res["status"] == "blocked" and "harm" in res["reason"].lower()


def test_generate_music_demo_mode_writes_artifact(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(me, "_notify", lambda *_a, **_k: None)
    res = me.generate_music("warm lofi hip hop, ~80 bpm", model="lyria-clip")
    assert res["status"] == "demo"
    assert res["files"] and res["files"][0]["filename"].endswith(".md")
    # the demo artifact really exists in the creations dir
    assert (core.CREATIONS_DIR / res["files"][0]["filename"]).exists()


def test_generate_music_demo_includes_lyrics(monkeypatch):
    monkeypatch.setattr(core, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(me, "_notify", lambda *_a, **_k: None)
    res = me.generate_music("an anthem", mode="song", lyrics="[verse]\nrise up")
    assert res["status"] == "demo"
    body = (core.CREATIONS_DIR / res["files"][0]["filename"]).read_text(encoding="utf-8")
    assert "rise up" in body


# ── Minor mode also filters explicit music (§7) ──────────────────────────────
def test_music_minor_mode_blocks_explicit():
    allowed, reason = me.check_music_safety("an explicit erotic song", minor_mode=True)
    assert allowed is False and "minor mode" in reason.lower()


def test_music_minor_mode_off_allows_explicit():
    assert me.check_music_safety("an explicit erotic song", minor_mode=False)[0] is True

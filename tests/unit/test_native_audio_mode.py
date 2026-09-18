"""Phase N decision layer - docs/design/active/local-voice-repair-and-native-audio.md.

These tests are about REFUSAL BEHAVIOUR, not happy paths. The design's whole
risk is a second listening mode that quietly substitutes itself for dictation,
so most of what is asserted here is that a refusal stays a refusal and an offer
stays an offer.
"""
import datetime as dt
import os

import pytest

from agent_friday.services import native_audio as na


# --- section 5.5: never auto-selected ---------------------------------------

def test_default_mode_is_transcribe_never_understand():
    assert na.DEFAULT_LISTENING_MODE == na.MODE_TRANSCRIBE


def test_unknown_mode_falls_back_to_transcribe_not_understand():
    r = na.resolve_listening_mode("wharrgarbl", na._ok())
    assert r["active"] == na.MODE_TRANSCRIBE


# --- section 5.7: provenance, unknown is stale ------------------------------

def test_gguf_built_after_the_fix_is_accepted(tmp_path):
    f = tmp_path / "audio.gguf"
    f.write_bytes(b"x")
    after = dt.datetime(2026, 8, 1).timestamp()
    os.utime(f, (after, after))
    assert na.gguf_provenance(str(f))["available"] is True


def test_gguf_built_before_the_fix_is_refused(tmp_path):
    f = tmp_path / "audio.gguf"
    f.write_bytes(b"x")
    before = dt.datetime(2026, 6, 1).timestamp()
    os.utime(f, (before, before))
    v = na.gguf_provenance(str(f))
    assert v["available"] is False
    assert v["code"] == "local_audio_gguf_stale"
    assert "24118" in v["detail"], "the PR number must reach the maintainer"


def test_gguf_on_the_boundary_day_is_accepted(tmp_path):
    f = tmp_path / "audio.gguf"
    f.write_bytes(b"x")
    boundary = dt.datetime(2026, 6, 5, 12, 0).timestamp()
    os.utime(f, (boundary, boundary))
    assert na.gguf_provenance(str(f))["available"] is True


def test_unknown_provenance_is_treated_as_stale():
    v = na.gguf_provenance("/nonexistent/never.gguf")
    assert v["available"] is False
    assert v["code"] == "local_audio_gguf_stale"


# --- section 5.6: the 30-second ceiling -------------------------------------

@pytest.mark.parametrize("dur", [0.0, 1.0, 29.9, 30.0])
def test_utterances_within_the_ceiling_are_served(dur):
    assert na.check_input_budget(dur)["available"] is True


@pytest.mark.parametrize("dur", [30.01, 45.0, 600.0])
def test_utterances_over_the_ceiling_are_refused_and_offered_transcription(dur):
    v = na.check_input_budget(dur)
    assert v["available"] is False
    assert v["code"] == "local_audio_input_too_long"
    assert "transcription" in v["action"].lower()


def test_budget_is_visible_during_capture_not_only_at_the_end():
    assert na.remaining_budget_sec(0) == 30.0
    assert na.remaining_budget_sec(29.5) == 0.5
    assert na.remaining_budget_sec(45) == 0.0


# --- I4: 16 kHz mono --------------------------------------------------------

def test_16k_mono_is_accepted():
    assert na.check_input_format(16000, 1)["available"] is True


@pytest.mark.parametrize("rate,ch", [(44100, 1), (16000, 2), (8000, 1), (0, 0)])
def test_other_formats_are_refused(rate, ch):
    assert na.check_input_format(rate, ch)["available"] is False


# --- I4: only E2B / E4B / 12B have an audio tower ---------------------------

@pytest.mark.parametrize("mid", ["gemma4:e2b-fridayweaver-1.0", "gemma4-e4b",
                                 "gemma4-12b-it"])
def test_audio_capable_models_are_accepted(mid):
    assert na.check_model_audio_capable(mid)["available"] is True


@pytest.mark.parametrize("mid", ["gemma4-27b", "llama3-8b", "", None])
def test_models_without_an_audio_tower_are_refused(mid):
    v = na.check_model_audio_capable(mid)
    assert v["available"] is False
    assert v["code"] == "local_audio_unsupported_model"


# --- I2: Ollama cannot serve audio ------------------------------------------

def test_llama_server_runtime_is_accepted():
    assert na.check_runtime("llama-server")["available"] is True


@pytest.mark.parametrize("rt", ["ollama", "Ollama", "ollama-managed", "", None])
def test_ollama_and_unknown_runtimes_are_refused(rt):
    v = na.check_runtime(rt)
    assert v["available"] is False
    assert v["code"] == "local_audio_runtime_unsupported"


# --- section 5.6: admission refuses, unlike the Tier-2 probe ----------------

def test_admission_uses_the_smaller_of_two_disputed_vram_figures():
    """torch says there is room, nvidia-smi says there is not. A voice mode
    yields; it does not compete with whatever holds the card."""
    v = na.check_vram_admission({"vram_free_gb": 11.6,
                                 "vram_free_real_gb": 1.1})
    assert v["available"] is False
    assert v["code"] == "local_audio_no_vram"


def test_admission_passes_when_both_figures_agree_there_is_room():
    v = na.check_vram_admission({"vram_free_gb": 11.6,
                                 "vram_free_real_gb": 7.5})
    assert v["available"] is True


def test_unmeasurable_vram_is_refused_not_assumed():
    assert na.check_vram_admission({})["available"] is False


# --- C2: surfaces and offers, never substitutes -----------------------------

def test_understand_requested_but_unavailable_does_not_become_transcribe():
    """The whole design risk in one test. An unavailable `understand` must not
    silently resolve to `transcribe`; it reports no active mode and carries the
    offer for the user to accept."""
    unavailable = na.check_input_budget(999)
    r = na.resolve_listening_mode(na.MODE_UNDERSTAND, unavailable)
    assert r["requested"] == na.MODE_UNDERSTAND
    assert r["active"] is None, "a refusal must not resolve to the other mode"
    assert r["offer"] is unavailable


def test_understand_available_resolves_to_understand():
    r = na.resolve_listening_mode(na.MODE_UNDERSTAND, na._ok())
    assert r["active"] == na.MODE_UNDERSTAND
    assert r["offer"] is None


def test_availability_returns_the_most_fundamental_reason_first():
    """Model identity is checked before VRAM: a user on a model that can never
    listen should be told that, not told to free up GPU."""
    v = na.understand_mode_availability(
        model_id="gemma4-27b", runtime="ollama", gguf_path=None,
        gpu_status={"vram_free_gb": 0.0, "vram_free_real_gb": 0.0})
    assert v["code"] == "local_audio_unsupported_model"


def test_every_refusal_carries_a_code_message_and_action():
    for v in (na.check_input_budget(999), na.check_runtime("ollama"),
              na.check_model_audio_capable("llama3"),
              na.gguf_provenance("/nope.gguf"),
              na.check_vram_admission({"vram_free_gb": 0.1}),
              na.seat_evicted_refusal()):
        assert v["available"] is False
        assert v["code"] and v["user_message"] and v["action"], v

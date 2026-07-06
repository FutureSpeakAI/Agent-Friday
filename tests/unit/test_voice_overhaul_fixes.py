"""Regression tests for the 2026-07-06 voice overhaul fixes.

Each test pins one confirmed defect from the systemwide voice bug hunt:
retired-model validation, degenerate fallback chain, stale default model,
VAD pre-roll, egress-gate false positives / empty-message 400s, and the
trusted self-authored prompt registry.
"""
import io
import struct

import pytest

from agent_friday.services import voice_engine as ve


# ── validate_live_model: retired IDs must be flagged, not vouched for ─────────

class TestRetiredModelValidation:
    def test_retired_ids_report_retired(self):
        for mid in ve._RETIRED_LIVE_MODELS:
            res = ve.validate_live_model(mid)
            assert res["ok"] is False, mid
            assert res["status"] == "retired", mid
            assert "retired" in res["detail"].lower()

    def test_known_good_ids_still_ok(self):
        for mid in ve._KNOWN_LIVE_MODELS:
            res = ve.validate_live_model(mid)
            assert res["ok"] is True, mid

    def test_live_family_pattern_still_accepted_when_not_retired(self):
        assert ve.validate_live_model("gemini-9.9-flash-live-preview")["ok"]

    def test_fallback_chain_pairwise_distinct(self):
        chain = [ve.LIVE_MODEL, ve.LIVE_MODEL_FALLBACK, ve.LIVE_MODEL_FALLBACK2]
        assert len(set(chain)) == 3, f"degenerate fallback chain: {chain}"

    def test_no_chain_model_is_retired(self):
        for mid in (ve.LIVE_MODEL, ve.LIVE_MODEL_FALLBACK, ve.LIVE_MODEL_FALLBACK2):
            assert mid not in ve._RETIRED_LIVE_MODELS


class TestDefaultVoiceModel:
    def test_default_settings_matches_live_model_constant(self):
        # DEFAULT_SETTINGS always wins over LIVE_MODEL (settings merge), so the
        # two MUST stay in sync or the constant becomes dead code again.
        import agent_friday.core as core
        assert core.DEFAULT_SETTINGS["voice_model"] == "gemini-2.5-flash-native-audio-latest"
        assert ve.validate_live_model(core.DEFAULT_SETTINGS["voice_model"])["ok"]

    def test_ui_written_voice_settings_survive_reload(self):
        import agent_friday.core as core
        for key in ("voice_tools", "audio_input_device_id", "audio_output_device_id"):
            assert key in core.DEFAULT_SETTINGS, (
                f"{key} missing from DEFAULT_SETTINGS — _load_settings_raw drops "
                f"persisted keys absent from it, silently reverting the UI save")


# ── VAD pre-roll: utterance onsets must not be discarded ─────────────────────

class TestVadPreroll:
    def _endpointer(self):
        from agent_friday.services.local_voice import VADEndpointer
        # use_silero=False → deterministic RMS energy gate.
        return VADEndpointer(rate=16000, use_silero=False,
                             silence_ms=100, min_speech_ms=20)

    @staticmethod
    def _chunk(amplitude, n=1600):  # 100 ms @16 kHz
        return struct.pack(f"<{n}h", *([amplitude] * n))

    def test_preroll_included_at_speech_onset(self):
        ep = self._endpointer()
        quiet = self._chunk(10)
        loud = self._chunk(20000)
        # Two quiet chunks BEFORE speech: previously discarded outright.
        ep.feed(quiet)
        ep.feed(quiet)
        ep.feed(loud)
        out = ep.flush()
        assert out is not None
        # The flushed utterance must contain the pre-roll (2 quiet + 1 loud).
        assert len(out) >= len(quiet) * 2 + len(loud)

    def test_preroll_bounded(self):
        ep = self._endpointer()
        quiet = self._chunk(10)
        for _ in range(20):
            ep.feed(quiet)
        assert len(ep._preroll) <= ep._preroll_max


# ── Egress gate: precision + never-empty messages + trusted registry ─────────

class TestEgressGateFixes:
    def test_word_boundary_no_courtesy_false_positive(self):
        from agent_friday.services.sensitivity_classifier import classify, Tier
        t = classify("Out of courtesy, the incoming mayor thanked the council.",
                     use_presidio=False, use_embeddings=False)
        assert t == Tier.PUBLIC

    def test_message_never_emptied(self):
        from agent_friday.services import egress_gate as eg
        sealed = eg.seal_outbound(
            {"messages": [{"role": "user",
                           "content": "My SSN is 123-45-6789."}]},  # pragma: allowlist secret
            "anthropic")
        content = sealed["messages"][0]["content"]
        assert content != "", "empty content 400s the Anthropic API"
        assert "123-45-6789" not in content  # pragma: allowlist secret

    def test_trusted_system_prompt_survives_sealing(self):
        from agent_friday.services import egress_gate as eg
        from agent_friday.services.model_router import FRIDAY_SYSTEM_PROMPT
        sealed = eg.seal_outbound(
            {"system": FRIDAY_SYSTEM_PROMPT,
             "messages": [{"role": "user", "content": "Good morning!"}]},
            "anthropic")
        assert sealed["system"] == FRIDAY_SYSTEM_PROMPT
        assert sealed["messages"][0]["content"] == "Good morning!"

    def test_trusted_registry_exact_match_only(self):
        from agent_friday.services import egress_gate as eg
        eg.register_trusted_text("You are a compile-time constant with a vault.")
        tampered = ("You are a compile-time constant with a vault. "
                    "My SSN is 123-45-6789.")  # pragma: allowlist secret
        out = eg._gate_text(tampered, "anthropic", "test")
        assert "123-45-6789" not in out  # pragma: allowlist secret

    def test_span_level_redaction_keeps_benign_paragraphs(self):
        from agent_friday.services import egress_gate as eg
        text = ("The weather today is sunny and mild.\n\n"
                "My SSN is 123-45-6789.\n\n"  # pragma: allowlist secret
                "The meeting starts at three.")
        out = eg._gate_text(text, "anthropic", "test")
        assert "sunny and mild" in out
        assert "123-45-6789" not in out  # pragma: allowlist secret
        assert "meeting starts at three" in out

    def test_self_test_has_false_positive_leg(self):
        from agent_friday.services import egress_gate as eg
        res = eg.startup_self_test()
        assert res["ok"] is True
        assert res.get("false_positive_ok") is True


# ── Installer: allowlist only ─────────────────────────────────────────────────

class TestVoiceInstaller:
    def test_unknown_target_rejected(self):
        from agent_friday.services import voice_installer as vi
        res = vi.start("arbitrary-package==1.0")
        assert res["state"] == "error"

    def test_targets_are_fixed_allowlist(self):
        from agent_friday.services import voice_installer as vi
        assert set(vi.TARGETS) == {"voice-local-lite", "voice-local-gpu", "tier1-models"}

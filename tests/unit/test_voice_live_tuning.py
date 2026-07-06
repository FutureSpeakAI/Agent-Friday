"""Regression tests for the 2026-07-06 Gemini Live tuning pass.

Pins the fixes for Stephen's three Tier-3 requirements, grounded in Google's
current Live API docs:
  - barge-in ON by default (was NO_INTERRUPTION → voice wouldn't interrupt),
  - context-window compression ON by default (removes the ~15-min session cap),
  - the fallback chain / model config stay coherent.
"""
import pytest

from google.genai import types

from agent_friday.routes.voice import _build_realtime_input_config


class TestInterruptionDefault:
    def test_default_is_interruptible(self):
        # Unset/empty/"auto"/legacy "speaker"/"headphones" must all barge in.
        for mode in ("auto", "", None, "speaker", "headphones"):
            cfg = _build_realtime_input_config(types, mode)
            ah = getattr(cfg, "activity_handling", None)
            assert ah == types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS, mode

    def test_explicit_opt_out_disables_interruption(self):
        for mode in ("no-barge", "no_barge", "speaker-safe", "none", "off"):
            cfg = _build_realtime_input_config(types, mode)
            ah = getattr(cfg, "activity_handling", None)
            assert ah == types.ActivityHandling.NO_INTERRUPTION, mode

    def test_vad_still_enabled(self):
        # Automatic activity detection must stay ON regardless of mode, or the
        # server never detects user speech to interrupt on.
        for mode in ("auto", "no-barge"):
            cfg = _build_realtime_input_config(types, mode)
            aad = cfg.automatic_activity_detection
            assert aad is not None and aad.disabled is False, mode


class TestLiveSessionDefaults:
    def test_context_compression_on_by_default(self):
        # Without compression the Live session hits a hard ~15-min cap and
        # terminates — the opposite of "fluid for hours".
        import agent_friday.core as core
        assert core.DEFAULT_SETTINGS["voice_context_compression"] is True

    def test_interruption_mode_default_is_interruptible(self):
        import agent_friday.core as core
        cfg = _build_realtime_input_config(
            types, core.DEFAULT_SETTINGS["voice_interruption_mode"])
        assert cfg.activity_handling == types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS

    def test_start_sensitivity_low_for_echo(self):
        # LOW start sensitivity keeps Friday's own quieter speaker bleed from
        # tripping a self-interrupt when barge-in is on.
        cfg = _build_realtime_input_config(types, "auto")
        aad = cfg.automatic_activity_detection
        assert aad.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_LOW


class TestModelChainStillValid:
    def test_live_chain_distinct_and_not_retired(self):
        from agent_friday.services import voice_engine as ve
        chain = [ve.LIVE_MODEL, ve.LIVE_MODEL_FALLBACK, ve.LIVE_MODEL_FALLBACK2]
        assert len(set(chain)) == 3
        for m in chain:
            assert m not in ve._RETIRED_LIVE_MODELS

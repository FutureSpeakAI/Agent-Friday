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


class TestToolChoreography:
    """announce → act → confirm — pins the fix for the on-stage freeze/stutter
    during voice tool execution (news-reading demo, 2026-07-08)."""

    def test_choreography_block_exists_and_orders_the_contract(self):
        from agent_friday.routes.voice import VOICE_TOOL_CHOREOGRAPHY as c
        assert "Before EVERY tool call" in c
        assert "Never call a tool mid-sentence" in c
        # The three beats must appear in order: announce, silent-run, confirm.
        i_announce = c.index("announcing what you're about to do")
        i_silent = c.index("stay silent")
        i_confirm = c.index("confirm completion")
        assert i_announce < i_silent < i_confirm

    def test_choreography_wired_into_both_session_prompts(self):
        # Both ws_live and ws_voice_local must inject the block — a grep-level
        # pin so a prompt refactor can't silently drop the choreography.
        import inspect
        import agent_friday.routes.voice as vr
        src = inspect.getsource(vr)
        assert src.count("+ VOICE_TOOL_CHOREOGRAPHY") >= 2, (
            "VOICE_TOOL_CHOREOGRAPHY must be concatenated into BOTH voice "
            "session prompts (Gemini Live and local)")


class TestPaywallHint:
    def test_thin_extraction_mentions_paywall(self, monkeypatch):
        import agent_friday.services.news_engine as ne
        monkeypatch.setattr(ne, "_extract_article_text",
                            lambda url: ("Some Title", "too short"))
        result, status = ne._deep_dive_article("https://example.com/story",
                                               refresh=True)
        assert status == 422
        assert "paywall" in result["message"].lower()

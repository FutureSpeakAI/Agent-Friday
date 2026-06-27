"""Unit tests for voice_personality.py — pure mood-adaptive voice system.

All functions are stateless/class-based with no filesystem or network IO.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.voice_personality import (
    AFFECTIVE_DIALOG_INSTRUCTION,
    DEFAULT_VOICE_STYLE,
    VOICE_MOOD_PROFILES,
    VoiceMoodProfile,
    VoicePersonality,
    get_voice_personality,
)

KNOWN_MOODS = ["curious", "creative", "protective", "focused", "social", "reflective"]
VALID_PACES = {"slow", "normal", "fast"}


# ── VOICE_MOOD_PROFILES integrity ─────────────────────────────────────────────

class TestVoiceMoodProfiles:
    def test_all_six_moods_present(self):
        assert set(KNOWN_MOODS).issubset(VOICE_MOOD_PROFILES.keys())

    @pytest.mark.parametrize("mood", KNOWN_MOODS)
    def test_profile_is_voice_mood_profile_instance(self, mood):
        assert isinstance(VOICE_MOOD_PROFILES[mood], VoiceMoodProfile)

    @pytest.mark.parametrize("mood", KNOWN_MOODS)
    def test_profile_has_nonempty_style_instruction(self, mood):
        profile = VOICE_MOOD_PROFILES[mood]
        assert isinstance(profile.style_instruction, str)
        assert len(profile.style_instruction.strip()) > 0

    @pytest.mark.parametrize("mood", KNOWN_MOODS)
    def test_profile_has_valid_pace(self, mood):
        profile = VOICE_MOOD_PROFILES[mood]
        assert profile.pace in VALID_PACES, (
            f"Mood '{mood}' has invalid pace '{profile.pace}'; "
            f"expected one of {VALID_PACES}"
        )

    @pytest.mark.parametrize("mood", KNOWN_MOODS)
    def test_profile_mood_field_matches_key(self, mood):
        assert VOICE_MOOD_PROFILES[mood].mood == mood

    def test_protective_is_slow(self):
        assert VOICE_MOOD_PROFILES["protective"].pace == "slow"

    def test_focused_is_fast(self):
        assert VOICE_MOOD_PROFILES["focused"].pace == "fast"

    def test_reflective_is_slow(self):
        assert VOICE_MOOD_PROFILES["reflective"].pace == "slow"

    def test_curious_is_normal(self):
        assert VOICE_MOOD_PROFILES["curious"].pace == "normal"

    def test_creative_is_normal(self):
        assert VOICE_MOOD_PROFILES["creative"].pace == "normal"

    def test_social_is_normal(self):
        assert VOICE_MOOD_PROFILES["social"].pace == "normal"


# ── VoicePersonality.get_voice_style ─────────────────────────────────────────

class TestGetVoiceStyle:
    @pytest.fixture
    def vp(self):
        return VoicePersonality()

    @pytest.mark.parametrize("mood", KNOWN_MOODS)
    def test_known_mood_returns_profile_style(self, vp, mood):
        expected = VOICE_MOOD_PROFILES[mood].style_instruction
        assert vp.get_voice_style(mood) == expected

    def test_unknown_mood_returns_default(self, vp):
        assert vp.get_voice_style("confused") == DEFAULT_VOICE_STYLE

    def test_idle_mood_returns_default(self, vp):
        assert vp.get_voice_style("idle") == DEFAULT_VOICE_STYLE

    def test_empty_string_mood_returns_default(self, vp):
        assert vp.get_voice_style("") == DEFAULT_VOICE_STYLE

    def test_mood_lookup_is_case_insensitive(self, vp):
        # The method lowercases the input
        assert vp.get_voice_style("CURIOUS") == vp.get_voice_style("curious")
        assert vp.get_voice_style("Focused") == vp.get_voice_style("focused")

    def test_uses_current_mood_when_no_arg(self, vp):
        vp.current_mood = "curious"
        assert vp.get_voice_style() == VOICE_MOOD_PROFILES["curious"].style_instruction

    def test_default_mood_is_idle(self, vp):
        # Fresh instance has idle mood → default style
        assert vp.current_mood == "idle"
        assert vp.get_voice_style() == DEFAULT_VOICE_STYLE


# ── VoicePersonality.build_system_instruction ─────────────────────────────────

class TestBuildSystemInstruction:
    BASE = "You are Friday, a helpful AI assistant."

    @pytest.fixture
    def vp(self):
        return VoicePersonality()

    def test_returns_string(self, vp):
        result = vp.build_system_instruction(self.BASE)
        assert isinstance(result, str)

    def test_contains_base_instruction(self, vp):
        result = vp.build_system_instruction(self.BASE)
        assert self.BASE in result

    def test_contains_mood_block_header(self, vp):
        result = vp.build_system_instruction(self.BASE, mood="curious")
        assert "CURRENT MOOD" in result
        assert "CURIOUS" in result

    def test_mood_name_is_uppercased_in_block(self, vp):
        for mood in KNOWN_MOODS:
            result = vp.build_system_instruction(self.BASE, mood=mood)
            assert mood.upper() in result

    def test_affective_true_prepends_affective_block(self, vp):
        result = vp.build_system_instruction(self.BASE, affective_dialog=True)
        assert result.startswith(AFFECTIVE_DIALOG_INSTRUCTION)

    def test_affective_false_omits_affective_block(self, vp):
        result = vp.build_system_instruction(self.BASE, affective_dialog=False)
        assert AFFECTIVE_DIALOG_INSTRUCTION not in result

    def test_affective_defaults_to_instance_setting_false(self, vp):
        vp.affective_dialog = False
        result = vp.build_system_instruction(self.BASE)
        assert AFFECTIVE_DIALOG_INSTRUCTION not in result

    def test_affective_defaults_to_instance_setting_true(self, vp):
        vp.affective_dialog = True
        result = vp.build_system_instruction(self.BASE)
        assert result.startswith(AFFECTIVE_DIALOG_INSTRUCTION)

    def test_arg_affective_overrides_instance_setting(self, vp):
        vp.affective_dialog = True
        result = vp.build_system_instruction(self.BASE, affective_dialog=False)
        assert AFFECTIVE_DIALOG_INSTRUCTION not in result

    def test_slow_pace_adds_measured_hint(self, vp):
        # protective and reflective are "slow"
        for mood in ("protective", "reflective"):
            result = vp.build_system_instruction(self.BASE, mood=mood)
            assert "measured" in result or "deliberate" in result

    def test_fast_pace_adds_efficiency_hint(self, vp):
        result = vp.build_system_instruction(self.BASE, mood="focused")
        assert "efficient" in result or "wasted" in result

    def test_normal_pace_no_pace_hint(self, vp):
        # normal-pace moods should NOT add a pace clause
        for mood in ("curious", "creative", "social"):
            result = vp.build_system_instruction(self.BASE, mood=mood)
            # These specific phrases are only added for slow/fast
            assert "measured, deliberate pace" not in result
            assert "no wasted words" not in result

    def test_mood_arg_overrides_current_mood(self, vp):
        vp.current_mood = "social"
        result = vp.build_system_instruction(self.BASE, mood="focused")
        assert "FOCUSED" in result
        assert "SOCIAL" not in result

    def test_uses_current_mood_when_no_mood_arg(self, vp):
        vp.current_mood = "reflective"
        result = vp.build_system_instruction(self.BASE)
        assert "REFLECTIVE" in result

    def test_style_instruction_present_in_output(self, vp):
        for mood in KNOWN_MOODS:
            expected_style = VOICE_MOOD_PROFILES[mood].style_instruction
            result = vp.build_system_instruction(self.BASE, mood=mood)
            # Style text is embedded verbatim
            assert expected_style in result


# ── VoicePersonality property setters ────────────────────────────────────────

class TestVoicePersonalityProperties:
    def test_set_current_mood_lowercases(self):
        vp = VoicePersonality()
        vp.current_mood = "SOCIAL"
        assert vp.current_mood == "social"

    def test_set_affective_dialog_coerces_to_bool(self):
        vp = VoicePersonality()
        vp.affective_dialog = 1  # truthy int
        assert vp.affective_dialog is True
        vp.affective_dialog = 0
        assert vp.affective_dialog is False


# ── get_voice_personality (singleton) ────────────────────────────────────────

class TestGetVoicePersonality:
    def test_returns_voice_personality_instance(self):
        instance = get_voice_personality()
        assert isinstance(instance, VoicePersonality)

    def test_returns_same_instance_on_second_call(self):
        a = get_voice_personality()
        b = get_voice_personality()
        assert a is b


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

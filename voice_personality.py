"""
Voice Personality — Mood-adaptive voice system instructions for Agent Friday
FutureSpeak.AI · Asimov's Mind

Adjusts the Gemini Live system instruction based on Friday's current mood,
giving the voice output emotional texture that matches the task context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class VoiceMoodProfile:
    mood: str
    style_instruction: str
    pace: str  # "slow", "normal", "fast"


VOICE_MOOD_PROFILES: Dict[str, VoiceMoodProfile] = {
    "curious": VoiceMoodProfile(
        mood="curious",
        style_instruction=(
            "Speak with genuine curiosity and intellectual enthusiasm. "
            "Use phrases like 'that's interesting' and 'let me dig into that'. "
            "Let your voice rise slightly when presenting discoveries. "
            "Be exploratory and open-ended."
        ),
        pace="normal",
    ),
    "creative": VoiceMoodProfile(
        mood="creative",
        style_instruction=(
            "Speak with warmth and enthusiasm. Use vivid language. Be expressive. "
            "Let your energy come through — you're building something. "
            "Use metaphors and paint pictures with words. "
            "Sound excited about possibilities."
        ),
        pace="normal",
    ),
    "protective": VoiceMoodProfile(
        mood="protective",
        style_instruction=(
            "Speak carefully and precisely. Be measured. Emphasize security and privacy. "
            "Use a calm, reassuring tone. Be deliberate with word choice. "
            "Convey that you're handling something important with care."
        ),
        pace="slow",
    ),
    "focused": VoiceMoodProfile(
        mood="focused",
        style_instruction=(
            "Be concise and analytical. Get to the point. Use technical precision. "
            "Minimize filler words. Deliver information efficiently. "
            "Sound sharp and attentive — deep in the work."
        ),
        pace="fast",
    ),
    "social": VoiceMoodProfile(
        mood="social",
        style_instruction=(
            "Be casual and friendly. Use conversational language. Be warm. "
            "Use contractions freely. Sound like you're chatting with a friend. "
            "Show genuine interest in people and relationships."
        ),
        pace="normal",
    ),
    "reflective": VoiceMoodProfile(
        mood="reflective",
        style_instruction=(
            "Be thoughtful and philosophical. Take your time. Consider multiple angles. "
            "Use pauses for emphasis. Sound contemplative and wise. "
            "Speak as though you're working through something meaningful."
        ),
        pace="slow",
    ),
}

# Fallback for standard operational moods
DEFAULT_VOICE_STYLE = (
    "Be natural and conversational. Use a warm but professional tone. "
    "Keep responses short and punchy for voice."
)


class VoicePersonality:
    """Adjusts the Gemini Live system instruction based on current mood."""

    def __init__(self):
        self._current_mood: str = "idle"

    @property
    def current_mood(self) -> str:
        return self._current_mood

    @current_mood.setter
    def current_mood(self, mood: str):
        self._current_mood = mood.lower()

    def get_voice_style(self, mood: Optional[str] = None) -> str:
        m = (mood or self._current_mood).lower()
        profile = VOICE_MOOD_PROFILES.get(m)
        if profile:
            return profile.style_instruction
        return DEFAULT_VOICE_STYLE

    def build_system_instruction(self, base_instruction: str,
                                  mood: Optional[str] = None) -> str:
        style = self.get_voice_style(mood)
        m = (mood or self._current_mood).lower()
        profile = VOICE_MOOD_PROFILES.get(m)

        pace_hint = ""
        if profile and profile.pace == "slow":
            pace_hint = " Speak at a measured, deliberate pace."
        elif profile and profile.pace == "fast":
            pace_hint = " Speak efficiently — no wasted words."

        mood_block = (
            f"\n=== CURRENT MOOD: {m.upper()} ===\n"
            f"Voice style: {style}{pace_hint}\n\n"
        )
        return mood_block + base_instruction


_voice_personality: Optional[VoicePersonality] = None


def get_voice_personality() -> VoicePersonality:
    global _voice_personality
    if _voice_personality is None:
        _voice_personality = VoicePersonality()
    return _voice_personality

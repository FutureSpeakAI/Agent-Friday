"""Gauntlet finding: services/voice_engine.py's _synthesize_tts_wav() ignored
local_preferred mode entirely -- with a valid Gemini key and network online,
TTS went straight to Gemini without ever trying the local pyttsx3/Piper
engine first, contradicting local_preferred's own documented definition
("local first, cloud when it helps") and diverging from the established
codebase idiom used for vision (routes/chat.py:369) and file-upload
analysis (routes/core_routes.py:1085): `mode in ('local_only',
'local_preferred')`.

Unlike local_only (an absolute refusal if local fails, fixed separately),
local_preferred must still fall through to Gemini if local synthesis
fails -- it's a preference, not a guarantee.

NOTE on test design: _synthesize_tts_wav_gemini's call site is wrapped in a
broad `except Exception`, which falls back to local on ANY error -- so a
mock that *raises* to signal "you shouldn't have called me" is unsafe here:
the raise gets silently absorbed as "Gemini failed" and the function's own
fallback-to-local logic then returns a coincidentally-correct-looking value
through the wrong path (this bit an earlier draft of this exact test). Every
assertion below tracks calls in a list and asserts on it after the call
returns, never by raising from inside a mock.
"""
from __future__ import annotations

from agent_friday.services import voice_engine as ve


def _settings(mode):
    return {"model_routing": {"mode": mode}}


class TestSynthesizeTtsWavHonorsLocalPreferred:
    def test_local_preferred_tries_local_first_even_with_a_valid_key(self, monkeypatch):
        monkeypatch.setattr(ve.core, "_scrub_pii", lambda text: (text, {}))
        monkeypatch.setattr(ve, "_load_settings", lambda: _settings("local_preferred"))
        monkeypatch.setattr(ve.core, "GEMINI_API_KEY", "fake-valid-key")
        monkeypatch.setattr(ve, "_network_is_offline", lambda: False)

        local_calls = []
        gemini_calls = []
        monkeypatch.setattr(ve, "_synthesize_tts_wav_local",
                            lambda text: local_calls.append(text) or object())
        monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini",
                            lambda text, voice=None, style="briefing":
                                gemini_calls.append(text) or object())

        ve._synthesize_tts_wav("hello")

        assert local_calls == ["hello"], (
            "local_preferred mode never tried local synthesis at all -- "
            "local_preferred should try local first, matching the vision/"
            "file-upload idiom (mode in ('local_only', 'local_preferred'))"
        )
        assert gemini_calls == [], (
            "local_preferred mode called Gemini TTS even though local "
            "synthesis succeeded and should have been preferred"
        )

    def test_local_preferred_falls_back_to_gemini_when_local_fails(self, monkeypatch):
        """No-op-shaped sanity check: unlike local_only, a local_preferred
        TTS failure must still fall through to Gemini -- it's a
        preference, not an absolute guarantee."""
        monkeypatch.setattr(ve.core, "_scrub_pii", lambda text: (text, {}))
        monkeypatch.setattr(ve, "_load_settings", lambda: _settings("local_preferred"))
        monkeypatch.setattr(ve.core, "GEMINI_API_KEY", "fake-valid-key")
        monkeypatch.setattr(ve, "_network_is_offline", lambda: False)
        monkeypatch.setattr(ve, "_synthesize_tts_wav_local", lambda text: None)
        gemini_calls = []
        monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini",
                            lambda text, voice=None, style="briefing":
                                gemini_calls.append(text) or object())

        ve._synthesize_tts_wav("hello")
        assert gemini_calls == ["hello"]

    def test_smart_mode_unaffected_still_prefers_cloud_when_key_present(self, monkeypatch):
        """No-op-shaped sanity check: this fix must not change behavior for
        modes other than local_preferred/local_only -- 'smart'/'cloud_only'
        should still go straight to Gemini when a key is present, never
        trying local first."""
        monkeypatch.setattr(ve.core, "_scrub_pii", lambda text: (text, {}))
        monkeypatch.setattr(ve, "_load_settings", lambda: _settings("smart"))
        monkeypatch.setattr(ve.core, "GEMINI_API_KEY", "fake-valid-key")
        monkeypatch.setattr(ve, "_network_is_offline", lambda: False)

        local_calls = []
        gemini_calls = []
        monkeypatch.setattr(ve, "_synthesize_tts_wav_local",
                            lambda text: local_calls.append(text) or None)
        monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini",
                            lambda text, voice=None, style="briefing":
                                gemini_calls.append(text) or object())

        ve._synthesize_tts_wav("hello")
        assert local_calls == [], "smart mode should not try local first"
        assert gemini_calls == ["hello"]

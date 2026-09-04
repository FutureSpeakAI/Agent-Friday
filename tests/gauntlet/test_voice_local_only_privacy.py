"""Gauntlet finding: local-only mode did not gate the voice pipeline the
way it gates vision and text (fixed 2026-08-23, commit 4607bd9, for
routes/chat.py's screenshot path). Two call sites were affected:

1. routes/voice.py:_resolve_voice_engine() -- with Local-Only Mode on but
   the Tier-1 voice deps not installed (a separate, easy-to-skip step from
   `pip install -e .[voice-local-lite]`), a session was silently handed
   /ws/live (Gemini) instead of refusing or staying local. `voice_engine`
   preference and Gemini key validity were checked; `model_routing.mode`
   never was.

2. services/voice_engine.py:_synthesize_tts_wav() -- "read this aloud" and
   the News audio briefing sent spoken text to Gemini TTS regardless of
   local-only mode; only a PII regex scrub and network/key status gated
   the cloud path, and the PII scrubber is known to have real gaps
   (memory: "Vault contact-PII leak -- phone/address/account-tail never
   had a regex").

Both are fixed the same way the vision path was: local-only becomes an
absolute override that wins regardless of preference/key/network status,
and refuses clearly (rather than silently degrading to cloud) when no
local engine is available.
"""
from __future__ import annotations

import pytest

from agent_friday.routes import voice as v
from agent_friday.services import voice_engine as ve


def _settings(mode="local_only", voice_engine="local"):
    return {"model_routing": {"mode": mode}, "voice_engine": voice_engine}


class _FakeLocalEngine:
    """A local voice engine that reports itself unavailable -- deps missing."""
    def available(self):
        return False

    def models_ready(self):
        return False

    def resolve_tier(self, settings):
        return "cpu"


class TestResolveVoiceEngineRespectsLocalOnly:
    def test_local_only_never_returns_gemini_even_with_a_valid_key(self, monkeypatch):
        monkeypatch.setattr(v, "_network_status", lambda: {"offline": False})
        monkeypatch.setattr(v, "resolve_gemini_key", lambda: {"valid": True})
        monkeypatch.setattr(v, "get_local_voice_engine", lambda: _FakeLocalEngine())
        monkeypatch.setattr(v, "_local_brain_ready", lambda: True)

        result = v._resolve_voice_engine(_settings(mode="local_only",
                                                    voice_engine="local"))
        assert result["engine"] != "gemini", (
            "with Local-Only Mode on and no local voice engine ready, "
            "_resolve_voice_engine still picked Gemini -- the same class "
            "of bug the vision path had before its 2026-08-23 fix"
        )

    def test_explicit_gemini_preference_does_not_override_local_only(self, monkeypatch):
        """Local-only must be an absolute override, not just the default
        path's behavior -- an explicit voice_engine='gemini' preference
        must not defeat it, mirroring how the vision fix's local-only
        check isn't conditional on any other setting."""
        monkeypatch.setattr(v, "_network_status", lambda: {"offline": False})
        monkeypatch.setattr(v, "resolve_gemini_key", lambda: {"valid": True})
        monkeypatch.setattr(v, "get_local_voice_engine", lambda: _FakeLocalEngine())
        monkeypatch.setattr(v, "_local_brain_ready", lambda: True)

        result = v._resolve_voice_engine(_settings(mode="local_only",
                                                    voice_engine="gemini"))
        assert result["engine"] != "gemini"

    def test_non_local_only_mode_still_falls_back_to_cloud_as_before(self, monkeypatch):
        """No-op-shaped sanity check: this fix must not touch behavior for
        smart/local_preferred/cloud_only modes -- cloud fallback when local
        deps are missing is still the documented, intended behavior there."""
        monkeypatch.setattr(v, "_network_status", lambda: {"offline": False})
        monkeypatch.setattr(v, "resolve_gemini_key", lambda: {"valid": True})
        monkeypatch.setattr(v, "get_local_voice_engine", lambda: _FakeLocalEngine())
        monkeypatch.setattr(v, "_local_brain_ready", lambda: True)

        result = v._resolve_voice_engine(_settings(mode="local_preferred",
                                                    voice_engine="local"))
        assert result["engine"] == "gemini", (
            "local_preferred mode should still fall back to cloud when "
            "local deps are missing -- that is its documented semantics, "
            "unlike local_only"
        )


class TestSynthesizeTtsWavRespectsLocalOnly:
    def test_local_only_refuses_rather_than_calling_gemini(self, monkeypatch):
        monkeypatch.setattr(ve.core, "_scrub_pii", lambda text: (text, {}))
        monkeypatch.setattr(ve, "_load_settings", lambda: _settings(mode="local_only"))
        monkeypatch.setattr(ve, "_synthesize_tts_wav_local", lambda text: None)

        def _fail_if_called(*a, **k):
            raise AssertionError(
                "local-only mode is on but _synthesize_tts_wav_gemini was "
                "called anyway -- spoken text would have reached Gemini TTS"
            )
        monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini", _fail_if_called)

        with pytest.raises(RuntimeError, match="local-only"):
            ve._synthesize_tts_wav("hello, this should stay on-device")

    def test_local_only_uses_local_engine_when_available(self, monkeypatch):
        monkeypatch.setattr(ve.core, "_scrub_pii", lambda text: (text, {}))
        monkeypatch.setattr(ve, "_load_settings", lambda: _settings(mode="local_only"))
        sentinel = object()
        monkeypatch.setattr(ve, "_synthesize_tts_wav_local", lambda text: sentinel)

        def _fail_if_called(*a, **k):
            raise AssertionError("Gemini TTS must not be called when local synthesis succeeded")
        monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini", _fail_if_called)

        assert ve._synthesize_tts_wav("hello") is sentinel

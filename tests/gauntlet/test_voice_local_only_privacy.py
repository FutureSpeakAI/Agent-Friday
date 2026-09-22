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

    def test_local_preference_no_longer_falls_back_to_cloud(self, monkeypatch):
        """AMENDED 2026-09-09. This test previously asserted the opposite.

        It used to pin that `voice_engine="local"` under `local_preferred`
        routing WOULD fall back to Gemini when the Tier-1 deps were missing,
        on the reasoning that cloud fallback is local_preferred's documented
        semantics. That reasoning conflated two different settings: the
        routing MODE (how models are chosen) and the user's explicit VOICE
        choice. A user who selects the voice engine named "local" and receives
        Gemini has been lied to by the word itself, whatever the routing mode
        says -- and the old guard only caught it when local_only was ALSO set,
        so protection required saying "local" twice in two places.

        Ruled 2026-09-09: `local` terminates. The prior behaviour was the bug,
        and anyone relying on it was relying on being deceived. See
        tests/gauntlet/test_local_never_reaches_cloud.py and
        docs/design/active/cloud-voice-providers.md section 10.0b.
        """
        monkeypatch.setattr(v, "_network_status", lambda: {"offline": False})
        monkeypatch.setattr(v, "resolve_gemini_key", lambda: {"valid": True})
        monkeypatch.setattr(v, "get_local_voice_engine", lambda: _FakeLocalEngine())
        monkeypatch.setattr(v, "_local_brain_ready", lambda: True)

        result = v._resolve_voice_engine(_settings(mode="local_preferred",
                                                    voice_engine="local"))
        assert result["engine"] != "gemini", (
            "the voice engine named local served a cloud provider because the "
            "ROUTING mode permitted it -- two different settings, and only one "
            "of them is the user's voice choice")

    def test_local_only_still_differs_from_local_preferred(self, monkeypatch):
        """The original test's real purpose, preserved.

        It existed to check the local_only fix had not over-reached into other
        routing modes. That check is still worth having; it just has to be made
        on a case where the modes genuinely still differ. They do: an EXPLICIT
        cloud choice is honoured under local_preferred and refused under
        local_only. C1 cuts both ways -- a user who asks for cloud gets cloud.
        """
        monkeypatch.setattr(v, "_network_status", lambda: {"offline": False})
        monkeypatch.setattr(v, "resolve_gemini_key", lambda: {"valid": True})
        monkeypatch.setattr(v, "get_local_voice_engine", lambda: _FakeLocalEngine())
        monkeypatch.setattr(v, "_local_brain_ready", lambda: True)
        monkeypatch.setattr(v, "validate_live_model", lambda *a, **k: {"ok": True},
                            raising=False)

        permitted = v._resolve_voice_engine(_settings(mode="local_preferred",
                                                       voice_engine="gemini"))
        assert permitted["engine"] == "gemini"

        refused = v._resolve_voice_engine(_settings(mode="local_only",
                                                     voice_engine="gemini"))
        assert refused["engine"] != "gemini"


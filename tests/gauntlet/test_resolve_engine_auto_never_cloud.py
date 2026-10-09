"""`auto` never resolves to a cloud engine (R4.1).

The local sibling of the existing `test_resolve_engine_local_only_never_cloud`
(D-AC3). That one guards the case where the user turned local-only ON; this one
guards the case where they did nothing at all except pick the option whose name
promises the system will choose sensibly.

Why this test exists rather than a comment: `auto` used to be allowed to reach
the cloud tier when a key was present and local-only was off. A setting that
lies to the person who chose it is a bug. A word that reads as "pick whichever
works" must not be able to mean "send your voice to a vendor."

If someone restores the cloud branch for `auto`, this file goes red.
"""
from __future__ import annotations

import pytest

from agent_friday.routes import voice as voice_routes


class TestAutoNeverReachesCloud:

    @pytest.fixture
    def _cloud_is_ready(self, monkeypatch):
        """The tempting case: a valid cloud key, online, local deps missing.

        This is the exact configuration under which the old code returned
        gemini. If `auto` is honest, it returns text-only here and says why.
        """
        monkeypatch.setattr(voice_routes, "_network_status",
                            lambda: {"offline": False})
        monkeypatch.setattr(voice_routes, "resolve_gemini_key",
                            lambda *a, **k: {"valid": True})

        class _DeadEngine:
            def available(self):
                return False

            def models_ready(self):
                return False

            def resolve_tier(self, settings=None):
                return "cpu"

        monkeypatch.setattr(voice_routes, "get_local_voice_engine",
                            lambda: _DeadEngine())
        return monkeypatch

    def test_auto_with_local_dead_and_cloud_ready_returns_text_only(
            self, _cloud_is_ready):
        out = voice_routes._resolve_voice_engine({"voice_engine": "auto"})
        assert out["engine"] != "gemini", (
            "`auto` promoted a local failure to the cloud without consent - "
            "the exact silent hop cloud-voice-providers.md section 6.3 forbids")
        assert out["engine"] == "demo"

    def test_the_refusal_explains_itself_and_offers_the_alternative(
            self, _cloud_is_ready):
        """A refusal with no reason is indistinguishable from a bug."""
        out = voice_routes._resolve_voice_engine({"voice_engine": "auto"})
        reason = (out.get("reason") or "").lower()
        assert "local" in reason
        assert "cloud" in reason, (
            "the user was not told the cloud path exists and is theirs to "
            "choose - C1 says these are peers among which the user chooses")

    def test_auto_prefers_local_when_local_works(self, monkeypatch):
        monkeypatch.setattr(voice_routes, "_network_status",
                            lambda: {"offline": False})
        monkeypatch.setattr(voice_routes, "resolve_gemini_key",
                            lambda *a, **k: {"valid": True})
        monkeypatch.setattr(voice_routes, "_local_brain_ready", lambda: True)

        class _LiveEngine:
            def available(self):
                return True

            def models_ready(self):
                return True

            def resolve_tier(self, settings=None):
                return "cpu"

        monkeypatch.setattr(voice_routes, "get_local_voice_engine",
                            lambda: _LiveEngine())
        out = voice_routes._resolve_voice_engine({"voice_engine": "auto"})
        assert out["engine"] == "local"
        assert "local only" in (out.get("reason") or "").lower(), (
            "the reason string must state the scope, because the label the "
            "user picked says 'Automatic (local only)'")

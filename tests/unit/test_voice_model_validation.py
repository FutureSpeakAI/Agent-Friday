"""H8 — the Gemini Live model-id validator.

A stale/renamed Live model id is the #1 cause of the opaque "voice is broken"
report (a connect failure that reads like an auth error). validate_live_model
catches it up front so it surfaces in Settings → Voice and the boot log.
"""
from __future__ import annotations

from agent_friday.services import voice_engine as ve


class TestValidateLiveModel:
    def test_known_primary_ok(self):
        r = ve.validate_live_model(ve.LIVE_MODEL)
        assert r["ok"] is True and r["status"] == "ok"

    def test_known_fallbacks_ok(self):
        assert ve.validate_live_model(ve.LIVE_MODEL_FALLBACK)["ok"]
        assert ve.validate_live_model(ve.LIVE_MODEL_FALLBACK2)["ok"]

    def test_live_family_pattern_ok(self):
        # An unseen-but-plausible Live model (matches the -live/native-audio
        # marker) is accepted — we don't want to reject legitimate new models.
        assert ve.validate_live_model("gemini-9.9-flash-live-preview")["ok"]
        assert ve.validate_live_model("gemini-x-native-audio-preview")["ok"]

    def test_typo_flagged_unknown(self):
        r = ve.validate_live_model("gemini-2.5-flash")  # text model, not Live
        assert r["ok"] is False
        assert r["status"] == "unknown"
        assert "isn't a recognized" in r["detail"]

    def test_empty_flagged(self):
        r = ve.validate_live_model("")
        assert r["ok"] is False and r["status"] == "empty"

    def test_whitespace_stripped(self):
        assert ve.validate_live_model(f"  {ve.LIVE_MODEL}  ")["ok"]

    def test_uses_settings_when_no_arg(self, monkeypatch):
        monkeypatch.setattr(ve, "_load_settings",
                            lambda: {"voice_model": "totally-made-up-model"})
        r = ve.validate_live_model()
        assert r["ok"] is False and r["status"] == "unknown"

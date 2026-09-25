"""Q8: one standard for both vendors, or neither means anything.

The Q2 rule is "where GA cannot be determined, exclude." Inworld's GA status
cannot be determined - Inworld's product docs present TTS-2 as production while
Inworld's OWN comparison material and Artificial Analysis label it "Research
Preview". A vendor contradicting itself is worse evidence than silence.

So Inworld ships nothing. It is registered, visible, and unselectable with the
real reason. The integration code stays so enabling it later is a configuration
change rather than a rebuild.

These tests exist to stop the feature being quietly re-enabled because someone
wanted it, and equally to stop the integration being deleted as dead code.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cloud_voice


class TestInworldIsNotSelectable:

    def test_ga_is_not_established_for_inworld(self):
        assert cloud_voice.ga_established("inworld") is False
        assert cloud_voice.ga_established("elevenlabs") is True

    def test_inworld_is_visible_but_unselectable(self, monkeypatch):
        """Visible, not hidden: a hidden capability is undiscoverable.

        Unselectable, not selectable-with-a-warning: a warning the user can
        click past is not a standard.
        """
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "a_valid_key")
        rows = {r["name"]: r for r in cloud_voice.available_providers({})}
        assert "inworld" in rows, "Inworld was hidden rather than disclosed"
        assert rows["inworld"]["selectable"] is False

    def test_the_reason_given_is_the_real_one(self, monkeypatch):
        """A valid key must not produce a misleading 'needs a key' message.

        Precedence matters: telling a user with a working key that they need a
        key would send them off to fix the wrong thing.
        """
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "a_valid_key")
        rows = {r["name"]: r for r in cloud_voice.available_providers({})}
        reason = rows["inworld"]["reason"] or ""
        assert "key" not in reason.lower(), (
            "a GA-unestablished provider reported a key problem instead of the "
            "actual reason")
        assert "research preview" in reason.lower()
        assert "Q8" in reason

    def test_resolve_provider_refuses_a_persisted_inworld_selection(self):
        """A settings file from a build where Inworld was enabled must not win."""
        assert cloud_voice.resolve_provider(
            {"voice_engine": "inworld", "inworld_api_key": "k"}) is None
        assert cloud_voice.resolve_provider(
            {"voice_engine": "cloud:inworld"}) is None

    def test_synthesize_refuses_before_any_network_work(self, monkeypatch):
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "a_valid_key")
        called = []
        monkeypatch.setattr(cloud_voice, "_synth_inworld",
                            lambda *a, **k: called.append(1))
        monkeypatch.setattr(cloud_voice, "gate_synthesis_input",
                            lambda t, p: t)
        with pytest.raises(cloud_voice.CloudVoiceUnavailable) as exc:
            cloud_voice.synthesize("hello", provider="inworld", settings={})
        assert exc.value.code == "cloud_voice_ga_unestablished"
        assert called == [], "an unestablished provider was contacted"
        assert exc.value.offer

    def test_elevenlabs_is_unaffected(self, monkeypatch):
        """One honest provider still ships. Excluding Inworld is not a retreat."""
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "sk_valid_shape")
        rows = {r["name"]: r for r in cloud_voice.available_providers({})}
        assert rows["elevenlabs"]["selectable"] is True
        assert cloud_voice.resolve_provider(
            {"voice_engine": "elevenlabs"}) == "elevenlabs"


class TestTheIntegrationSurvives:
    """Enabling Inworld later must be a config change, not a rebuild."""

    def test_flipping_the_flag_makes_inworld_selectable(self, monkeypatch):
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "a_valid_key")
        monkeypatch.setitem(cloud_voice.GA_ESTABLISHED, "inworld", True)
        rows = {r["name"]: r for r in cloud_voice.available_providers({})}
        assert rows["inworld"]["selectable"] is True
        assert cloud_voice.resolve_provider(
            {"voice_engine": "inworld"}) == "inworld"

    def test_the_client_metering_and_terms_handling_all_still_exist(self):
        """Guards against someone deleting the integration as dead code."""
        assert cloud_voice.INWORLD_MODELS
        assert callable(cloud_voice._synth_inworld)
        assert cloud_voice.audio_is_durable("inworld") is False
        assert cloud_voice.meter_model_id(
            "inworld", "inworld-tts-2",
            {"inworld_plan_tier": "growth"}) == "inworld-tts-2:growth"

    def test_q3_remains_a_separate_blocker_from_q8(self, monkeypatch):
        """Resolving GA must NOT silently make the audio durable.

        Two independent questions. Conflating them is exactly how a
        half-answered legal review turns into shipped audio.
        """
        monkeypatch.setitem(cloud_voice.GA_ESTABLISHED, "inworld", True)
        assert cloud_voice.audio_is_durable("inworld") is False, (
            "enabling Inworld's GA flag also made its audio durable; Q3's "
            "Outputs contradiction is a separate blocker")
        assert "Q3" in (cloud_voice.legal_review_required("inworld") or "")


class TestNonDurableAudioIsNotCached:
    """Q3 tightened: 'we don't archive it' and 'it isn't cached' differ."""

    def _client(self, monkeypatch, durable, provider):
        from flask import Flask
        from agent_friday.routes import cloud_voice_routes as cvr

        class _R:
            audio = b"RIFFfake"
            mime = "audio/wav"
            model = "m"
            chars = 5
            duration_ms = 1
            cost_usd = 0.001
            priced = True

        _R.provider = provider
        _R.durable = durable
        _R.notes = []
        monkeypatch.setattr(cvr.cloud_voice, "synthesize",
                            lambda *a, **k: _R())
        app = Flask(__name__)
        app.register_blueprint(cvr.cloud_voice_bp)
        return app.test_client()

    def test_non_durable_response_is_no_store(self, monkeypatch):
        client = self._client(monkeypatch, durable=False, provider="inworld")
        resp = client.post("/api/voice/cloud/tts", json={"text": "hi"})
        assert resp.status_code == 200
        assert "no-store" in resp.headers.get("Cache-Control", "")
        assert resp.headers["X-Friday-Voice-Durable"] == "0"

    def test_durable_response_is_not_forced_no_store(self, monkeypatch):
        """The restriction is targeted, not blanket - it means something."""
        client = self._client(monkeypatch, durable=True, provider="elevenlabs")
        resp = client.post("/api/voice/cloud/tts", json={"text": "hi"})
        assert "no-store" not in resp.headers.get("Cache-Control", "")
        assert resp.headers["X-Friday-Voice-Durable"] == "1"

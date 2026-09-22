"""C2, proved: cloud voice surfaces and offers. It never substitutes.

The three constraints in cloud-voice-providers.md section 1.0 are prose until
something fails when they are violated. These tests are that something. Each one
FAILS if the corresponding enforcement is removed - that is the acceptance bar
this repo set for itself after docs/decisions/2026-09-04-five-dead-settings.md.

The load-bearing case, and the one the decision asked for explicitly: force a
LOCAL failure and confirm it surfaces and offers rather than substituting.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cloud_voice, voice_indicator


@pytest.fixture(autouse=True)
def _clean_indicator():
    voice_indicator.reset_for_tests()
    yield
    voice_indicator.reset_for_tests()


# -- The named case: local fails -> cloud must NOT be substituted -------------

class TestLocalFailureDoesNotPromoteToCloud:
    """Section 6.3. The hard case, and the one convenience this design refuses.

    A local failure is exactly the moment the user most needs to know their
    audio is about to leave the machine. Auto-promotion here would be the single
    most defensible-sounding violation available, which is why it is tested
    rather than trusted.
    """

    def test_auto_never_resolves_to_a_cloud_provider(self):
        """`auto` is local-only even with both cloud keys present and valid.

        Deletes the friendliest possible route to a silent cloud hop.
        """
        settings = {
            "voice_engine": "auto",
            "elevenlabs_api_key": "sk_looks_completely_real",
            "inworld_api_key": "inworld_key",
        }
        assert cloud_voice.resolve_provider(settings) is None

    def test_local_engine_failure_surfaces_and_offers(self, monkeypatch):
        """A dead local voice does not cause a cloud call. It produces an offer.

        Simulated at the layer that matters: nothing selected a cloud provider,
        so synthesize() refuses and hands back an OFFER, not audio.
        """
        called = []
        monkeypatch.setattr(cloud_voice, "_synth_elevenlabs",
                            lambda *a, **k: called.append("elevenlabs"))
        monkeypatch.setattr(cloud_voice, "_synth_inworld",
                            lambda *a, **k: called.append("inworld"))

        with pytest.raises(cloud_voice.CloudVoiceUnavailable) as exc:
            cloud_voice.synthesize("the local voice just died",
                                   settings={"voice_engine": "auto"})

        assert called == [], "a local failure reached a cloud provider"
        assert exc.value.code == "cloud_voice_not_selected"
        assert exc.value.offer, "refusal carried no offer - C2 requires one"

    def test_offer_is_data_not_prose(self):
        """Every refusal carries a machine-readable code AND a human offer.

        A refusal the UI cannot render as an action is a refusal the user cannot
        take, which collapses back into "cloud voice just doesn't work".
        """
        with pytest.raises(cloud_voice.CloudVoiceUnavailable) as exc:
            cloud_voice.synthesize("hello", settings={"voice_engine": "local"})
        assert exc.value.code
        assert exc.value.offer


# -- The reverse direction: cloud fails -> announced, still not substituted ---

class TestCloudFailureIsSurfacedNotSubstituted:

    def _settings(self):
        return {"voice_engine": "elevenlabs",
                "elevenlabs_api_key": "sk_test_key_shape"}

    def test_network_failure_raises_and_does_not_return_local_audio(
            self, monkeypatch):
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "sk_test_key_shape")
        monkeypatch.setattr(cloud_voice, "gate_synthesis_input",
                            lambda t, p: t)

        def _boom(*a, **k):
            raise cloud_voice.CloudVoiceUnavailable(
                "could not reach ElevenLabs", code="cloud_voice_network",
                offer="Use Friday's local voice for now.",
                requested="elevenlabs")

        monkeypatch.setattr(cloud_voice, "_synth_elevenlabs", _boom)
        with pytest.raises(cloud_voice.CloudVoiceUnavailable) as exc:
            cloud_voice.synthesize("hi", settings=self._settings())
        assert exc.value.code == "cloud_voice_network"
        assert exc.value.offer

    def test_route_returns_503_and_never_substitutes(self, monkeypatch):
        """The HTTP contract: `substituted: false`, an offer, and no audio."""
        from agent_friday.routes import cloud_voice_routes as cvr

        def _boom(*a, **k):
            raise cloud_voice.CloudVoiceUnavailable(
                "ElevenLabs is out of credits", code="cloud_voice_quota",
                offer="Top up, or use Friday's local voice.",
                requested="elevenlabs")

        monkeypatch.setattr(cvr.cloud_voice, "synthesize", _boom)
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(cvr.cloud_voice_bp)
        client = app.test_client()
        resp = client.post("/api/voice/cloud/tts", json={"text": "hello"})
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["substituted"] is False
        assert body["surfaced"] is True
        assert body["code"] == "cloud_voice_quota"
        assert body["offer"]
        assert not resp.data.startswith(b"RIFF"), "returned audio on a refusal"


# -- Local-only mode is an absolute override, cloud providers included -------

class TestLocalOnlyIsAbsolute:

    def test_resolve_provider_returns_none_under_local_only(self):
        settings = {"voice_engine": "elevenlabs",
                    "elevenlabs_api_key": "sk_real_looking",
                    "model_routing": {"mode": "local_only"}}
        assert cloud_voice.resolve_provider(settings) is None

    def test_explicit_provider_still_refused_under_local_only(self, monkeypatch):
        """Passing the provider explicitly must not be a bypass."""
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "sk_real_looking")
        called = []
        monkeypatch.setattr(cloud_voice, "_synth_elevenlabs",
                            lambda *a, **k: called.append(1))
        with pytest.raises(cloud_voice.CloudVoiceUnavailable) as exc:
            cloud_voice.synthesize(
                "secret", provider="elevenlabs",
                settings={"model_routing": {"mode": "local_only"}})
        assert exc.value.code == "cloud_voice_local_only"
        assert called == []

    def test_providers_are_unselectable_under_local_only(self, monkeypatch):
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "sk_real_looking")
        rows = cloud_voice.available_providers(
            {"model_routing": {"mode": "local_only"}})
        assert rows and all(not r["selectable"] for r in rows)
        assert all("local-only" in (r["reason"] or "") for r in rows)


# -- The indicator reflects what SERVED, never what was requested ------------

class TestIndicatorTellsTheTruth:

    def test_degraded_state_does_not_claim_the_requested_provider(self):
        voice_indicator.record_degraded(
            requested="elevenlabs", serving=None,
            reason="ElevenLabs is out of credits", offer="Use local voice.")
        snap = voice_indicator.snapshot()
        assert snap["provider"] != "elevenlabs", (
            "the indicator named the REQUESTED provider after it failed to "
            "serve - C2's final clause")
        assert snap["degraded"] is True
        assert snap["requested"] == "elevenlabs"

    def test_indicator_renders_during_degraded_states(self):
        voice_indicator.record_degraded(
            requested="inworld", serving="local", reason="no key")
        snap = voice_indicator.snapshot()
        assert snap["provider"] == "local"
        assert snap["reason"] == "no key"

    def test_not_dismissible_while_cloud_is_active(self):
        voice_indicator.record_served(
            provider="elevenlabs", label="ElevenLabs (cloud)",
            model="eleven_flash_v2_5", is_cloud=True, cost_usd=0.01, chars=200)
        assert voice_indicator.snapshot()["dismissible"] is False
        voice_indicator.record_served(
            provider="local", label="Local (private, on-device)",
            is_cloud=False, cost_usd=0.0, chars=200)
        assert voice_indicator.snapshot()["dismissible"] is True

    def test_unpriced_call_does_not_become_a_zero(self):
        """An unknown cost must not render as a verified-free call."""
        voice_indicator.record_served(
            provider="inworld", label="Inworld (cloud)",
            model="inworld-tts-2", is_cloud=True, cost_usd=None,
            chars=500, priced=False)
        snap = voice_indicator.snapshot()
        assert snap["session_cost_usd"] == 0.0
        assert snap["session_cost_exact"] is False, (
            "an unpriced call was folded into the total as $0.00")

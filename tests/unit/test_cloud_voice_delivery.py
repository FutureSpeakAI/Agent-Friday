"""Speech pace reaches native provider synthesis without changing its owner."""
import base64
from types import SimpleNamespace

import pytest
import requests

from agent_friday.services import cloud_voice, voice_delivery


@pytest.fixture
def transport(monkeypatch):
    events = []
    monkeypatch.setattr(cloud_voice, "_api_key", lambda provider: "sk_synthetic_voice_fixture")  # pragma: allowlist secret -- synthetic test credential
    monkeypatch.setattr(cloud_voice, "_meter", lambda *args: None)
    monkeypatch.setattr(voice_delivery, "settings_snapshot",
                        lambda: pytest.fail("synthesis reloaded voice settings"))

    def gate(text, provider):
        events.append(("gate", provider, text))
        return text

    def post(url, **kwargs):
        events.append(("post", url, kwargs["json"]))
        return SimpleNamespace(status_code=200, content=b"ID3-fixture",
                               json=lambda: {"audioContent": base64.b64encode(b"RIFF-fixture").decode()})

    monkeypatch.setattr(cloud_voice, "gate_synthesis_input", gate)
    monkeypatch.setattr(requests, "post", post)
    return events


@pytest.mark.parametrize("model", tuple(cloud_voice.ELEVENLABS_GA_MODELS))
@pytest.mark.parametrize("pace,speed", [("measured", .9), ("natural", 1.0), ("brisk", 1.08)])
def test_saved_pace_reaches_elevenlabs_native_synthesis(transport, model, pace, speed):
    text = "Here is the explanation."
    result = cloud_voice.synthesize(text, provider="elevenlabs", model=model,
                                   voice_id="fixture_voice",
                                   settings={"voice_speaking_pace": pace})
    assert transport[0] == ("gate", "elevenlabs", text)
    assert transport[1][1].endswith("/text-to-speech/fixture_voice")
    assert transport[1][2] == {"text": text, "model_id": model,
                               "voice_settings": {"speed": speed}}
    assert result.audio == b"ID3-fixture" and result.mime == "audio/mpeg"
    assert result.model == model and result.voice_id == "fixture_voice"


def test_bound_call_pace_overrides_saved_pace_and_does_not_leak(transport):
    saved = {"voice_speaking_pace": "brisk"}
    with voice_delivery.using_preferences({"voice_speaking_pace": "measured"}):
        cloud_voice.synthesize("First call.", provider="elevenlabs", settings=saved)
    cloud_voice.synthesize("Another call.", provider="elevenlabs", settings=saved)
    payloads = [event[2] for event in transport if event[0] == "post"]
    assert [p["voice_settings"]["speed"] for p in payloads] == [.9, 1.08]
    assert saved == {"voice_speaking_pace": "brisk"}


def test_adaptive_pace_gives_dense_speech_more_time(transport):
    for text in ("A simple explanation.", "The estimate is 18.25 percent, up from 12.75 percent."):
        cloud_voice.synthesize(text, provider="elevenlabs",
                               settings={"voice_speaking_pace": "adaptive"})
    payloads = [event[2] for event in transport if event[0] == "post"]
    assert [p["voice_settings"]["speed"] for p in payloads] == [.98, .93]


def test_absent_pace_preserves_provider_voice_settings(transport):
    cloud_voice.synthesize("Hello.", provider="elevenlabs", settings={})
    assert "voice_settings" not in transport[1][2]


@pytest.mark.parametrize("pace,speed", [("measured", .9), ("natural", 1.0), ("brisk", 1.08)])
def test_inworld_native_rate_keeps_its_wav_contract(monkeypatch, transport, pace, speed):
    # Exercise transport plumbing without enabling this provider in production.
    monkeypatch.setitem(cloud_voice.GA_ESTABLISHED, "inworld", True)
    result = cloud_voice.synthesize("One thought.", provider="inworld",
                                   voice_id="fixture_voice", model="inworld-tts-2-flash",
                                   settings={"voice_speaking_pace": pace})
    assert transport[0] == ("gate", "inworld", "One thought.")
    assert transport[1][2] == {"text": "One thought.", "voiceId": "fixture_voice",
                               "modelId": "inworld-tts-2-flash",
                               "audioConfig": {"audioEncoding": "LINEAR16", "speakingRate": speed}}
    assert result.audio == b"RIFF-fixture" and result.mime == "audio/wav"
    assert result.durable is False


def test_pace_never_bypasses_the_egress_gate(monkeypatch, transport):
    def refuse(text, provider):
        raise cloud_voice.CloudVoiceUnavailable("Withheld.", code="cloud_voice_withheld")

    monkeypatch.setattr(cloud_voice, "gate_synthesis_input", refuse)
    with pytest.raises(cloud_voice.CloudVoiceUnavailable):
        cloud_voice.synthesize("Private fixture.", provider="elevenlabs",
                               settings={"voice_speaking_pace": "measured"})
    assert transport == []


def test_ignored_native_speed_is_detected(monkeypatch, transport):
    original = cloud_voice._synth_elevenlabs

    def ignored(text, key, model, voice, *, speed=None):
        return original(text, key, model, voice)

    monkeypatch.setattr(cloud_voice, "_synth_elevenlabs", ignored)
    with pytest.raises(AssertionError):
        test_saved_pace_reaches_elevenlabs_native_synthesis(
            transport, "eleven_multilingual_v2", "measured", .9)

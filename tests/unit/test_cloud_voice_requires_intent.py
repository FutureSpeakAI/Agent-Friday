"""Text sent to ElevenLabs or Inworld goes through the owner-engine checkpoint.

The cloud voice is an engine the owner picked (or an explicit speak_text request),
so the owner's choice is the intent in every mode except local_only, which
refuses with the fixed sentence and sends nothing. Synthetic data; no network.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cloud_intent as ci
from agent_friday.services import cloud_voice as cv
from agent_friday.services import elevenlabs_tools as el


@pytest.fixture(autouse=True)
def _clean():
    ci.clear()
    ci.forget_recent()
    yield
    ci.clear()


@pytest.fixture
def mode(monkeypatch):
    def _set(name):
        monkeypatch.setattr(ci, "routing_mode", lambda: name)
    return _set


@pytest.fixture
def wire(monkeypatch):
    import requests
    sent = []

    class _R:
        status_code = 200
        content = b"audio"
        text = ""

        def json(self):
            return {"audioContent": ""}

    def _post(*a, **k):
        sent.append((a, k))
        return _R()
    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(requests, "request", _post)
    return sent


def test_local_only_refuses_elevenlabs_synthesis_and_sends_nothing(mode, wire):
    mode("local_only")
    with pytest.raises(cv.CloudVoiceUnavailable) as ei:
        cv._synth_elevenlabs("hello", "k", "m", "v")
    assert ei.value.code == "cloud_voice_local_only" and "Nothing was sent" in str(ei.value)
    assert wire == []


def test_local_only_refuses_inworld_synthesis_and_sends_nothing(mode, wire):
    mode("local_only")
    with pytest.raises(cv.CloudVoiceUnavailable):
        cv._synth_inworld("hello", "k", "m", "v")
    assert wire == []


def test_local_only_refuses_the_elevenlabs_tools(mode, wire):
    mode("local_only")
    resp, err = el._request("GET", "/voices", key="k")
    assert resp is None and "Nothing was sent" in err
    assert wire == []


@pytest.mark.parametrize("m", ["cloud_only", "cloud_preferred", "local_preferred"])
def test_other_modes_pass_on_the_owners_engine_choice_and_leave_a_receipt(mode, wire, m):
    mode(m)
    audio, mime = cv._synth_elevenlabs("hello", "k", "m", "v")
    assert audio == b"audio" and len(wire) == 1
    last = ci.recent(1)[0]
    assert last["intent"] == "user_explicit" and last["provider"] == "elevenlabs"
    resp, err = el._request("GET", "/voices", key="k")
    assert resp is not None

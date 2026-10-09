"""Gemini TTS honours Local only even when the settings cannot be read.

A read error is not permission: the spoken text stays on this computer, or is
not spoken at all, exactly as when Local only is on.
"""
from __future__ import annotations

import io

import pytest

import agent_friday.services.voice_engine as ve

pytestmark = pytest.mark.real_provider_paths

TEXT = "The weather tomorrow looks clear with a high of 78."


class _Recorder:
    def __init__(self, result):
        self.texts = []
        self._result = result

    def __call__(self, text, *a, **k):
        self.texts.append(text)
        return self._result


def _unreadable():
    raise OSError("settings file locked")


def test_unreadable_settings_speak_locally_not_through_gemini(monkeypatch):
    local = _Recorder(io.BytesIO(b"RIFFlocal"))
    gemini = _Recorder(io.BytesIO(b"RIFFgemini"))
    monkeypatch.setattr(ve, "_synthesize_tts_wav_local", local)
    monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini", gemini)
    monkeypatch.setattr(ve, "_load_settings", _unreadable)

    buf = ve._synthesize_tts_wav(TEXT)

    assert buf.getvalue() == b"RIFFlocal"
    assert gemini.texts == []


def test_unreadable_settings_and_no_local_voice_refuse_rather_than_reach_gemini(monkeypatch):
    local = _Recorder(None)
    gemini = _Recorder(io.BytesIO(b"RIFFgemini"))
    monkeypatch.setattr(ve, "_synthesize_tts_wav_local", local)
    monkeypatch.setattr(ve, "_synthesize_tts_wav_gemini", gemini)
    monkeypatch.setattr(ve, "_load_settings", _unreadable)

    with pytest.raises(RuntimeError, match="refusing to send spoken text to Gemini TTS"):
        ve._synthesize_tts_wav(TEXT)
    assert gemini.texts == []

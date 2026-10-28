"""How sure the ear was, and putting a mishearing right (north star NS-22.2-6
"UI shows transcription confidence" and NS-22.4-3 "correction of transcription").

faster-whisper says how sure it is of each segment (avg_logprob, no_speech_prob);
the local ear threw that away and kept only the words. Now the confidence is
kept, travels with the words through the voice session (input_transcript and
voice_turn_done carry it), and the chat shows it under what you said, warns
when it is low, and offers "Correct": what you meant goes to the call as the
next thing said, and the misheard words are marked corrected.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from tests.unit import test_voice_session as tvs

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """As in test_voice_session: no settings read, and every session closed (its speaker thread ended)."""
    from agent_friday.services import off_record, voice_delivery
    monkeypatch.setattr(off_record, "_settings", lambda: {})
    monkeypatch.setattr(voice_delivery, "settings_snapshot", lambda: {})
    yield
    for s in tvs._OPEN:
        try:
            s.close()
            s._speaker.join(1.5)
        except Exception:
            pass
    del tvs._OPEN[:]


def _lv():
    # Imported in each test: on code without heard_confidence every test fails on its own.
    from agent_friday.services import local_voice
    return local_voice


def test_confidence_is_the_segments_probability_weighted_by_length():
    lv = _lv()
    assert hasattr(lv, "heard_confidence"), "the ear has no confidence helper"
    segs = [NS(text=" Book a table", avg_logprob=math.log(0.9), no_speech_prob=0.0),
            NS(text=" for two", avg_logprob=math.log(0.5), no_speech_prob=0.0)]
    want = (0.9 * len("Book a table") + 0.5 * len("for two")) / (len("Book a table") + len("for two"))
    assert lv.heard_confidence(segs) == round(want, 2)
    # A segment the model thinks is not speech counts for less; nothing to judge is None.
    assert lv.heard_confidence([NS(text=" hm", avg_logprob=0.0, no_speech_prob=0.75)]) == 0.25
    assert lv.heard_confidence([]) is None and lv.heard_confidence([NS(text=" ", avg_logprob=-0.1)]) is None


def test_the_whisper_ear_keeps_its_confidence():
    lv = _lv()
    asr = lv.WhisperASR.__new__(lv.WhisperASR)
    asr._model = NS(transcribe=lambda audio, **k: (iter([NS(text=" hello there", avg_logprob=math.log(0.4),
                                                               no_speech_prob=0.0)]), None))
    assert asr._run([0.0]) == "hello there"
    assert getattr(asr, "last_confidence", "absent") == 0.4


class _SureEar(tvs._Ear):
    def __init__(self, conf):
        super().__init__()
        self.last_confidence = conf


def _speak(ear):
    s, frames = tvs._session(["Done."], ear=ear)
    s.start()
    chunk = b"\x01\x00" * 1600                 # 100 ms
    for _ in range(5):
        s.feed_audio(chunk)
    s.vad.close()
    s.feed_audio(chunk)                          # endpoint
    for _ in range(150):
        if any(f["type"] == "voice_turn_done" for f in frames):
            break
        time.sleep(0.02)
    return frames


def test_the_words_carry_how_sure_the_ear_was():
    frames = _speak(_SureEar(0.42))
    it = [f for f in frames if f["type"] == "input_transcript"][0]
    done = [f for f in frames if f["type"] == "voice_turn_done"][0]
    assert it.get("confidence", "absent") == 0.42 and done.get("confidence", "absent") == 0.42


def test_an_ear_that_does_not_say_sends_none():
    frames = _speak(tvs._Ear())
    done = [f for f in frames if f["type"] == "voice_turn_done"][0]
    assert "confidence" in done and done["confidence"] is None


def test_typed_words_in_a_call_carry_no_confidence():
    s, frames = tvs._session(["Done."], ear=_SureEar(0.3), stream=False)
    s.start()
    s.run_turn("what I meant")
    done = [f for f in frames if f["type"] == "voice_turn_done"][0]
    assert done["user_text"] == "what I meant" and "confidence" in done and done["confidence"] is None


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/app.html"])
def test_the_chat_shows_it_and_offers_a_correction(rel):
    s = (ROOT / rel).read_text(encoding="utf-8")
    assert "const FRIDAY_HEARD_UNSURE = 0.6;" in s and "function HeardFix({ m, onFix })" in s
    fix = s[s.index("function HeardFix({ m, onFix })"):]
    fix = fix[:fix.index("\n}\n")]
    assert "' may have misheard this ('" in fix and "'Heard ' + Math.round(conf * 100) + '% sure'" in fix
    assert "'aria-label': 'Correct what ' + fridayName() + ' heard'" in fix and "'Send the correction'" in fix
    assert "'You corrected this to: \\u201C' + m.corrected + '\\u201D'" in fix
    # The correction goes to the live call, or with no call to the chat, and marks the misheard words.
    assert "setChatMsgs(p => p.map(x => x === msg ? { ...x, corrected: t } : x));" in s
    assert "window.fridayVoice.sendText(t))) sendChat(t, { skipForecast: true });" in s
    # Shown under every spoken message, with the confidence the call sent.
    assert ("m.role === 'user' && m.via === 'voice' && /*#__PURE__*/React.createElement(HeardFix" in s
            or "{m.role==='user'&&m.via==='voice'&&<HeardFix m={m} onFix={t=>fixHeard(m,t)}/>}" in s)
    assert ("confidence: typeof m.confidence === 'number' ? m.confidence : null" in s
            or "confidence:typeof m.confidence==='number'?m.confidence:null" in s)

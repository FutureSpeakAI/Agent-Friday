"""The turn ends sooner when the owner has finished a sentence, and never
cuts them off mid-sentence.

Measured live: warm first audio was 1.88 s, of which the wait for silence is
the single largest fixed part. The endpointer now reads the transcript so far
(the session hands it each partial): a finished sentence closes the turn after
a short quiet, an unfinished one waits longer than the configured wait, and
with nothing to read the configured wait applies.
"""
import struct

import pytest

from agent_friday.services import local_voice as lv
from agent_friday.services import voice_ear_stream as ves
from agent_friday.services import voice_session as vs

CHUNK_MS = 85
SAMPLES = int(16000 * CHUNK_MS / 1000)
LOUD = struct.pack("<%dh" % SAMPLES, *([9000, -9000] * (SAMPLES // 2)))
QUIET = b"\x00\x00" * SAMPLES


def _vad(silence_ms=500):
    return lv.VADEndpointer(silence_ms=silence_ms, use_silero=False)


def _speak(vad, hint, chunks=8):
    for _ in range(chunks):
        assert vad.feed(LOUD) is None
    vad.transcript_hint = hint


def _quiet_until_end(vad, limit_ms=2000):
    """Milliseconds of quiet before the endpoint fires, or None."""
    for i in range(1, int(limit_ms / CHUNK_MS) + 1):
        if vad.feed(QUIET) is not None:
            return i * CHUNK_MS
    return None


def test_a_complete_question_ends_the_turn_at_the_short_wait():
    v = _vad()
    _speak(v, "What's on my calendar today?")
    assert _quiet_until_end(v) <= 425


def test_a_pause_mid_sentence_does_not_cut_the_owner_off():
    v = _vad()
    _speak(v, "what's on my")
    waited = _quiet_until_end(v)
    assert waited is not None and waited >= 800, waited


def test_with_no_transcript_the_configured_wait_applies():
    v = _vad(silence_ms=500)
    _speak(v, None)
    waited = _quiet_until_end(v)
    assert 500 <= waited <= 600, waited


def test_an_unpunctuated_finished_request_counts_as_complete():
    v = _vad()
    _speak(v, "what's on my calendar today")
    assert _quiet_until_end(v) <= 425


def test_a_longer_configured_wait_is_never_shortened_below_the_short_wait():
    v = _vad(silence_ms=300)
    _speak(v, "what's on my calendar today?")
    waited = _quiet_until_end(v)
    assert waited is not None and waited <= 340 + CHUNK_MS


def test_the_hint_is_forgotten_when_the_utterance_ends():
    v = _vad()
    _speak(v, "What's on my calendar today?")
    assert _quiet_until_end(v) is not None
    assert v.transcript_hint is None


@pytest.mark.parametrize("text,complete", [
    ("What's on my calendar today?", True),
    ("Check my email.", True),
    ("what is the weather in", False),
    ("what's on my calendar and", False),
    ("tell me about, ", False),
    ("so I was thinking...", False),
    ("", False),
    ("hello", False),
    ("any news", False),
    ("any news from the office", True),
    ("show me the tasks for tomorrow", True),
    ("show me the", False),
])
def test_sentence_complete(text, complete):
    assert lv.sentence_complete(text) is complete


def test_the_session_hands_each_partial_to_the_endpointer():
    class _Vad:
        transcript_hint = None
        _in_speech = True
        _buf = bytearray(b"\x00\x00" * 1600)

        def feed(self, pcm):
            return None

        def flush(self):
            return None

        def reset(self):
            pass

    class _R:
        def create_stream(self):
            class S:
                def accept_waveform(self, *a):
                    pass

                def input_finished(self):
                    pass
            return S()

        def is_ready(self, s):
            return False

        def decode_stream(self, s):
            pass

        def get_result(self, s):
            return type("R", (), {"text": "what's on my calendar today"})()

    class _Mouth:
        name, device = "kokoro", "cpu"

        def synthesize_stream(self, text, cancel=None):
            yield b"\x00\x01" * 240

    vad = _Vad()
    s = vs.VoiceSession(lambda o: True, ear=None, mouth=_Mouth(), vad=vad,
                        generate=lambda *a: "", stream_ear=ves.StreamingEar(_R()))
    try:
        s.feed_audio(b"\x00\x00" * 1600)
    finally:
        s.close()
    assert vad.transcript_hint == "what's on my calendar today"

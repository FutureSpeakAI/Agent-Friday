"""The user's words survive a Gemini Live leg renewal.

A live call is a chain of ~10-minute Gemini connections. Everything the
browser sent while the next one was connecting used to be dropped, and so was
a request heard just before the old one closed: one question at a renewal got
no answer at all. A short seam now carries both across; a long one still drops
them, so Friday never answers speech the user has already moved past.
"""
import inspect
import struct

import agent_friday.routes.voice as rv


def _speech(ms=100):
    n = 16 * ms                                   # 16 kHz
    return struct.pack("<%dh" % n, *([3000, -3000] * (n // 2)))


def _silence(ms=100):
    return b"\x00\x00" * (16 * ms)


def test_unanswered_words_and_speech_cross_a_short_seam():
    text, audio = rv._seam_replay_plan("remind me what it is", False,
                                       [_silence(), _speech(), _speech()], 2.5)
    assert text == "remind me what it is"
    assert len(audio) == 3


def test_words_friday_already_began_answering_are_not_repeated():
    text, _ = rv._seam_replay_plan("what's the weather", True, [], 2.0)
    assert text is None


def test_a_long_gap_drops_everything():
    assert rv._seam_replay_plan("hello", False, [_speech()], 45.0) == (None, [])
    assert rv._seam_replay_plan("hello", False, [_speech()], None) == (None, [])


def test_silence_is_not_replayed():
    _, audio = rv._seam_replay_plan("", False, [_silence()] * 5, 1.0)
    assert audio == []


def test_replay_keeps_only_the_newest_audio():
    chunks = [_speech(1000) for _ in range(30)]          # 30 s buffered
    _, audio = rv._seam_replay_plan("", False, chunks, 3.0)
    assert sum(len(c) for c in audio) <= rv.SEAM_REPLAY_MAX_AUDIO_S * 32000
    assert audio and audio[-1] is chunks[-1]


def test_the_renewal_uses_the_plan_and_gates_the_text():
    src = inspect.getsource(rv)
    assert "_seam_replay_plan(" in src and "_seam_chunks.append(" in src
    assert "_gate_voice_text(_pending_txt)" in src
    assert "_leg_ended_ts[0] = _time.time()" in src

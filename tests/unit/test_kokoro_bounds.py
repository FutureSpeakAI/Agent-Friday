"""One Kokoro utterance is bounded in time and in length.

A synthesis call waiting on an overloaded GPU once ran for over 40 minutes
inside istftnet.inverse. Kokoro's own output never ran away on the names that
were being spoken (measured: 0.04-0.08 s of audio per phoneme), but a voice turn
that never ends is the same failure to the person listening. These tests use
fake pipelines: a wedged one, a runaway one, a failing one, and a normal one.
Every synthesize() call runs on a watchdog thread so a regression fails here
instead of hanging the suite.
"""
import threading
import time

import numpy as np
import pytest

from agent_friday.services import kokoro_voice as kv


def _tts(pipeline):
    t = kv.KokoroTTS()
    t._pipeline = pipeline          # load() returns early once a pipeline exists
    t._device = "fake"
    return t


def _call(tts, text, deadline=10.0):
    """synthesize() on a watchdog thread: (result, exception, seconds)."""
    out = {}

    def run():
        t0 = time.time()
        try:
            out["pcm"] = tts.synthesize(text)
        except BaseException as e:  # noqa: BLE001
            out["err"] = e
        out["took"] = time.time() - t0

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(deadline)
    if th.is_alive():
        pytest.fail("synthesize(%r) was still running after %.0fs: no time bound"
                    % (text, deadline))
    return out.get("pcm"), out.get("err"), out["took"]


def _second(n):
    return np.zeros(int(n * kv.KOKORO_NATIVE_RATE), dtype="float32")


@pytest.fixture
def small_budget(monkeypatch):
    monkeypatch.setattr(kv, "SYNTH_BUDGET_BASE_S", 0.4, raising=False)
    monkeypatch.setattr(kv, "SYNTH_BUDGET_PER_CHAR_S", 0.0, raising=False)


def test_a_wedged_synthesis_is_abandoned_at_its_budget(small_budget):
    release = threading.Event()

    def wedged(text, voice=None):
        release.wait(20)                 # a GPU that never answers
        yield "g", "ph", _second(1)

    tts = _tts(wedged)
    pcm, err, took = _call(tts, "Siobhan and Aoife are on the call.")
    assert isinstance(err, kv.KokoroUnavailable) and err.code == "local_voice_kokoro_timeout"
    assert took < 2.0, "the caller waited %.1fs" % took

    # While the abandoned call is still stuck, the next one refuses at once
    # instead of queueing behind it.
    pcm, err, took = _call(tts, "Tadhg")
    assert isinstance(err, kv.KokoroUnavailable) and err.code == "local_voice_kokoro_busy"
    assert took < 0.5

    # When the stuck call finally returns, the engine works again.
    release.set()
    time.sleep(0.3)
    tts._pipeline = lambda text, voice=None: iter([("g", "ph", _second(0.5))])
    pcm, err, _ = _call(tts, "Tadhg")
    assert err is None and len(pcm) == int(0.5 * kv.KOKORO_NATIVE_RATE) * 2


def test_runaway_audio_is_cut_to_the_phoneme_cap():
    phonemes = "kəkˈoɹo"                 # 7 phonemes: cap = 2 + 0.5*7 = 5.5 s

    def runaway(text, voice=None):
        yield "Kokoro", phonemes, _second(300)

    pcm, err, _ = _call(_tts(runaway), "Kokoro")
    assert err is None
    seconds = len(pcm) / 2 / kv.KOKORO_NATIVE_RATE
    cap = kv.MAX_AUDIO_BASE_S + kv.MAX_AUDIO_PER_PHONEME_S * len(phonemes)
    assert seconds <= cap + 0.01, "kept %.1fs of audio for %d phonemes" % (seconds, len(phonemes))


def test_ordinary_speech_is_untouched():
    def normal(text, voice=None):
        yield "Siobhan", "ʃɪvˈɔːn", _second(0.6)
        yield "and Aoife", "ænd ˈiːfə", _second(0.8)

    pcm, err, took = _call(_tts(normal), "Siobhan and Aoife")
    assert err is None and took < 1.0
    assert len(pcm) == (int(0.6 * kv.KOKORO_NATIVE_RATE) + int(0.8 * kv.KOKORO_NATIVE_RATE)) * 2


def test_a_failing_pipeline_still_names_itself():
    def broken(text, voice=None):
        raise TypeError("unknown word")
        yield  # pragma: no cover

    pcm, err, _ = _call(_tts(broken), "Aoife")
    assert isinstance(err, kv.KokoroUnavailable)
    assert err.code == "local_voice_kokoro_synthesis_failed"


def test_the_budget_is_generous_for_real_speech():
    """The budget must never trip a working engine: the slowest measured CPU
    run was 8 s for 280 characters; a 280-character sentence gets 85 s."""
    assert kv.synthesis_budget_s("x" * 280) >= 80
    assert kv.synthesis_budget_s("Tadhg") >= 15

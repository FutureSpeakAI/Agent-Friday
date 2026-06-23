"""Unit tests for the Tier-1 local voice engine (services/local_voice.py).

These run in CI with NO torch, NO GPU, and (typically) NO faster-whisper/piper
installed — exactly the philosophy of the suite. The real model backends are
swapped for fakes injected onto the engine the same way the suite stubs LLMs,
so the whole ASR→TTS orchestration is exercised without a model download.
"""
import array

from services import local_voice as lv
from services.local_voice import (
    LocalVoiceEngine,
    VADEndpointer,
    _pcm16_rms,
    _resample_pcm16,
    deps_status,
    split_sentences,
)


def _pcm(value, ms, rate=16000):
    """Make `ms` of mono PCM16 at `rate` with a constant sample value."""
    n = int(rate * ms / 1000)
    return array.array("h", [value] * n).tobytes()


# ── dependency probing ──────────────────────────────────────────────────────

def test_deps_status_shape():
    d = deps_status()
    assert set(d) == {"faster_whisper", "piper", "onnxruntime", "silero_vad"}
    assert all(isinstance(v, bool) for v in d.values())


# ── audio helpers ─────────────────────────────────────────────────────────────

def test_resample_upsamples_16k_to_24k():
    one_sec_16k = _pcm(1000, 1000, rate=16000)          # 16000 samples
    out = _resample_pcm16(one_sec_16k, 16000, 24000)
    n_out = len(out) // 2
    # ~24000 samples (1s at 24kHz), allow a small interpolation slack.
    assert 23900 <= n_out <= 24100


def test_resample_noop_when_rates_equal():
    pcm = _pcm(500, 100)
    assert _resample_pcm16(pcm, 24000, 24000) == pcm


def test_rms_distinguishes_speech_from_silence():
    assert _pcm16_rms(_pcm(0, 100)) == 0.0
    assert _pcm16_rms(_pcm(5000, 100)) > 600


# ── VAD endpointer ────────────────────────────────────────────────────────────

def test_vad_fires_after_trailing_silence():
    vad = VADEndpointer(silence_ms=800, start_rms=600, min_speech_ms=200)
    fired = None
    # 3 × 100ms of speech (loud), then silence until the 800ms trailing gate.
    for _ in range(3):
        assert vad.feed(_pcm(6000, 100)) is None
    for i in range(9):
        out = vad.feed(_pcm(0, 100))
        if out is not None:
            fired = out
            break
    assert fired is not None
    # The finalized utterance carries the speech we fed (plus trailing audio).
    assert len(fired) >= len(_pcm(6000, 300))


def test_vad_ignores_pure_silence():
    vad = VADEndpointer(silence_ms=800, start_rms=600)
    for _ in range(20):
        assert vad.feed(_pcm(0, 100)) is None


def test_vad_requires_min_speech():
    # A single short blip below min_speech_ms must not finalize a turn.
    vad = VADEndpointer(silence_ms=300, start_rms=600, min_speech_ms=500)
    vad.feed(_pcm(6000, 100))           # only 100ms of speech
    fired = any(vad.feed(_pcm(0, 100)) for _ in range(6))
    assert not fired


# ── sentence splitting (per-sentence TTS streaming) ───────────────────────────

def test_split_sentences_basic():
    assert split_sentences("Hello world. How are you?") == [
        "Hello world.", "How are you?"]


def test_split_sentences_strips_markdown():
    out = split_sentences("**Bold** and `code` here. Next one!")
    assert out == ["Bold and code here.", "Next one!"]
    assert "*" not in "".join(out) and "`" not in "".join(out)


def test_split_sentences_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


# ── engine orchestration with injected fakes ──────────────────────────────────

class FakeASR:
    def __init__(self):
        self.model_size = "fake"
        self.fed = []

    def load(self, progress=None):
        pass

    def transcribe(self, pcm16_16k):
        self.fed.append(len(pcm16_16k))
        return "hello friday"


class FakeTTS:
    """Returns a fixed 24kHz PCM16 sine-ish buffer sized to the text."""
    def __init__(self):
        self.voice = "fake"
        self.spoken = []

    def load(self, progress=None):
        pass

    def synthesize(self, text):
        self.spoken.append(text)
        n = max(1, len(text)) * 240        # ~10ms of 24kHz audio per char
        return array.array("h", [1000] * n).tobytes()


def _engine_with_fakes():
    eng = LocalVoiceEngine()
    eng._asr = FakeASR()
    eng._tts = FakeTTS()
    eng._ready = True
    return eng


def test_engine_transcribe_uses_injected_asr():
    eng = _engine_with_fakes()
    assert eng.transcribe(_pcm(6000, 500)) == "hello friday"
    assert eng._asr.fed  # ASR actually received audio


def test_engine_synthesize_returns_playback_pcm():
    eng = _engine_with_fakes()
    pcm = eng.synthesize("Hi there")
    assert isinstance(pcm, (bytes, bytearray)) and len(pcm) > 0
    # Even length → valid PCM16 frames the 24kHz worklet can play.
    assert len(pcm) % 2 == 0


def test_engine_synthesize_b64_roundtrips():
    import base64
    eng = _engine_with_fakes()
    b64 = eng.synthesize_b64("Hello")
    assert base64.b64decode(b64) == eng.synthesize("Hello")


def test_engine_health_shape_and_status():
    eng = LocalVoiceEngine()
    h = eng.health()
    assert h["engine"] == "local-voice-lite"
    assert h["status"] in ("ok", "needs_download", "missing")
    assert set(["available", "models_ready", "deps", "asr_model", "tts_voice"]) <= set(h)
    # available must agree with deps being importable.
    assert h["available"] == lv.deps_installed()


def test_module_health_never_raises():
    # The convenience wrapper used by health endpoints must be exception-proof.
    out = lv.local_voice_health()
    assert out["engine"] == "local-voice-lite"
    assert "status" in out

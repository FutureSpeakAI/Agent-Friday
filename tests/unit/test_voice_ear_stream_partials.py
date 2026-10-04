"""The streaming ear gives true partials while the owner speaks, and the
final at the endpoint is its flush, not a re-transcription (local voice spec
P2). A fake recognizer stands in for sherpa-onnx: it "recognises" one more
word per fed chunk.
"""
import threading
import time

from agent_friday.services import voice_ear_stream as ves
from agent_friday.services import voice_session as vs

WORDS = ["what's", "on", "my", "calendar", "today"]


class _Stream:
    def __init__(self):
        self.chunks = 0
        self.finished = False

    def accept_waveform(self, rate, samples):
        assert rate == 16000
        self.chunks += 1

    def input_finished(self):
        self.finished = True


class _Result:
    def __init__(self, text):
        self.text = text


class FakeRecognizer:
    def __init__(self):
        self.streams = []

    def create_stream(self):
        s = _Stream()
        self.streams.append(s)
        return s

    def is_ready(self, s):
        return False

    def decode_stream(self, s):
        pass

    def get_result(self, s):
        n = len(WORDS) if s.finished else min(s.chunks, len(WORDS) - 1)
        return _Result(" ".join(WORDS[:n]))


def test_partials_grow_become_stable_and_finish_flushes():
    ear = ves.StreamingEar(FakeRecognizer(), stable_chunks=2)
    pcm = b"\x00\x00" * 1600
    seen = [ear.feed(pcm) for _ in range(6)]
    texts = [e[0] for e in seen if e]
    assert texts[0] == "what's" and texts[-1].startswith("what's on my calendar")
    assert any(e and e[1] for e in seen), "a partial that stopped changing is stable"
    assert ear.finish() == "what's on my calendar today"
    assert ear.feed(pcm) == ("what's", False), "finish starts a fresh utterance"


class _Ear:
    name, device = "faster-whisper", "cpu"

    def __init__(self):
        self.calls = 0

    def transcribe(self, pcm):
        self.calls += 1
        return "whisper heard this"


class _VAD:
    """In speech for the first `speech` chunks, then endpoints once."""

    def __init__(self, speech=5):
        self.speech = speech
        self.n = 0
        self._buf = bytearray()
        self._in_speech = False

    def feed(self, pcm):
        self.n += 1
        if self.n <= self.speech:
            self._in_speech = True
            self._buf += pcm
            return None
        self._in_speech = False
        out, self._buf = bytes(self._buf), bytearray()
        return out

    def flush(self):
        return None


class _Mouth:
    name, device = "kokoro", "cpu"

    def synthesize_stream(self, text, cancel=None):
        yield b"\x00\x01" * 240


def test_the_session_streams_partials_and_turns_on_the_streamed_final():
    frames, heard = [], []
    whisper = _Ear()
    done = threading.Event()

    def gen(text, on_delta, cancel):
        heard.append((text, time.perf_counter()))
        done.set()
        return "Two meetings."
    s = vs.VoiceSession(lambda o: frames.append(dict(o, _t=time.perf_counter())) or True,
                        ear=whisper, mouth=_Mouth(), vad=_VAD(speech=5), generate=gen,
                        stream_ear=ves.StreamingEar(FakeRecognizer(), stable_chunks=2))
    try:
        for _ in range(5):
            s.feed_audio(b"\x00\x00" * 1600)
        partials = [f["text"] for f in frames if f["type"] == "partial_transcript"]
        assert partials, "no partial transcript reached the client during speech"
        t_end = time.perf_counter()
        s.feed_audio(b"\x00\x00" * 1600)            # the endpoint
        assert done.wait(3)
    finally:
        s.close()
    assert heard[0][0] == "what's on my calendar today"
    assert whisper.calls == 0, "the whisper ear re-transcribed a streamed utterance"
    assert (heard[0][1] - t_end) * 1000.0 <= 150.0, "final later than 150 ms after endpoint"


def test_an_empty_streamed_final_falls_back_to_the_whisper_ear():
    class _Silent(FakeRecognizer):
        def get_result(self, s):
            return _Result("")
    whisper = _Ear()
    s = vs.VoiceSession(lambda o: True, ear=whisper, mouth=_Mouth(), vad=_VAD(),
                        generate=lambda *a: "", stream_ear=ves.StreamingEar(_Silent()))
    try:
        text, _ms = s._final_transcript(b"\x00\x00" * 16000)
    finally:
        s.close()
    assert text == "whisper heard this" and whisper.calls == 1

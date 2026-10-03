"""A barge stops the GENERATION, not only the playback (local voice spec P0,
defect 6b), end to end: Escape -> VoiceSession.barge -> the cancel the mind
was handed -> the transport closes the seat's stream (the closed connection
is what makes llama-server free the slot) within 300 ms, and the next turn
starts without waiting for the cut one.

The mind here is the real transport (`model_router._consume_sse_completion`)
reading a slow fake SSE stream, bound the way the route binds it
(`TURN_CANCEL.set(cancel)`). No socket, no model.
"""
import json
import threading
import time

from agent_friday.services import model_router as mr
from agent_friday.services import voice_session as vs


def _sse(content):
    return "data: " + json.dumps({"choices": [{"delta": {"content": content},
                                               "finish_reason": None}]})


class _SlowStream:
    """A seat that streams a token every 20 ms for ten seconds."""

    def __init__(self):
        self.closed_at = None

    def iter_lines(self, decode_unicode=True):
        for i in range(500):
            if self.closed_at is not None:
                return
            time.sleep(0.02)
            yield _sse("word%d. " % i)

    def close(self):
        self.closed_at = time.perf_counter()


class _Mouth:
    name, device = "kokoro", "cpu"

    def synthesize_stream(self, text, cancel=None):
        yield b"\x00\x01" * 240


class _VAD:
    _buf = bytearray()
    _in_speech = False

    def feed(self, pcm):
        return None

    def flush(self):
        return None


def test_escape_closes_the_seat_stream_within_300_ms_and_frees_the_next_turn():
    streams = []
    started = threading.Event()

    def gen(text, on_delta, cancel):
        if text != "first":
            on_delta("Second. ")
            return "Second."
        resp = _SlowStream()
        streams.append(resp)
        tok = mr.TURN_CANCEL.set(cancel)
        try:
            started.set()
            out = mr._consume_sse_completion(resp, on_delta=on_delta)
        finally:
            mr.TURN_CANCEL.reset(tok)
        return out["choices"][0]["message"]["content"]

    frames = []
    s = vs.VoiceSession(lambda o: frames.append(o) or True, ear=object(),
                        mouth=_Mouth(), vad=_VAD(), generate=gen)
    try:
        th = threading.Thread(target=s.run_turn, args=("first",), daemon=True)
        th.start()
        assert started.wait(3)
        time.sleep(0.15)                       # mid-generation
        t_barge = time.perf_counter()
        s.handle({"type": "barge"})
        end = time.monotonic() + 2
        while streams[0].closed_at is None and time.monotonic() < end:
            time.sleep(0.005)
        assert streams[0].closed_at is not None, (
            "Escape stopped the audio but the seat kept generating")
        assert (streams[0].closed_at - t_barge) * 1000.0 <= 300.0
        th.join(2)
        assert not th.is_alive(), "the barged turn still holds the turn lock"
        # The next utterance runs at once.
        t0 = time.perf_counter()
        s.run_turn("second")
        assert (time.perf_counter() - t0) < 2.0
        assert any(f.get("type") == "text" and f.get("text") == "Second." for f in frames)
    finally:
        s.close()

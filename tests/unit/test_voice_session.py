"""The streamed local voice turn (clean-sheet §4.2, §6) without a socket, a
model or a GPU: fake ear/mouth engines, a fake mind that streams deltas, and
a recording `send`.
"""
import base64
import threading
import time

import pytest

from agent_friday.services import voice_session as vs
from agent_friday.services.voice_session import ClauseChunker, DeltaFilter


# ── ClauseChunker ────────────────────────────────────────────────────────────

def _run(chunker, pieces):
    out = []
    for p in pieces:
        out += chunker.feed(p)
    tail = chunker.flush()
    if tail:
        out.append(tail)
    return out


def test_clause_chunker():
    c = ClauseChunker()
    out = _run(c, ["Pulling that up now. ", "Your morning looks completely clear today, and Janet ",
                   "replied an hour ago! Anything else?"])
    assert out == ["Pulling that up now.", "Your morning looks completely clear today,",
                   "and Janet replied an hour ago!", "Anything else?"]
    # comma cuts only after >= 6 words
    assert _run(ClauseChunker(), ["Yes, I can. "]) == ["Yes, I can."]
    # hard cut at 12 words, at a whitespace boundary, never an empty clause
    long = " ".join(f"w{i}" for i in range(1, 30))
    out = _run(ClauseChunker(), [long])
    assert out[0] == " ".join(f"w{i}" for i in range(1, 13))
    assert all(o.strip() for o in out)
    assert " ".join(out) == long
    # decimals and clock times do not cut mid-token; a trailing period waits
    c = ClauseChunker()
    assert c.feed("It is 10:30 and 3.5 degrees") == []
    assert c.feed(".") == []
    assert c.feed(" Cold") == ["It is 10:30 and 3.5 degrees."]
    assert c.flush() == "Cold"
    assert ClauseChunker().flush() is None


def test_delta_filter_drops_channel_markup_but_keeps_the_announcement():
    f = DeltaFilter()
    got = "".join(f.feed(p) for p in ["Pulling that up now. <|tool_call>call:get_x{",
                                      "a:1}<tool_call|>", " Done."])
    got += f.flush()
    assert got == "Pulling that up now.  Done."
    # a marker split across deltas
    f = DeltaFilter()
    got = "".join(f.feed(p) for p in ["hello <", "|channel>thought<channel", "|> world"])
    assert got + f.flush() == "hello  world"
    # a lone '<' that is not a marker is kept
    f = DeltaFilter()
    assert f.feed("a <") == "a " and f.feed("b") == "<b"


# ── fakes ────────────────────────────────────────────────────────────────────

class _Ear:
    name = "faster-whisper"
    device = "cpu"

    def __init__(self):
        self.calls = []

    def transcribe(self, pcm):
        self.calls.append(len(pcm))
        return f"heard {len(pcm)}"


class _Mouth:
    name = "kokoro"
    device = "cpu"

    def __init__(self, fail_on=None, delay=0.0):
        self.fail_on = fail_on or set()
        self.delay = delay
        self.spoken = []

    def synthesize_stream(self, text, cancel=None):
        if any(k in text for k in self.fail_on):
            raise RuntimeError("g2p TypeError")
        self.spoken.append(text)
        for _ in range(3):
            if cancel is not None and cancel.is_set():
                return
            if self.delay:
                time.sleep(self.delay)
            yield b"\x00\x01" * 2400


class _Piper(_Mouth):
    name = "piper"


class _VAD:
    """Deterministic endpointer: every chunk is speech until `close()`."""

    def __init__(self):
        self._buf = bytearray()
        self._in_speech = False
        self._closing = False

    def feed(self, pcm):
        if self._closing:
            self._closing = False
            out = bytes(self._buf)
            self.reset()
            return out or None
        self._in_speech = True
        self._buf.extend(pcm)
        return None

    def flush(self):
        out = bytes(self._buf)
        self.reset()
        return out or None

    def reset(self):
        self._buf = bytearray()
        self._in_speech = False

    def close(self):
        self._closing = True


class _Receipt:
    def __init__(self):
        self.marks = []
        self.fields = {}
        self.outcome = None
        self.audio = 0

    def mark(self, stage, audio_ms=None):
        self.marks.append(stage)

    def set(self, **kw):
        self.fields.update(kw)

    def count_audio_out(self, n):
        self.audio += n

    def count_text_out(self, n):
        pass

    def done(self, outcome=None, code="", detail=""):
        self.outcome = outcome or ("served" if self.audio else "silent")


def _mind(script, stream=True, delay=0.0):
    """A fake `generate(user_text, on_delta, cancel)`."""
    def gen(user_text, on_delta, cancel):
        if stream:
            for piece in script:
                if cancel.is_set():
                    return "".join(script)
                on_delta(piece)
                if delay:
                    time.sleep(delay)
        return "".join(script)
    return gen


_OPEN: list = []


@pytest.fixture(autouse=True)
def _close_sessions():
    """Every session's speaker thread ends on close(); leaving them running
    trips tests/api/test_smoke.py::test_no_background_threads later in the
    same process."""
    yield
    for s in _OPEN:
        try:
            s.close()
            s._speaker.join(1.5)
        except Exception:
            pass
    del _OPEN[:]


def _session(script, *, mouth=None, fallback=None, ear=None, stream=True,
             delay=0.0, receipts=None, timings=None):
    frames = []
    r = receipts if receipts is not None else []
    hooks = {"receipt": lambda: r.append(_Receipt()) or r[-1]}
    if timings is not None:
        hooks["timings"] = lambda: timings
    s = vs.VoiceSession(lambda o: frames.append(o) or True,
                        ear=ear or _Ear(), mouth=mouth or _Mouth(),
                        fallback_mouth=fallback, vad=_VAD(),
                        generate=_mind(script, stream=stream, delay=delay),
                        hooks=hooks,
                        manifest_snapshot={"mode": "local", "ready": True},
                        contract={"tools": ["knowledge_query", "memory_recall"],
                                  "knowledge_graph": True, "memory": True})
    _OPEN.append(s)
    return s, frames


def _types(frames):
    return [f["type"] for f in frames]


# ── session start ────────────────────────────────────────────────────────────

def test_session_start_sends_manifest_and_contract_then_stage_lamps():
    s, frames = _session(["hi"])
    s.start()
    t = _types(frames)
    assert t[:3] == ["manifest", "contract", "context_reach"]
    assert frames[1]["tools"] == ["knowledge_query", "memory_recall"]
    assert frames[2]["full_context"] is True
    assert t.count("stage") == 3 and t[-1] == "status"
    assert s.state == "listening"


# ── the streamed turn ────────────────────────────────────────────────────────

def test_first_clause_speaks_while_the_model_is_still_writing():
    mouth = _Mouth()
    order = []
    script = ["Pulling that up now. ", "Your morning looks completely clear today, ",
              "and Janet replied. "]
    s, frames = _session(script, mouth=mouth, delay=0.05)
    s.start()
    s.run_turn("what's on tomorrow", audio_ms=1500)
    t = _types(frames)
    # audio for clause 1 arrived BEFORE the final `text` frame (i.e. before
    # the model finished), which is the whole point of the pipeline.
    assert t.index("audio") < t.index("text")
    assert mouth.spoken == ["Pulling that up now.", "Your morning looks completely clear today,",
                            "and Janet replied."]
    rec = [f for f in frames if f["type"] == "turn_receipt"][0]
    assert rec["clauses"] == 3 and rec["outcome"] == "served"
    assert rec["first_clause_ms"] is not None and rec["first_audio_ms"] is not None
    assert t[-3:] == ["turn_end", "voice_turn_done", "status"]
    assert frames[-2]["agent_text"] == "".join(script).strip()


def test_non_streaming_leg_still_speaks_the_whole_reply():
    mouth = _Mouth()
    s, frames = _session(["One sentence. Then another one!"], mouth=mouth, stream=False)
    s.start()
    s.run_turn("hi")
    assert mouth.spoken == ["One sentence.", "Then another one!"]


def test_tool_call_markup_is_never_spoken():
    mouth = _Mouth()
    script = ["Let me check. <|tool_call>call:query_calendar{}<tool_call|>",
              " Your day is clear."]
    s, frames = _session(script, mouth=mouth)
    s.start()
    s.run_turn("calendar?")
    assert mouth.spoken == ["Let me check.", "Your day is clear."]
    assert not any("tool_call" in x for x in mouth.spoken)


def test_receipt_carries_prefill_tokens_and_clauses():
    receipts = []
    s, frames = _session(["Okay. Done."], receipts=receipts,
                         timings={"prompt_n": 412, "predicted_n": 9})
    s.start()
    s.run_turn("go")
    r = receipts[-1]
    assert r.fields["prefill_tokens"] == 412
    assert r.fields["clauses"] == 2
    assert "first_brain_token" in r.marks and "first_audio_out" in r.marks
    assert r.outcome == "served"
    rec = [f for f in frames if f["type"] == "turn_receipt"][0]
    assert rec["prefill_tokens"] == 412


# ── clause fallback (§4.3) ───────────────────────────────────────────────────

def test_failed_clause_is_spoken_by_piper_with_one_notice_per_session():
    kok = _Mouth(fail_on={"Janet"})
    piper = _Piper()
    s, frames = _session(["Your morning is clear. Janet replied. Janet again."],
                         mouth=kok, fallback=piper)
    s.start()
    s.run_turn("hi")
    assert kok.spoken == ["Your morning is clear."]
    assert piper.spoken == ["Janet replied.", "Janet again."]
    notices = [f for f in frames if f["type"] == "error-nonfatal"]
    assert len(notices) == 1
    assert notices[0]["code"] == "local_voice_clause_fallback"
    assert "Piper" in notices[0]["message"]
    rec = [f for f in frames if f["type"] == "turn_receipt"][0]
    assert rec["engines"]["mouth"] == "kokoro (+piper fallback)"
    # second turn: same code, no second notice
    s.run_turn("again")
    assert len([f for f in frames if f["type"] == "error-nonfatal"]) == 1


# ── barge-in ─────────────────────────────────────────────────────────────────

def test_barge_cancels_queued_clauses_and_marks_the_receipt_aborted():
    mouth = _Mouth(delay=0.05)
    receipts = []
    script = ["Sentence one. Sentence two. Sentence three. Sentence four. Sentence five."]
    s, frames = _session(script, mouth=mouth, receipts=receipts)
    s.start()
    th = threading.Thread(target=s.run_turn, args=("talk",))
    th.start()
    # wait for the first audio frame, then interrupt
    for _ in range(200):
        if any(f["type"] == "audio" for f in frames):
            break
        time.sleep(0.01)
    s.handle({"type": "barge"})
    th.join(5.0)
    assert not th.is_alive()
    assert len(mouth.spoken) < 5
    t = _types(frames)
    assert "interrupted" in t
    rec = [f for f in frames if f["type"] == "turn_receipt"][0]
    assert rec["outcome"] == "aborted"
    assert receipts[-1].outcome == "aborted"


# ── chunked ear (§4.2) ───────────────────────────────────────────────────────

def test_partial_transcripts_stream_while_speaking_and_short_utterances_are_redone_whole():
    ear = _Ear()
    s, frames = _session(["ok."], ear=ear)
    s.start()
    s.chunk_s = 0.5
    s._chunk_bytes = int(0.5 * 16000 * 2)
    chunk = b"\x01\x00" * 1600                        # 100 ms
    for _ in range(12):                                # 1.2 s of speech
        s.feed_audio(chunk)
    partials = [f for f in frames if f["type"] == "partial_transcript"]
    assert len(partials) == 2                          # two 0.5 s windows
    assert partials[-1]["text"] == "heard 16000 heard 16000"
    s.vad.close()
    s.feed_audio(chunk)                                # endpoint
    for _ in range(100):
        if any(f["type"] == "voice_turn_done" for f in frames):
            break
        time.sleep(0.02)
    it = [f for f in frames if f["type"] == "input_transcript"][0]
    # <= 8 s: the whole utterance (12 x 100 ms; the closing chunk is not part of the utterance) was transcribed again, whole
    assert it["text"] == f"heard {12 * 3200}"
    assert ear.calls[-1] == 12 * 3200


def test_long_utterance_transcribes_only_the_tail_and_joins():
    ear = _Ear()
    s, frames = _session(["ok."], ear=ear)
    s.start()
    s.rejoin_s = 0.4                                   # everything is "long"
    s.chunk_s = 0.5
    s._chunk_bytes = int(0.5 * 16000 * 2)
    chunk = b"\x01\x00" * 1600
    for _ in range(12):
        s.feed_audio(chunk)
    s.vad.close()
    s.feed_audio(chunk)
    for _ in range(100):
        if any(f["type"] == "voice_turn_done" for f in frames):
            break
        time.sleep(0.02)
    it = [f for f in frames if f["type"] == "input_transcript"][0]
    tail = 12 * 3200 - 2 * 16000
    assert it["text"] == f"heard 16000 heard 16000 heard {tail}"


def test_end_flushes_and_closes():
    s, frames = _session(["bye."])
    s.start()
    s.feed_audio(b"\x01\x00" * 1600)
    s.handle({"type": "end"})
    assert s.done.is_set()
    assert any(f["type"] == "voice_turn_done" for f in frames)
    s.close()

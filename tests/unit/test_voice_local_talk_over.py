"""Talking over Friday on the LOCAL voice path stops her (clean-sheet §4.2
barge-in, §8.3 "Interrupts: audio stops within one clause").

The user's voice is told apart from Friday's own echo by the same
relative-to-bleed detector the Gemini Live bridge uses (`LiveBargeDetector`),
fed only while her voice is playing. A barge stops her audio at once, drops
the queued clauses, tells the mind to stop, and ends the turn without waiting
for the mind, so the next turn starts straight away. Escape works as before.

No socket, model or GPU: fake engines, a fake clock for the detector, and a
recording `send`.
"""
import base64
import inspect
import json
import pathlib
import re
import struct
import threading
import time

import pytest

from agent_friday.services import voice_session as vs

MS_PER_CHUNK = 170                    # the browser's ~171 ms mic frames
CHUNK_SAMPLES = 16 * MS_PER_CHUNK     # 16 kHz PCM16


def _tone(rms, samples=CHUNK_SAMPLES):
    """A chunk whose RMS is exactly `rms` (alternating +rms/-rms)."""
    a = int(rms)
    return struct.pack("<%dh" % samples, *([a, -a] * (samples // 2)))


def _audio_frame(pcm):
    return {"type": "audio", "data": base64.b64encode(pcm).decode("ascii")}


# ── fakes ────────────────────────────────────────────────────────────────────

class _Ear:
    name = "faster-whisper"
    device = "cpu"

    def transcribe(self, pcm):
        return "heard"


class _Mouth:
    name = "kokoro"
    device = "cpu"

    def __init__(self, delay=0.0, seconds=0.3):
        self.delay = delay
        self.piece = b"\x00\x01" * int(24000 * seconds / 3)
        self.spoken = []

    def synthesize_stream(self, text, cancel=None):
        self.spoken.append(text)
        for _ in range(3):
            if cancel is not None and cancel.is_set():
                return
            if self.delay:
                time.sleep(self.delay)
            yield self.piece


class _VAD:
    """Never endpoints: the talk-over is what these tests watch."""

    def __init__(self):
        self._buf = bytearray()
        self._in_speech = False

    def feed(self, pcm):
        return None

    def flush(self):
        return None


class _Receipt:
    def __init__(self):
        self.outcome = None
        self.detail = ""

    def mark(self, *a, **k):
        pass

    def set(self, **kw):
        pass

    def count_audio_out(self, n):
        pass

    def count_text_out(self, n):
        pass

    def done(self, outcome=None, code="", detail=""):
        self.outcome = outcome or "served"
        self.detail = detail


class _GpuQueue:
    def __init__(self):
        self.cancelled = []

    def cancel_turn(self, turn_id):
        self.cancelled.append(turn_id)
        return 0

    def clear_turn(self, turn_id):
        pass

    def depth(self):
        return 0


class _NaiveTrigger:
    """What a raw level trigger would do: fire on anything above the floor
    held for 200 ms. The control that proves the echo fixtures are loud
    enough to fool a detector that is not relative to the bleed."""

    def __init__(self, floor=550, sustain_ms=200):
        self.floor, self.sustain_ms, self.sustained = floor, sustain_ms, 0.0

    def reset_turn(self, now=None):
        self.sustained = 0.0

    def feed(self, rms, chunk_ms, now=None):
        self.sustained = self.sustained + chunk_ms if rms >= self.floor else 0.0
        return self.sustained >= self.sustain_ms


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


_OPEN: list = []
_RELEASE: list = []
_MINDS: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for ev in _RELEASE:
        ev.set()
    for s in _OPEN:
        try:
            s.close()
            s._speaker.join(1.5)
        except Exception:
            pass
    for th in _MINDS:
        th.join(2.0)
    del _OPEN[:], _RELEASE[:], _MINDS[:]


def _detector(**kw):
    from agent_friday.routes.voice import LiveBargeDetector
    return LiveBargeDetector(**{"grace_ms": 800, "sustain_ms": 200, **kw})


def _held_mind(first="Here is the first part of a long answer. ", cancel_aware=False):
    """A mind that speaks one clause, then keeps generating until released.
    `cancel_aware=False` is a brain that cannot be stopped at all."""
    release = threading.Event()
    _RELEASE.append(release)
    seen = {"cancel": None, "calls": []}

    def gen(user_text, on_delta, cancel):
        _MINDS.append(threading.current_thread())
        seen["cancel"] = cancel
        seen["calls"].append(user_text)
        if user_text != "first":
            on_delta("Second answer. ")
            return "Second answer."
        on_delta(first)
        on_delta("Then the second clause arrives. ")
        while not release.wait(0.01):
            if cancel_aware and cancel.is_set():
                break
        return first + "Then the second clause arrives. And a third, unheard."
    return gen, release, seen


def _session(generate, *, detector=None, mouth=None, clock=None, gpu=None,
             send=None):
    frames = []
    receipts = []

    def _rec(o):
        o = dict(o, _t=time.perf_counter())
        frames.append(o)
        return True
    s = vs.VoiceSession(send or _rec, ear=_Ear(), mouth=mouth or _Mouth(),
                        vad=_VAD(), generate=generate,
                        hooks={"receipt": lambda: receipts.append(_Receipt())
                               or receipts[-1]},
                        gpu_queue=gpu, barge_detector=detector,
                        clock=clock or time.monotonic)
    _OPEN.append(s)
    return s, frames, receipts


def _types(frames):
    return [f["type"] for f in frames]


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _start_turn(s, frames, text="first"):
    th = threading.Thread(target=s.run_turn, args=(text,), daemon=True)
    th.start()
    assert _wait(lambda: "audio" in _types(frames)), "Friday never started speaking"
    return th


def _feed(s, clock, rms, n):
    """`n` mic chunks at `rms`, the clock moving one chunk per chunk. Returns
    the wall time at which the last chunk was handed in."""
    t = None
    for _ in range(n):
        clock.t += MS_PER_CHUNK / 1000.0
        t = time.perf_counter()
        s.handle(_audio_frame(_tone(rms)))
    return t


# ── 1. sustained user speech during playback barges in ──────────────────────

def test_talking_over_friday_stops_her_and_ends_the_turn_at_once():
    clock = _Clock()
    gpu = _GpuQueue()
    gen, release, seen = _held_mind()
    s, frames, receipts = _session(gen, detector=_detector(), clock=clock,
                                   mouth=_Mouth(delay=0.03), gpu=gpu)
    s.start()
    th = _start_turn(s, frames)
    s.handle({"type": "speaking", "on": True})        # her voice is playing
    _feed(s, clock, 300, 5)                           # 850 ms of bleed: the grace window
    _feed(s, clock, 4000, 1)                          # 170 ms of the user: not yet
    assert "interrupted" not in _types(frames)
    t_confirm = _feed(s, clock, 4000, 1)              # 340 ms sustained: barge
    t = _types(frames)
    assert "interrupted" in t, "talking over Friday did not stop her"
    stop_ms = (frames[t.index("interrupted")]["_t"] - t_confirm) * 1000.0
    assert stop_ms < 50, "silence took %.1f ms after the barge was confirmed" % stop_ms
    # The mind was told to stop, its GPU jobs dropped, and the turn ended
    # WITHOUT waiting for a brain that has not returned.
    assert seen["cancel"].is_set()
    assert gpu.cancelled, "the turn's queued GPU jobs were not cancelled"
    th.join(1.0)
    assert not th.is_alive(), "the barged turn is still waiting on its mind"
    assert not release.is_set()
    rec = [f for f in frames if f["type"] == "turn_receipt"][-1]
    assert rec["outcome"] == "aborted"
    assert receipts[-1].outcome == "aborted"
    # No audio of the barged turn after `interrupted`.
    t = _types(frames)
    assert "audio" not in t[t.index("interrupted") + 1:]


def test_talk_over_works_for_a_client_that_never_reports_playback():
    """Older clients (the PWA) send no `speaking` frames; the window is then
    estimated from the audio already sent, exactly as on the Live path."""
    clock = _Clock()
    gen, release, seen = _held_mind()
    s, frames, _ = _session(gen, detector=_detector(), clock=clock,
                            mouth=_Mouth(seconds=1.5))
    s.start()
    th = _start_turn(s, frames)
    assert _wait(lambda: _types(frames).count("audio") >= 3)
    _feed(s, clock, 300, 5)
    _feed(s, clock, 4000, 2)
    assert "interrupted" in _types(frames)
    assert seen["cancel"].is_set()
    th.join(1.0)
    assert not th.is_alive()


# ── 2. Friday's own echo never barges ───────────────────────────────────────

_BLEED = [600, 900, 1200, 700, 1100, 650, 1000, 1200, 800, 1150, 700, 1200,
          900, 1000, 1100, 1200, 750, 1050]           # her voice through the mic


def _echo_run(detector):
    clock = _Clock()
    gen, release, seen = _held_mind()
    s, frames, _ = _session(gen, detector=detector, clock=clock)
    s.start()
    _start_turn(s, frames)
    s.handle({"type": "speaking", "on": True})
    for rms in _BLEED:                                # ~3 s of syllable-shaped bleed
        clock.t += MS_PER_CHUNK / 1000.0
        s.handle(_audio_frame(_tone(rms)))
    return frames, seen


def test_her_own_echo_never_stops_her():
    frames, seen = _echo_run(_detector())
    assert "interrupted" not in _types(frames)
    assert not seen["cancel"].is_set()


def test_the_echo_fixture_would_fool_a_raw_level_trigger():
    """Teeth for the test above: the same bleed DOES trip a trigger that is
    not relative to Friday's own level."""
    frames, seen = _echo_run(_NaiveTrigger())
    assert "interrupted" in _types(frames)


def test_speech_when_she_is_not_playing_is_not_a_barge():
    clock = _Clock()
    gen, release, seen = _held_mind()
    s, frames, _ = _session(gen, detector=_detector(), clock=clock)
    s.start()
    _start_turn(s, frames)
    s.handle({"type": "speaking", "on": True})
    _feed(s, clock, 300, 5)                           # the bleed is learned
    s.handle({"type": "speaking", "on": False})       # playback drained
    _feed(s, clock, 4000, 12)                         # the user simply talks
    assert "interrupted" not in _types(frames)
    assert not seen["cancel"].is_set()


# ── 3. the clause queue and the generation are cancelled ────────────────────

def test_a_barge_drops_queued_clauses_and_cancels_the_mind():
    clock = _Clock()
    all_queued = threading.Event()
    seen = {}

    class HeldMouth(_Mouth):
        def __init__(self):
            super().__init__()
            self.held = threading.Event()
            self.release = threading.Event()

        def synthesize_stream(self, text, cancel=None):
            # The first clause reaches playback; the second stays in flight
            # while the rest remain queued, regardless of caller scheduling.
            if len(self.spoken) == 1:
                self.held.set()
                self.release.wait()
            yield from super().synthesize_stream(text, cancel)

    mouth = HeldMouth()
    _RELEASE.append(mouth.release)

    def gen(user_text, on_delta, cancel):
        _MINDS.append(threading.current_thread())
        seen["cancel"] = cancel
        for i in range(6):
            on_delta("Clause number %d is here. " % i)
        all_queued.set()
        cancel.wait()                                # a mind that honours cancel
        return "done"
    s, frames, _ = _session(gen, detector=_detector(), clock=clock, mouth=mouth)
    s.start()
    th = None
    try:
        th = _start_turn(s, frames)
        assert all_queued.wait(5.0), "the mind did not queue its clauses"
        assert mouth.held.wait(5.0), "the mouth did not reach the held clause"
        assert not s._speak_q.empty(), "the barge needs queued clauses to cancel"
        assert not seen["cancel"].is_set()
        turn = s._current_turn
        s.handle({"type": "speaking", "on": True})
        _feed(s, clock, 300, 5)
        _feed(s, clock, 4000, 2)
        assert "interrupted" in _types(frames), "talking over Friday did not stop her"
        assert seen["cancel"].is_set(), "the barge did not cancel the mind"
        th.join(2.0)
        assert not th.is_alive()
        assert s._speak_q.empty(), "queued clauses survived the barge"
        mouth.release.set()
        with turn["cv"]:
            assert turn["cv"].wait_for(lambda: turn["pending"] == 0, timeout=5.0), (
                "the interrupted clause did not finish")
        assert len(mouth.spoken) < 6, "every clause was synthesized anyway"
        t = _types(frames)
        assert "audio" not in t[t.index("interrupted") + 1:]
    finally:
        if "cancel" in seen:
            seen["cancel"].set()
        mouth.release.set()
        s.close()
        if th is not None:
            th.join(2.0)


def test_no_audio_frame_ever_follows_interrupted():
    """The client reopens its playback gate on `interrupted`, so an audio
    frame of the barged turn sent after it would play. The speaker is held
    mid-send while the barge arrives."""
    in_send, gate = threading.Event(), threading.Event()
    _RELEASE.append(gate)
    frames = []

    def send(o):
        if o["type"] == "audio" and not in_send.is_set():
            in_send.set()
            gate.wait(2.0)
        frames.append(o)
        return True
    gen, release, seen = _held_mind()
    s, _, _ = _session(gen, send=send)
    s.start()
    th = threading.Thread(target=s.run_turn, args=("first",), daemon=True)
    th.start()
    assert in_send.wait(5.0)
    b = threading.Thread(target=s.handle, args=({"type": "barge"},), daemon=True)
    b.start()
    b.join(0.3)
    gate.set()
    b.join(2.0)
    th.join(2.0)
    t = _types(frames)
    assert "interrupted" in t
    assert "audio" not in t[t.index("interrupted") + 1:], t


# ── 4. the next turn starts without waiting ─────────────────────────────────

def test_the_next_turn_starts_while_the_barged_mind_is_still_running():
    gen, release, seen = _held_mind()
    s, frames, _ = _session(gen)
    s.start()
    th = _start_turn(s, frames)
    s.handle({"type": "barge"})                       # Escape
    th.join(1.0)
    assert not th.is_alive(), "Escape left the turn waiting on its mind"
    t0 = time.monotonic()
    nxt = threading.Thread(target=s.run_turn, args=("second",), daemon=True)
    nxt.start()
    assert _wait(lambda: "second" in seen["calls"], 1.0), (
        "the next turn waited on the barged one")
    assert time.monotonic() - t0 < 1.0
    assert not release.is_set()                       # the first mind still runs
    nxt.join(5.0)
    assert not nxt.is_alive()
    done = [f for f in frames if f["type"] == "voice_turn_done"]
    assert [d["user_text"] for d in done] == ["first", "second"]
    # The barged turn records what she actually said, not the unheard tail
    # the mind would have returned.
    said = done[0]["agent_text"]
    assert said.startswith("Here is the first part of a long answer.")
    assert "unheard" not in said
    t = _types(frames)
    assert t.index("turn_end") < t.index("input_transcript", t.index("interrupted"))


def test_escape_still_barges_when_talk_over_is_off():
    """voice_interruption_mode=no-barge: talking over her does nothing, Escape
    stops her as it always has."""
    clock = _Clock()
    gen, release, seen = _held_mind()
    s, frames, _ = _session(gen, detector=None, clock=clock)
    s.start()
    th = _start_turn(s, frames)
    s.handle({"type": "speaking", "on": True})
    _feed(s, clock, 300, 5)
    _feed(s, clock, 4000, 6)
    assert "interrupted" not in _types(frames)
    s.handle({"type": "barge"})
    assert "interrupted" in _types(frames)
    assert seen["cancel"].is_set()
    th.join(1.0)
    assert not th.is_alive()


# ── the route reads the setting and wires the detector ──────────────────────

def test_the_local_route_reads_the_interruption_mode_and_barge_tuning():
    from agent_friday.routes import voice as rv
    import agent_friday.core as core
    d = rv._local_talk_over_detector({})
    assert isinstance(d, rv.LiveBargeDetector)
    assert (d.grace_ms, d.sustain_ms) == (800, 170)
    d = rv._local_talk_over_detector(dict(core.DEFAULT_SETTINGS))
    assert (d.grace_ms, d.sustain_ms) == (800, 170)
    d = rv._local_talk_over_detector({"voice_barge_grace_ms": 500,
                                      "voice_local_barge_sustain_ms": 120})
    assert (d.grace_ms, d.sustain_ms) == (500, 120)
    # Live's speaker-safe tuning is its own: it never moves the local path.
    d = rv._local_talk_over_detector({"voice_barge_sustain_ms": 400})
    assert d.sustain_ms == 170
    for off in ("no-barge", "speaker-safe", "none", "off", "No-Barge"):
        assert rv._local_talk_over_detector({"voice_interruption_mode": off}) is None
    for on in ("auto", "headphones", ""):
        assert rv._local_talk_over_detector({"voice_interruption_mode": on}) is not None


def test_the_local_handler_builds_its_session_with_the_detector():
    from agent_friday.routes import voice as rv
    src = inspect.getsource(rv)
    assert "barge_detector=_local_talk_over_detector(settings)" in src
    assert "rms=_quick_rms" in src


def test_the_local_mind_passes_the_turns_cancel_to_the_transport():
    from agent_friday.routes import voice as rv
    src = inspect.getsource(rv)
    assert "TURN_CANCEL.set(cancel)" in src


# ── the transport and the agent loop honour the cancel ──────────────────────

def _sse(content=None, tool=None, finish=None):
    d = {"choices": [{"delta": {}, "finish_reason": finish}], "model": "seat"}
    if content is not None:
        d["choices"][0]["delta"]["content"] = content
    if tool is not None:
        d["choices"][0]["delta"]["tool_calls"] = [tool]
    return "data: " + json.dumps(d)


class _Stream:
    """An SSE response whose third line arrives after the barge."""

    def __init__(self, cancel):
        self.cancel = cancel
        self.closed = False
        self.read = 0

    def iter_lines(self, decode_unicode=True):
        for i, line in enumerate([
                _sse("Here is "), _sse("the answer. "),
                _sse(" More after the barge."),
                _sse(tool={"index": 0, "id": "c1", "function": {
                    "name": "write_file", "arguments": "{\"path\": \"x\"}"}}),
                _sse(finish="tool_calls"), "data: [DONE]"]):
            if i == 2:
                self.cancel.set()
            self.read += 1
            yield line

    def close(self):
        self.closed = True


def test_the_seat_stream_stops_and_closes_when_the_turn_is_cancelled():
    from agent_friday.services import model_router as mr
    cancel = threading.Event()
    resp = _Stream(cancel)
    got = []
    tok = mr.TURN_CANCEL.set(cancel)
    try:
        out = mr._consume_sse_completion(resp, on_delta=got.append)
    finally:
        mr.TURN_CANCEL.reset(tok)
    assert got == ["Here is ", "the answer. "]
    msg = out["choices"][0]["message"]
    assert msg["content"] == "Here is the answer. "
    assert not msg.get("tool_calls"), "a cut-off round must not carry a tool call"
    assert out["choices"][0]["finish_reason"] == "cancelled"
    assert resp.closed, "the seat keeps generating unless the stream is closed"
    assert resp.read == 3


def test_an_uncancelled_stream_is_unchanged():
    from agent_friday.services import model_router as mr
    resp = _Stream(threading.Event())
    resp.cancel = threading.Event()                   # never consulted
    out = mr._consume_sse_completion(resp)
    assert out["choices"][0]["message"]["tool_calls"]
    assert not resp.closed


def test_a_cancelled_turn_runs_no_tool_and_no_further_round(monkeypatch):
    from agent_friday.services import agent as ag
    from agent_friday.services import model_router as mr
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    ran = []
    monkeypatch.setattr(ag, "_execute_tool",
                        lambda name, args, **kw: ran.append(name) or "ok")
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None,
                        raising=False)
    cancel = threading.Event()
    rounds = []

    def send_fn(convo, tools):
        rounds.append(1)
        cancel.set()                                  # the barge lands mid-round
        return {"choices": [{"message": {
            "content": "On it. <|tool_call>call:write_file{path:x}<tool_call|>",
            "tool_calls": [{"id": "c1", "function": {
                "name": "write_file", "arguments": {"path": "x"}}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    tools = [{"type": "function", "function": {"name": "write_file",
              "parameters": {"type": "object", "properties": {}}}}]
    tok = mr.TURN_CANCEL.set(cancel)
    try:
        text, _ = ag._oai_agentic_loop(
            [{"role": "user", "content": "write it"}], tools, send_fn,
            provider="local", model="seat-x")
    finally:
        mr.TURN_CANCEL.reset(tok)
    assert ran == [], "a tool ran after the user interrupted"
    assert len(rounds) == 1, "another round was sent to the seat after the barge"
    assert "stopped" in text.lower()


def test_a_cancelled_turn_never_falls_back_to_another_provider(monkeypatch):
    import agent_friday.services.agent as ag
    from agent_friday.services import model_router as mr
    cancel = threading.Event()
    legs = []

    def local_leg(messages, **kw):
        legs.append("local")
        cancel.set()
        raise ConnectionError("stream closed by the barge")
    monkeypatch.setattr(ag, "_call_ollama", local_leg)
    monkeypatch.setattr(ag, "_call_claude_agent",
                        lambda *a, **k: legs.append("cloud") or ("cloud reply", []))
    monkeypatch.setattr(ag, "_call_openai",
                        lambda *a, **k: legs.append("openai") or ("openai reply", []))
    monkeypatch.setattr(ag, "get_anthropic_client", lambda: object())
    monkeypatch.setattr(ag, "_load_settings", lambda: {"model_routing": {}})

    class _R:
        def route(self, messages, task_context=None):
            return {"provider": "local", "model": "seat-x"}
    monkeypatch.setattr("agent_friday.routing.model_router.get_router",
                        lambda cfg=None: _R())
    monkeypatch.setattr("agent_friday.services.demo_mode.is_demo", lambda: False)
    tok = mr.TURN_CANCEL.set(cancel)
    try:
        text, _ = ag._generate_agent([{"role": "user", "content": "hi"}], system="s")
    finally:
        mr.TURN_CANCEL.reset(tok)
    assert legs == ["local"], "a cancelled voice turn was re-run on %s" % legs[1:]
    assert text == ""


# ── under 300 ms from the first word, and a setting that says what it does ──

REPO = pathlib.Path(__file__).resolve().parents[2]
_UI = {"index.html": REPO / "index.html", "app.html": REPO / "ui_parts" / "app.html"}
# One local mic frame: 2048 browser samples at 24 kHz, resampled to 16 kHz
# PCM16 by the client (`f2pcm`): 1365 samples, 2730 bytes, 85.3 ms.
LOCAL_FRAME_BYTES = 2 * (2048 * 16000 // 24000)
LOCAL_FRAME_MS = LOCAL_FRAME_BYTES / 32.0


def test_two_local_mic_frames_confirm_a_barge_and_one_does_not():
    """Onset to silence: the barge confirms on the second ~85 ms local frame
    (about 171 ms, 256 ms when the first word lands late in a frame), never
    on one frame alone, which is the echo margin."""
    import agent_friday.core as core
    from agent_friday.routes import voice as rv
    d = rv._local_talk_over_detector(dict(core.DEFAULT_SETTINGS))
    assert LOCAL_FRAME_MS < d.sustain_ms <= 2 * LOCAL_FRAME_MS
    assert 3 * LOCAL_FRAME_MS < 300
    d.reset_turn(now=0.0)
    t = 0.0
    for _ in range(10):                               # 853 ms of bleed: the grace window
        t += LOCAL_FRAME_MS / 1000.0
        assert not d.feed(300, LOCAL_FRAME_MS, now=t)
    t += LOCAL_FRAME_MS / 1000.0
    assert not d.feed(4000, LOCAL_FRAME_MS, now=t), "one frame is not enough"
    t += LOCAL_FRAME_MS / 1000.0
    assert d.feed(4000, LOCAL_FRAME_MS, now=t), "two frames must confirm"


def test_the_local_voice_default_sustain_is_declared():
    import agent_friday.core as core
    assert core.DEFAULT_SETTINGS["voice_local_barge_sustain_ms"] == 170
    assert core.DEFAULT_SETTINGS["voice_barge_sustain_ms"] == 200     # Live, unchanged


@pytest.mark.parametrize("name", sorted(_UI))
def test_the_local_engine_sends_mic_frames_of_about_85_ms(name):
    src = _UI[name].read_text(encoding="utf-8")
    assert re.search(r"createScriptProcessor\(\s*4096", src) is None, (
        "%s: a fixed 4096-frame mic buffer is 171 ms per frame on the local "
        "engine, which puts a talk-over barge past 300 ms" % name)
    assert re.search(r"\(agentSettings\.voice_engine\s*\|\|\s*'local'\)\s*===\s*"
                     r"'gemini'\s*\?\s*4096\s*:\s*2048", src), (
        "%s: the local engine must use 2048-frame mic buffers; Gemini Live "
        "keeps 4096" % name)
    assert re.search(r"createScriptProcessor\(\s*MIC_FRAMES\s*,", src), name


@pytest.mark.parametrize("name", sorted(_UI))
def test_the_interruption_setting_shows_for_every_engine_and_says_what_it_does(name):
    src = _UI[name].read_text(encoding="utf-8")
    flat = re.sub(r"\s+", "", src)
    # The label names her by her chosen name (fridayName()).
    i = flat.index('"Interrupting"+fridayName()')
    assert "gem&&" not in flat[max(0, i - 90):i], (
        "%s: the row is still Gemini-only; the local voice reads this setting too" % name)
    # What each option does, per engine. Gemini Live's no-barge mode keeps an
    # echo-aware talk-over; the local voice's means Esc only.
    for words in ("Speaker-safe (open speakers)", "Esc only",
                  "clearly louder than her own voice coming back through the mic",
                  "Esc always stops her."):
        assert words in src, "%s: missing %r" % (name, words)
    for wrong in ("No interruption (open speakers)", "choose no interruption there"):
        assert wrong not in src, "%s: still promises %r" % (name, wrong)

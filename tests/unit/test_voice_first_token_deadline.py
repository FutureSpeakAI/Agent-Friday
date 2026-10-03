"""The local mind has a first-token deadline (local voice spec P0, defect 6a).

Before: only 180-600 s HTTP timeouts, so a slow or stuck seat left the owner
in silence for minutes. Now, with no first word by ``first_token_filler_s``
Friday says one filler line; with none by ``first_token_abort_s`` the
generation is cancelled (the mind's cancel is set, which closes the seat's
stream), she says so plainly, and the receipt records ``deadline_hit``. A mind
that has started speaking is never cut by the deadline.
"""
import inspect
import threading
import time

from agent_friday.services import voice_session as vs


class _Mouth:
    name, device = "kokoro", "cpu"

    def __init__(self):
        self.spoken = []

    def synthesize_stream(self, text, cancel=None):
        self.spoken.append(text)
        yield b"\x00\x01" * 240


class _VAD:
    _buf = bytearray()
    _in_speech = False

    def feed(self, pcm):
        return None

    def flush(self):
        return None


class _Receipt:
    def __init__(self):
        self.fields = {}

    def mark(self, *a, **k):
        pass

    def set(self, **kw):
        self.fields.update(kw)

    def count_audio_out(self, n):
        pass

    def count_text_out(self, n):
        pass

    def done(self, outcome=None, code="", detail=""):
        pass


def _run(gen, filler=0.15, abort=0.45):
    mouth = _Mouth()
    frames = []
    receipts = []
    s = vs.VoiceSession(lambda o: frames.append(o) or True, ear=object(), mouth=mouth,
                        vad=_VAD(), generate=gen,
                        hooks={"receipt": lambda: receipts.append(_Receipt()) or receipts[-1]},
                        first_token_filler_s=filler, first_token_abort_s=abort)
    try:
        t0 = time.perf_counter()
        s.run_turn("hello")
        took = time.perf_counter() - t0
    finally:
        s.close()
    rec = [f for f in frames if f.get("type") == "turn_receipt"][-1]
    return mouth, rec, receipts[-1], took


def test_a_silent_mind_gets_a_filler_then_an_honest_abort():
    seen = {}
    released = threading.Event()

    def gen(text, on_delta, cancel):
        seen["cancel"] = cancel
        cancel.wait(10)                 # a seat that never answers
        released.set()
        return ""
    mouth, rec, receipt, took = _run(gen)
    assert mouth.spoken[:1] == [vs.FILLER_LINE], mouth.spoken
    assert vs.DEADLINE_LINE in mouth.spoken
    assert seen["cancel"].is_set(), "the stuck generation was never cancelled"
    assert released.wait(2)
    assert rec["deadline_hit"] == "abort" and receipt.fields.get("deadline_hit") == "abort"
    assert took < 3.0, "the turn waited for the stuck mind instead of the deadline"


def test_a_mind_that_starts_speaking_is_never_cut_by_the_deadline():
    def gen(text, on_delta, cancel):
        on_delta("Sure. ")
        time.sleep(0.8)                 # longer than the abort deadline
        on_delta("Here it is.")
        return "Sure. Here it is."
    mouth, rec, _r, _t = _run(gen)
    assert vs.FILLER_LINE not in mouth.spoken
    assert vs.DEADLINE_LINE not in mouth.spoken
    assert rec["deadline_hit"] is None


def test_the_route_wires_both_deadlines_from_settings():
    from agent_friday import core
    import agent_friday.routes.voice as rv
    assert core.DEFAULT_SETTINGS["voice_first_token_filler_s"] == 6
    assert core.DEFAULT_SETTINGS["voice_tool_hard_limit_s"] == 20
    src = inspect.getsource(rv)
    assert '"voice_first_token_filler_s", 6' in src
    assert '"voice_tool_hard_limit_s", 20' in src

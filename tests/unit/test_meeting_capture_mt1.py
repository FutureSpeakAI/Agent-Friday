"""Meeting capture, MT-1: two channels, truthful system audio, VAD, live
partials, the ear's lease and the local scheduler.

Every audio device is a fake. No microphone, speaker or loopback is opened, no
model is loaded and no real scheduler seat is touched.
"""
from __future__ import annotations

import array
import math
import threading
import time

import pytest

from agent_friday.services import local_scheduler as ls
from agent_friday.services import meeting_capture as mc
from agent_friday.services import turn_queue as tq


def tone(seconds, amp=8000, hz=440.0):
    n = int(mc.RATE * seconds)
    return array.array("h", (int(amp * math.sin(2 * math.pi * hz * i / mc.RATE))
                             for i in range(n))).tobytes()


def silence(seconds):
    return b"\x00\x00" * int(mc.RATE * seconds)


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class MicSource:
    kind = "microphone"
    error = ""

    def __init__(self, max_s):
        self.on = None

    def start(self, on_pcm):
        self.on = on_pcm

    def stop(self):
        pass


class DualFake:
    """What meeting_audio.DualSource looks like to the recorder."""
    kind = "microphone+system_audio"
    channels = ("you", "them")
    error = ""
    system_error = ""

    def __init__(self, max_s, active=True):
        self.on = None
        self.system_audio_active = active

    def start(self, on_pcm):
        self.on = on_pcm

    def stop(self):
        pass


class Rig:
    def __init__(self, tmp_path, monkeypatch, **kw):
        monkeypatch.setattr(mc, "_root", lambda: tmp_path / "meetings")
        self.factory_calls = []
        self.sources = []
        self.heard = []
        self.holds = []
        self.ledger = []
        dual = kw.pop("dual", {})

        def factory(max_s, system_audio=False):
            self.factory_calls.append({"system_audio": system_audio})
            s = DualFake(max_s, **dual) if system_audio else MicSource(max_s)
            self.sources.append(s)
            return s

        def transcriber(pcm):
            self.heard.append(len(pcm))
            return "mine" if max(array.array("h", pcm)) > 6000 else "theirs"

        class Hold:
            def __init__(h, mid):
                h.id, h.released = mid, False
                self.holds.append(h)

            def release(h):
                h.released = True

        opts = dict(source_factory=factory, transcriber=transcriber,
                    seat_resolver=lambda: None, asr_check=lambda: "",
                    lock_detector=lambda: None, event_lookup=lambda: None,
                    notifier=lambda *a: None, scheduler=Hold, partials_factory=lambda: None,
                    speech_gate=lambda pcm: max(array.array("h", pcm)) > 100,
                    ledger=lambda kind, **f: self.ledger.append((kind, f)),
                    retention=lambda: "", clock=time.monotonic, tick_s=0.01, chunk_s=1.0)
        opts.update(kw)
        self.m = mc.MeetingManager(**opts)

    @property
    def src(self):
        return self.sources[-1]

    def start(self, **kw):
        kw.setdefault("consent_ack", True)
        kw.setdefault("confirm", True)
        return self.m.start(**kw)

    def finish(self):
        self.m.stop()
        assert self.m.wait_idle(5)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    yield r
    try:
        r.m.stop()
    except mc.MeetingError:
        pass
    r.m.wait_idle(5)


def segments(rig, mid):
    return [g for g in rig.m.get(mid)["segments"] if g["kind"] == "speech"]


# ── two channels, kept apart ────────────────────────────────────────────────

def test_the_microphone_is_you_and_the_loopback_is_them(rig):
    mid = rig.start(system_audio=True)["id"]
    assert rig.factory_calls == [{"system_audio": True}]
    rig.src.on(tone(1.1, amp=9000), "you")
    rig.src.on(tone(1.1, amp=3000), "them")
    rig.finish()
    got = {(g["speaker"], g["text"]) for g in segments(rig, mid)}
    assert got == {("You", "mine"), ("Them", "theirs")}


def test_each_channel_keeps_its_own_clock(rig):
    mid = rig.start(system_audio=True)["id"]
    rig.src.on(tone(2.2, amp=9000), "you")
    rig.src.on(tone(1.2, amp=3000), "them")
    rig.finish()
    by = {}
    for g in segments(rig, mid):
        by.setdefault(g["speaker"], []).append(g["t"])
    assert by["You"][0] == 0 and by["Them"][0] == 0
    assert max(by["You"]) > max(by["Them"])


def test_a_microphone_only_recording_has_no_speaker_labels(rig):
    mid = rig.start()["id"]
    rig.src.on(tone(0.9, amp=9000))
    rig.finish()
    assert rig.factory_calls == [{"system_audio": False}]
    segs = segments(rig, mid)
    assert segs and {g["speaker"] for g in segs} == {None}


def test_the_notes_see_who_said_what(rig):
    mid = rig.start(system_audio=True)["id"]
    rig.src.on(tone(1.1, amp=9000), "you")
    rig.src.on(tone(1.1, amp=3000), "them")
    rig.finish()
    text = mc.transcript_text(rig.m.get(mid))
    assert "You: mine" in text and "Them: theirs" in text


def test_kept_audio_stays_two_channels_and_downloads_as_stereo(tmp_path, monkeypatch):
    import io
    import wave
    r = Rig(tmp_path, monkeypatch)
    mid = r.start(system_audio=True, keep_audio=True)["id"]
    r.src.on(tone(1.1, amp=9000), "you")
    r.src.on(tone(1.1, amp=3000), "them")
    r.finish()
    with wave.open(io.BytesIO(r.m.audio_wav(mid))) as w:
        assert w.getnchannels() == 2 and w.getframerate() == mc.RATE


# ── system audio reports the truth ──────────────────────────────────────────

def test_system_audio_is_not_captured_unless_the_owner_asked_and_it_started(rig):
    assert rig.m.status()["system_audio"]["captured"] is False
    rig.start()
    st = rig.m.status()
    assert st["system_audio"]["captured"] is False and st["speakers"] is False


def test_system_audio_is_captured_once_the_loopback_really_runs(rig):
    rig.start(system_audio=True)
    st = rig.m.status()
    assert st["system_audio"]["captured"] is True and st["speakers"] is True
    assert set(st["channels"]) <= {"you", "them"}


def test_asking_for_system_audio_that_did_not_start_is_not_reported_as_captured(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, dual={"active": False})
    r.start(system_audio=True)
    r.src.system_error = "no render device"
    st = r.m.status()
    assert st["system_audio"]["captured"] is False
    assert st["system_audio"]["requested"] is True
    assert "microphone is still" in st["system_audio"]["reason"]
    assert st["recording"] is True                     # the microphone carries on


def test_the_loopback_dying_mid_meeting_keeps_the_microphone_recording(rig):
    mid = rig.start(system_audio=True)["id"]
    rig.src.on(tone(1.1, amp=9000), "you")
    rig.src.system_error = "device removed"
    assert wait_for(lambda: rig.m.status()["system_audio"]["captured"] is False)
    assert rig.m.status()["recording"] is True
    rig.src.on(tone(1.1, amp=9000), "you")
    rig.finish()
    gaps = [g["text"] for g in rig.m.get(mid)["segments"] if g["kind"] == "gap"]
    assert any("System audio stopped" in t for t in gaps)
    assert not rig.m.get(mid)["system_audio"]


def test_the_status_never_claims_system_audio_for_a_mic_only_default_factory(monkeypatch):
    st = mc.system_audio_status()
    assert st["captured"] is False


# ── VAD ─────────────────────────────────────────────────────────────────────

def test_a_chunk_without_speech_is_not_transcribed(rig):
    rig.start()
    rig.src.on(silence(1.1))
    rig.finish()
    assert rig.heard == []


def test_the_speech_gate_decides_not_the_loudness(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, speech_gate=lambda pcm: True)
    r.start()
    r.src.on(silence(1.1))
    r.finish()
    assert r.heard, "a detector that heard speech must be believed over the RMS"


def test_silero_is_used_when_the_vad_endpointer_can_supply_it(monkeypatch):
    from agent_friday.services import local_voice

    class V:
        def silero_decider(self):
            return lambda pcm: True
    monkeypatch.setattr(local_voice, "VADEndpointer", V)
    gate, name = mc._default_speech_gate()
    assert name == "silero" and gate(silence(0.1)) is True


def test_the_rms_gate_is_the_fallback(monkeypatch):
    from agent_friday.services import local_voice

    class V:
        def silero_decider(self):
            return None
    monkeypatch.setattr(local_voice, "VADEndpointer", V)
    gate, name = mc._default_speech_gate()
    assert name == "energy"
    assert gate(tone(0.1, amp=8000)) is True and gate(silence(0.1)) is False


def test_a_silero_error_falls_back_to_the_rms_gate_for_that_chunk(monkeypatch):
    from agent_friday.services import local_voice

    def boom(pcm):
        raise RuntimeError("model unavailable")

    class V:
        def silero_decider(self):
            return boom
    monkeypatch.setattr(local_voice, "VADEndpointer", V)
    gate, _ = mc._default_speech_gate()
    assert gate(tone(0.1, amp=8000)) is True and gate(silence(0.1)) is False


# ── live partials ───────────────────────────────────────────────────────────

class FakeEar:
    def __init__(self, label, fail=False):
        self.label, self.fail, self.fed, self.resets = label, fail, 0, 0

    def feed(self, pcm):
        if self.fail:
            raise RuntimeError("recognizer died")
        self.fed += 1
        return ("%s words %d" % (self.label, self.fed), False)

    def reset(self):
        self.resets += 1


def test_live_partials_come_from_the_streaming_ear_and_are_not_stored(tmp_path, monkeypatch):
    ears = {"you": FakeEar("you"), "them": FakeEar("them")}
    r = Rig(tmp_path, monkeypatch, partials_factory=lambda: ears)
    mid = r.start(system_audio=True)["id"]
    r.src.on(tone(0.2, amp=9000), "you")
    r.src.on(tone(0.2, amp=3000), "them")
    assert wait_for(lambda: len(r.m.live()["partials"]) == 2)
    assert r.m.live()["partials"]["You"].startswith("you words")
    assert r.m.live()["partials"]["Them"].startswith("them words")
    assert r.m.status()["live_partials"] is True
    r.finish()
    assert "words" not in str(r.m.get(mid)), "a partial is shown, never stored"


def test_a_chunk_boundary_restarts_the_stream(tmp_path, monkeypatch):
    ears = {"you": FakeEar("you"), "them": FakeEar("them")}
    r = Rig(tmp_path, monkeypatch, partials_factory=lambda: ears)
    r.start()
    r.src.on(tone(1.2, amp=9000))
    assert wait_for(lambda: ears["you"].resets >= 1)
    r.finish()


def test_without_the_streaming_model_there_are_no_partials_and_nothing_breaks(rig):
    rig.start()
    rig.src.on(tone(0.2, amp=9000))
    assert rig.m.live() == {"recording": True, "available": False, "partials": {}}
    assert rig.m.status()["live_partials"] is False


def test_a_failing_streaming_ear_never_stops_the_recording(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, partials_factory=lambda: {"you": FakeEar("you", fail=True)})
    mid = r.start()["id"]
    r.src.on(tone(0.9, amp=9000))
    assert wait_for(lambda: r.m.live()["available"] is False)
    assert r.m.status()["recording"] is True
    r.finish()
    assert [g["text"] for g in segments(r, mid)] == ["mine"]


# ── the ear: the voice stack's lease and the CPU fallback ───────────────────

class FakeEngine:
    def __init__(self, device="cuda", alive=True, fail=False):
        self.device, self._alive, self.fail, self.calls = device, alive, fail, 0

    def alive(self):
        return self._alive

    def transcribe(self, pcm):
        self.calls += 1
        if self.fail:
            raise RuntimeError("worker died")
        return "text from " + self.device


def test_the_ear_is_built_the_way_a_voice_session_builds_it():
    seen = []

    def build(selection):
        seen.append(selection)
        return FakeEngine()
    ear = mc._EarSession(build)
    assert ear.transcribe(b"\x00\x00") == "text from cuda"
    assert seen == [{"device_policy": "if_free", "model": "auto"}]


def test_an_evicted_gpu_ear_is_replaced_by_a_cpu_ear_for_the_next_chunk():
    engines = [FakeEngine("cuda"), FakeEngine("cpu")]
    ear = mc._EarSession(lambda sel: engines.pop(0))
    assert ear.transcribe(b"\x00\x00") == "text from cuda"
    ear._engine._alive = False                       # the lease was taken back
    assert ear.transcribe(b"\x00\x00") == "text from cpu"
    assert ear.rebuilds == 2


def test_a_worker_that_dies_mid_chunk_is_retried_once_on_a_new_ear():
    engines = [FakeEngine("cuda", fail=True), FakeEngine("cpu")]
    ear = mc._EarSession(lambda sel: engines.pop(0))
    assert ear.transcribe(b"\x00\x00") == "text from cpu"


def test_the_default_transcriber_uses_the_session_ear(monkeypatch):
    monkeypatch.setattr(mc, "_EAR", mc._EarSession(lambda sel: FakeEngine("cpu")))
    assert mc._default_transcriber(b"\x00\x00") == "text from cpu"


# ── the scheduler: voice class for the whole meeting ────────────────────────

def test_the_meeting_holds_its_place_for_its_whole_length_and_lets_go_at_stop(rig):
    mid = rig.start()["id"]
    assert [h.id for h in rig.holds] == [mid] and not rig.holds[0].released
    rig.finish()
    assert rig.holds[0].released


def test_a_source_that_fails_to_start_does_not_leave_the_meeting_holding_its_place(tmp_path, monkeypatch):
    class Dead(MicSource):
        def start(self, on_pcm):
            raise OSError("no microphone")
    r = Rig(tmp_path, monkeypatch, source_factory=lambda max_s, **k: Dead(max_s))
    with pytest.raises(mc.MeetingError):
        r.start()
    assert r.holds and r.holds[0].released


@pytest.fixture
def sched(monkeypatch):
    tq.reset_for_tests()
    ls.reset_for_tests()
    monkeypatch.setattr(ls, "emit", lambda *a, **k: None)
    monkeypatch.setattr(tq, "seat_capacity", lambda seat: 1)
    monkeypatch.setattr(ls, "model_need_gb", lambda model: 0.0)
    ls.set_ram_probe(lambda: 64.0)
    ls.set_residency_probe(lambda model: True)
    yield
    tq.reset_for_tests()
    ls.reset_for_tests()


def _bg(model="some-model"):
    return tq.Ticket("bg1", "background", "default", model=model, kind="task")


def test_a_recording_meeting_is_a_voice_class_ticket_that_blocks_background_admission(sched):
    assert ls.gate(_bg()) == ""                      # a resident model, nobody busy
    hold = ls.hold_for_meeting("abc123def456")
    try:
        running = [t for t in tq.all_running() if t.kind == ls.MEETING_KIND]
        assert len(running) == 1 and running[0].cls == "voice"
        assert tq.people_active_anywhere()
        assert "meeting" in ls.gate(_bg())           # even for a model that is already loaded
        assert "meeting" in ls.interactive_busy_reason().lower()
    finally:
        hold.release()
    assert not tq.people_active_anywhere()
    assert ls.gate(_bg()) == ""


def test_the_meeting_never_takes_the_slot_a_chat_turn_needs(sched):
    hold = ls.hold_for_meeting("abc123def456")
    try:
        adm = tq.acquire("default", "chat", "turn-1", max_wait_s=2)
        assert adm.outcome == "admitted"
        adm.release()
    finally:
        hold.release()


def test_a_job_the_owner_asked_for_is_not_held_back_by_their_own_recording(sched):
    ls.set_residency_probe(lambda model: False)      # a swap: the gate looks at who is busy
    t = tq.Ticket("uj1", "user_job", "default", model="some-model", kind="image_job")
    hold = ls.hold_for_meeting("abc123def456")
    try:
        assert ls.gate(t) == ""
        assert ls.gate(tq.Ticket("bg2", "background", "default", model="some-model")) != ""
    finally:
        hold.release()


def test_releasing_the_hold_is_idempotent_and_clears_the_pause(sched):
    hold = ls.hold_for_meeting("abc123def456")
    assert tq.background_pause_reason()
    hold.release()
    hold.release()
    assert tq.background_pause_reason() == ""


def test_a_running_background_job_is_asked_to_pause_at_its_next_checkpoint(sched):
    naps = []

    def nap(s):
        naps.append(s)
        if len(naps) == 2:
            tq.clear_background_pause("meeting:x")   # the meeting ended
    tq.request_background_pause("meeting:x", "you are in a meeting")
    with tq.holding("default", "background"):
        assert tq.background_checkpoint(max_wait_s=5, sleep=nap) is True
    assert len(naps) == 2


def test_only_background_work_pauses_at_a_checkpoint(sched):
    tq.request_background_pause("meeting:x", "you are in a meeting")
    try:
        assert tq.background_checkpoint(max_wait_s=0.01, sleep=lambda s: None) is False   # no context
        with tq.holding("default", "chat"):
            assert tq.background_checkpoint(max_wait_s=0.01, sleep=lambda s: None) is False
    finally:
        tq.clear_background_pause("meeting:x")


def test_a_pause_that_is_never_cleared_cannot_strand_a_job_for_good(sched):
    tq.request_background_pause("meeting:x", "you are in a meeting")
    try:
        with tq.holding("default", "background"):
            t0 = time.monotonic()
            assert tq.background_checkpoint(max_wait_s=0.3) is True
            assert time.monotonic() - t0 < 2
    finally:
        tq.clear_background_pause("meeting:x")


def test_the_tool_executor_is_a_checkpoint_for_background_jobs(sched):
    done = threading.Timer(0.4, lambda: tq.clear_background_pause("meeting:x"))
    tq.request_background_pause("meeting:x", "you are in a meeting")
    done.start()
    with tq.holding("default", "background"):
        t0 = time.monotonic()
        assert tq.tool_begin() is None
        assert time.monotonic() - t0 >= 0.25
    done.join()


def test_the_seat_queue_does_not_start_a_background_task_during_a_meeting(sched):
    class SQ:
        gate = on_run = on_done = None
    sq = SQ()
    tq.attach_seat_queue(sq)
    assert sq.gate("default") is True
    tq.request_background_pause("meeting:x", "you are in a meeting")
    try:
        assert sq.gate("default") is False
    finally:
        tq.clear_background_pause("meeting:x")


def test_the_notes_run_is_a_job_the_owner_asked_for(tmp_path, monkeypatch):
    import contextlib
    seen = []

    @contextlib.contextmanager
    def work(label, cls=None, **k):
        seen.append((label, cls))
        yield
    monkeypatch.setattr(ls, "work", work)
    r = Rig(tmp_path, monkeypatch, seat_resolver=lambda: "local-model",
            notes_call=lambda s, u, m: {"notes": ["a"], "decisions": [], "action_items": []})
    mid = r.start()["id"]
    r.src.on(tone(1.1, amp=9000))
    r.finish()
    assert r.m.get(mid)["summary"]["status"] == "done"
    assert ("Meeting notes", "user_job") in seen

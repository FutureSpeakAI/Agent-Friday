"""The server gives back what a voice call loaded in it, and the page does not
promise a warm-up time it cannot keep.

Live: after two calls the Friday server was 7.0 GB private (about 4.3 GB the
day before). The CPU ear, the CPU Kokoro (a ~1.8 GB pipeline) and Piper that a
call builds in the server process were kept in ``_HELD`` for ever. And the page
said "about 9 s" for an 87 s warm-up and "about 91 s" for a 106 s one.
"""
import gc
import logging
import weakref

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import voice_start_profile as vsp
from agent_friday.services import voice_workers as vw


# ── what a call builds in the server is released when it ends ───────────────

class _Tts:
    def __init__(self):
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1


class _Worker:
    def __init__(self):
        self.stopped = 0

    def stop(self, reason=""):
        self.stopped += 1

    def alive(self):
        return True


@pytest.fixture()
def held(monkeypatch):
    monkeypatch.setattr(vw, "_HELD", {})
    monkeypatch.setattr(vw, "_PREPARED", {})
    yield vw._HELD


def _kokoro_cpu():
    m = object.__new__(vw.KokoroCpuMouth)
    m._worker, m.voice, m.degraded = _Worker(), "af_heart", ""
    return m


def _cpu_ear():
    e = object.__new__(vw.CpuWhisperEar)

    class _Asr:
        unloaded = 0

        def unload(self):
            self.unloaded += 1
    e._asr = _Asr()
    return e


def _piper():
    p = object.__new__(vw.PiperMouth)
    p._tts, p.voice, p.degraded = _Tts(), "en_US-amy-medium", ""
    return p


def test_a_call_that_ends_leaves_no_cpu_engine_held_or_referenced(held, monkeypatch):
    monkeypatch.setattr(vw, "_rss_mib", lambda: 1000)
    mouth, ear, piper = _kokoro_cpu(), _cpu_ear(), _piper()
    refs = [weakref.ref(mouth._worker), weakref.ref(ear._asr), weakref.ref(piper._tts)]
    vw._hold("mouth", mouth)
    vw._hold("ear", ear)
    out = vw.release_cpu_engines("call ended")
    assert held == {} and vw._PREPARED == {}
    assert sorted(out["released"]) == ["ear(CpuWhisperEar)", "mouth(KokoroCpuMouth)"]
    assert mouth._worker.stopped == 1 and ear._asr.unloaded == 1
    # Piper too, when it is the floor engine
    vw._hold("mouth", piper)
    vw.release_cpu_engines("call ended")
    assert piper._tts.unloaded == 1 and vw._HELD == {}
    del mouth, ear, piper, out
    gc.collect()
    assert [r() for r in refs] == [None, None, None], "something still references a released engine"


def test_a_gpu_worker_is_not_touched_by_the_cpu_release(held):
    class _W:
        stopped = False

        def stop(self, r):
            _W.stopped = True

        def alive(self):
            return True
    gpu = object.__new__(vw.WorkerMouth)
    gpu.worker, gpu.degraded = _W(), ""
    vw._hold("mouth", gpu)
    assert vw.release_cpu_engines("call ended")["released"] == []
    assert vw._HELD.get("mouth") is gpu and not _W.stopped


def test_the_end_of_a_call_logs_the_server_rss_before_and_after(held, monkeypatch, caplog):
    seq = iter([3400, 1500])
    monkeypatch.setattr(vw, "_rss_mib", lambda: next(seq))
    vw._hold("mouth", _kokoro_cpu())
    with caplog.at_level(logging.INFO, logger="friday.voice_workers"):
        vw.release_cpu_engines("call ended")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("server RSS 3400 -> 1500 MiB" in m and "mouth(KokoroCpuMouth)" in m for m in msgs), msgs


def test_the_cpu_engines_unload_what_they_hold():
    from agent_friday.services import local_voice as lv
    asr = lv.WhisperASR("base", force_cpu=True)
    asr._model = object()
    asr.unload()
    assert asr._model is None
    tts = lv.PiperTTS("en_US-amy-medium")
    tts._piper = object()
    tts.unload()
    assert tts._piper is None


def test_the_session_releases_in_process_engines_at_every_way_out():
    import inspect
    src = inspect.getsource(rv)
    for reason in ("call ended", "start refused", "start failed", "voice front failed",
                   "start aborted"):
        assert '_vw.release_cpu_engines("%s")' % reason in src, reason


def test_the_cpu_kokoro_is_its_own_process_so_its_libraries_leave_with_it(monkeypatch):
    """Measured: releasing an in-process CPU Kokoro freed ~110 MiB of a 1.74 GB
    server (RSS 1740 -> 1628): the model goes, the imported torch/transformers
    stay. So the CPU Kokoro runs as a worker process that exits with the call."""
    m = vw.KokoroCpuMouth("af_heart")
    w = m._worker
    assert w.engine == "kokoro-cpu" and w.stage == "mouth" and w._want_lease is False
    assert w.args == {"voice": "af_heart"}
    from agent_friday.voice import worker
    eng = worker.build_engine("kokoro-cpu", voice="af_heart")
    assert eng._cpu is True and eng.device == "cpu"


def test_the_cpu_kokoro_worker_never_touches_cuda(monkeypatch):
    import sys
    import types
    from agent_friday.voice import worker
    monkeypatch.setattr(worker, "_cuda_init_s", lambda: pytest.fail("CUDA initialised"))
    monkeypatch.setattr(worker, "_cuda_free_mib", lambda: pytest.fail("the card was read"))
    made = {}

    class FakeTTS:
        _device = "cpu"

        def __init__(self, voice, allow_cpu=False, cpu_only=False, **k):
            made.update(allow_cpu=allow_cpu, cpu_only=cpu_only)

        def prepare(self, progress=None):
            pass

        def load(self, progress=None):
            pass

        def synthesize(self, text):
            return b""
    fake_kv = types.ModuleType("agent_friday.services.kokoro_voice")
    fake_kv.KokoroTTS = FakeTTS
    monkeypatch.setitem(sys.modules, "agent_friday.services.kokoro_voice", fake_kv)
    monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))
    eng = worker.build_engine("kokoro-cpu", voice="af_heart")
    eng.prepare()
    out = eng.load()
    assert made == {"allow_cpu": True, "cpu_only": True} and eng.device == "cpu"
    assert out["resident_mib"] is None, "no VRAM is claimed for a CPU engine"


# ── the warm-up text ────────────────────────────────────────────────────────

SEL = {"ear": {"device_policy": "if_free", "model": "small"},
       "mouth": {"engine": "kokoro", "device_policy": "preferred", "voice": "af_heart"}}
ROOM = {"model": "m"}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    f = tmp_path / "voice_start_timing.json"
    monkeypatch.setattr(vw, "_free_ram_mb", lambda: 8000)
    monkeypatch.setattr(vw, "held", lambda stage: None)
    monkeypatch.setattr(rv, "_front_reserve_mib", lambda room: 900 if (room or {}).get("model") else 0)

    def records(*rows):
        vsp.update(f, stages=[dict(stage=s, engine="e", total_s=t, **extra)
                              for s, t, extra in rows])
    return f, records


def text(f, plan=None, room=ROOM, sel=SEL):
    return rv._warm_up_text(sel, room, plan or {"prepared": [], "warm": False}, timing_file=f)


def test_short_of_memory_it_says_so_and_names_no_number(world, monkeypatch):
    f, records = world
    records(("ear", 4.0, {}), ("mouth", 20.0, {}), ("front", 7.0, {}))
    monkeypatch.setattr(vw, "_free_ram_mb", lambda: 1500)
    assert text(f) == ("Warming up the voice - this PC is low on memory, so it may take a "
                       "minute or two")


def test_the_number_is_the_slowest_recent_total_of_the_same_path_rounded_up(world):
    f, records = world
    records(("ear", 28.6, {}), ("ear", 14.4, {}), ("mouth", 36.0, {}), ("mouth", 22.6, {}),
            ("front", 7.1, {}))
    assert text(f) == "Warming up the voice - about 40 s"          # 36.0 -> 40


def test_one_slow_recent_start_is_not_averaged_away(world):
    # The live miss: records of ~9 s, then an 87 s start. "About 9 s" kept failing.
    f, records = world
    records(("ear", 4.0, {}), ("mouth", 9.0, {}), ("mouth", 9.5, {}), ("mouth", 87.0, {}),
            ("front", 7.0, {}))
    assert text(f) == "Warming up the voice - about 90 s"


def test_a_stage_with_no_record_for_its_path_gets_no_number(world):
    f, records = world
    records(("ear", 4.0, {}), ("mouth", 20.0, {}), ("front", 7.0, {}))     # all cold
    # this start prepares the mouth, and nothing measured that way yet
    assert text(f, {"prepared": ["mouth"], "warm": False}) == "Warming up the voice"


def test_each_stage_uses_the_records_of_the_way_it_will_start(world):
    f, records = world
    records(("ear", 25.0, {}), ("ear", 5.0, {"prepared_s": 3.0}),
            ("mouth", 36.0, {}), ("mouth", 6.0, {"prepared_s": 5.0, "preimported_s": 0.1}),
            ("front", 7.0, {}))
    plan = {"prepared": ["ear", "mouth"], "warm": True}
    assert text(f, plan) == "Warming up the voice - about 10 s"        # front 7.0 is the slowest


def test_stages_already_running_are_not_waited_for(world, monkeypatch):
    f, records = world
    records(("ear", 4.0, {}), ("front", 7.0, {}))                       # no mouth record at all
    monkeypatch.setattr(vw, "held", lambda stage: object.__new__(vw.WorkerMouth)
                        if stage == "mouth" else None)
    monkeypatch.setattr(vw.WorkerMouth, "voice", "af_heart", raising=False)
    assert text(f) == "Warming up the voice - about 10 s"


def test_no_records_means_no_number(world):
    f, _records = world
    assert text(f) == "Warming up the voice"


def test_the_number_never_undercuts_a_recent_measurement_of_that_path(world):
    f, records = world
    rows = [("ear", t, {}) for t in (3.1, 4.4, 27.9, 5.0, 4.0)] + \
           [("mouth", t, {}) for t in (12.0, 19.9, 20.1)] + [("front", 7.0, {})]
    records(*rows)
    said = int(text(f).split("about ")[1].split(" s")[0])
    assert said >= 27.9 and said % 5 == 0


def test_path_classification():
    assert vsp.path_of({}) == "cold"
    assert vsp.path_of({"prepared_s": 1}) == "prepared"
    assert vsp.path_of({"preimported_s": 0.2}) == "warm"
    assert vsp.path_of({"prepared_s": 1, "preimported_s": 0.2}) == "prepared+warm"

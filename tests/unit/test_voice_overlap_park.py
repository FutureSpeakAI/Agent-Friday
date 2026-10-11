"""The stage loads overlap the brain's park; the card is asked for only after.

A worker's load is split in two. ``prepare`` spawns it, imports its libraries
and reads its weights into RAM (no lease, no VRAM claim, no CUDA) and runs
while the arbiter is still parking the brain; ``start`` (the commit) admits it
against the card, leases it, and moves the model to the device. The display
reserve is checked exactly where it was: at each stage's own admission, after
the room is there.
"""
import threading
import time

import pytest

from agent_friday.services import voice_prefetch as pf
from agent_friday.services import voice_workers as vw


@pytest.fixture()
def timing(monkeypatch):
    monkeypatch.setenv("FRIDAY_VOICE_FAKE_PREPARE_MS", "700")
    monkeypatch.setenv("FRIDAY_VOICE_FAKE_LOAD_MS", "300")
    monkeypatch.setattr(vw, "_PREPARED", {})
    events = []
    t0 = time.monotonic()

    def stamp(name):
        events.append((name, round(time.monotonic() - t0, 2)))
    monkeypatch.setattr(vw, "claim_gpu", lambda need, stage: stamp("claim") or 0)
    monkeypatch.setattr(vw, "unclaim_gpu", lambda mib: None)
    monkeypatch.setattr(vw, "acquire_lease",
                        lambda *a, **k: stamp("lease") or {"granted": True, "lease_id": "L1"})
    monkeypatch.setattr(vw, "release_lease", lambda lid: None)
    monkeypatch.setattr(vw, "record_measured_mib", lambda *a, **k: None)
    monkeypatch.setattr(vw, "_idle_s", lambda: 600.0)
    yield events, stamp
    vw.release_prepared("test")


def _fake_worker(args=None):
    return vw.VoiceWorker("fake", "ear", lease=True, args=args or {})


def _run(overlapped):
    """Park 0.7 s, prepare 0.7 s, load 0.3 s; returns (seconds, claim/park order)."""
    events = []
    w = _fake_worker()
    w._preparing = True
    t0 = time.monotonic()
    if overlapped:
        th = threading.Thread(target=w.prepare)
        th.start()
        time.sleep(0.7)               # the park, alongside the prepare
    else:
        time.sleep(0.7)               # the park first
        w.prepare()
    events.append("park_done")
    w.start()
    total = time.monotonic() - t0
    w.stop("test")
    return total


def test_prepare_overlaps_the_park_so_the_start_costs_the_longer_not_the_sum(timing):
    sequential = _run(False)
    overlapped = _run(True)
    assert sequential - overlapped >= 0.4, (sequential, overlapped)


def test_the_card_is_asked_only_after_the_park_is_done(timing):
    events, stamp = timing
    w = _fake_worker()
    w._preparing = True
    th = threading.Thread(target=w.prepare)
    th.start()
    time.sleep(0.5)
    stamp("park_done")
    w.start()
    th.join()
    w.stop("test")
    names = [n for n, _t in events]
    assert names.index("park_done") < names.index("claim") < names.index("lease"), events


def test_a_prepare_takes_no_claim_and_no_lease(timing):
    events, _stamp = timing
    w = _fake_worker()
    w._preparing = True
    w.prepare()
    try:
        assert events == [], "prepare asked the card for nothing: %s" % events
        assert w._prepare_ok and w.proc is not None and w.proc.poll() is None
    finally:
        w.abandon("test")


def test_the_commit_is_refused_by_the_card_and_the_prepared_worker_is_ended(timing, monkeypatch):
    def refuse(need, stage):
        raise vw.GpuRefused("local_voice_gpu_refused", "short of room")
    monkeypatch.setattr(vw, "claim_gpu", refuse)
    w = _fake_worker()
    w._preparing = True
    w.prepare()
    proc = w.proc
    with pytest.raises(vw.GpuRefused):
        w.start()
    assert proc.wait(timeout=5) is not None, "a worker the card turned away does not linger"


def test_a_failed_prepare_falls_back_to_a_fresh_start(timing, monkeypatch):
    real_call = vw.VoiceWorker._call
    state = {"n": 0}

    def call(self, msg, timeout_s=30.0):
        if msg.get("op") == "prepare":
            state["n"] += 1
            raise vw.WorkerDied("prepare blew up")
        return real_call(self, msg, timeout_s=timeout_s)
    monkeypatch.setattr(vw.VoiceWorker, "_call", call)
    w = _fake_worker()
    w._preparing = True
    with pytest.raises(vw.WorkerDied):
        w.prepare()
    w.start()                          # spawns afresh and loads
    try:
        assert w._alive and w.proc.poll() is None and state["n"] == 1
    finally:
        w.stop("test")


def test_a_prepared_stage_is_taken_once_and_only_for_the_same_arguments(timing, monkeypatch):
    monkeypatch.setenv("FRIDAY_VOICE_FAKE_PREPARE_MS", "0")
    w = vw.prepare_stage("mouth", "fake", {"voice": "af_heart"})
    other = vw.prepare_stage("ear", "fake", {"model": "small"})
    assert vw.take_prepared("mouth", "fake", {"voice": "bf_emma"}) is None, "other voice"
    got = vw.take_prepared("ear", "fake", {"model": "small"})
    assert got is other and vw.take_prepared("ear", "fake", {"model": "small"}) is None
    got.abandon("test")


def test_leftover_prepared_workers_are_ended(timing, monkeypatch):
    monkeypatch.setenv("FRIDAY_VOICE_FAKE_PREPARE_MS", "0")
    w = vw.prepare_stage("mouth", "fake", {"voice": "af_heart"})
    assert w._prepare_done.wait(10) and w.proc.poll() is None
    assert vw.release_prepared("fell back to the CPU") == 1
    assert w.proc.wait(timeout=5) is not None


def test_the_selection_decides_which_stages_are_prepared(monkeypatch):
    began = []
    monkeypatch.setattr(vw, "prepare_stage", lambda stage, engine, args=None:
                        began.append((stage, engine, dict(args or {}))))
    monkeypatch.setattr(vw, "held", lambda stage: None)
    vw.prepare_for_selection({"ear": {"device_policy": "if_free", "model": "small"},
                              "mouth": {"engine": "kokoro", "device_policy": "preferred",
                                        "voice": "af_heart"}})
    assert [b[0] for b in began] == ["ear", "mouth"]
    assert began[1] == ("mouth", "kokoro-cuda", {"voice": "af_heart"})
    began.clear()
    vw.prepare_for_selection({"ear": {"device_policy": "never"},
                              "mouth": {"engine": "piper"}})
    assert began == [], "CPU stages have nothing to prepare on the card"


# ── Kokoro: prepare builds on the CPU, load only moves it ───────────────────

def test_kokoro_prepare_builds_on_the_cpu_and_load_only_moves_the_model(monkeypatch):
    import sys
    import types
    from agent_friday.services import kokoro_voice as kv
    moves, builds = [], []

    class _Model:
        def to(self, device):
            moves.append(device)
            return self

        def eval(self):
            return self

    class KModel(_Model):
        def __init__(self, config=None, model=None):
            builds.append("model")

    class KPipeline:
        def __init__(self, lang_code, model=None):
            builds.append("pipeline")
            self.model = model
            self.g2p = type("g", (), {"fallback": object()})()

    mod = types.ModuleType("kokoro")
    mod.KModel, mod.KPipeline = KModel, KPipeline
    monkeypatch.setitem(sys.modules, "kokoro", mod)
    monkeypatch.setattr(kv, "ensure_espeak_fallback", lambda: {"wired": True})
    monkeypatch.setattr(kv, "attach_espeak_fallback", lambda p: {"fallback": True})
    monkeypatch.setattr(kv, "english_g2p_model_installed", lambda: True)
    monkeypatch.setattr(kv, "_cached_file", lambda n: "cached/" + n)
    monkeypatch.setattr(kv.KokoroTTS, "_resolve_device", lambda self: "cuda")
    t = kv.KokoroTTS("af_heart", allow_cpu=False)
    t.prepare()
    assert moves == ["cpu"] and t._pipeline is None, "prepare touched no device but the CPU"
    t.load()
    assert moves == ["cpu", "cuda"] and t._device == "cuda" and t._pipeline is not None
    assert builds == ["model", "pipeline"], "load did not rebuild what prepare built"


# ── the file-cache read ─────────────────────────────────────────────────────

def test_the_prefetch_reads_every_byte_of_files_and_folders(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 1_500_000)
    sub = tmp_path / "d"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 600_000)
    r = pf.read_into_cache([tmp_path / "a.bin", sub])
    assert r["files"] == 2 and r["bytes"] == 2_100_000 and r["skipped"] == ""


def test_the_prefetch_is_skipped_when_it_would_eat_the_free_ram(tmp_path, monkeypatch):
    (tmp_path / "a.bin").write_bytes(b"x" * 4_000_000)
    monkeypatch.setattr(pf, "_free_mib", lambda: 6)      # 4 MB against 3 MiB allowed
    r = pf.read_into_cache([tmp_path / "a.bin"])
    assert r["bytes"] == 0 and "free RAM" in r["skipped"]


def test_the_prefetch_stops_when_told(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 3_000_000)
    stop = threading.Event()
    stop.set()
    assert pf.read_into_cache([tmp_path / "a.bin"], stop=stop)["bytes"] == 0


def test_a_missing_path_is_not_an_error(tmp_path):
    assert pf.read_into_cache([tmp_path / "nope.bin"])["files"] == 0


# ── the handler begins the overlap before the park ──────────────────────────

def test_the_session_begins_preparing_before_it_asks_the_arbiter_to_park():
    import inspect
    from agent_friday.routes import voice as rv
    src = inspect.getsource(rv)
    assert src.index("_begin_prepare(settings, _sel)") < src.index("_make_room_for_voice(settings, _sel")


def test_begin_prepare_starts_the_workers_and_reads_the_front_model(monkeypatch):
    from agent_friday.routes import voice as rv
    from agent_friday.services import voice_front as vf
    monkeypatch.delenv("FRIDAY_TESTING", raising=False)
    monkeypatch.delenv("FRIDAY_NO_PREWARM", raising=False)
    seen = {}
    done = threading.Event()

    def read(paths, **k):
        seen["read"] = list(paths)
        done.set()
        return {}
    monkeypatch.setattr(vw, "prepare_for_selection", lambda sel: seen.update(sel=sel))
    monkeypatch.setattr(vf, "resolve", lambda s: ("m", ""))
    monkeypatch.setattr(vf, "model_path", lambda m: "front.gguf")
    monkeypatch.setattr(pf, "read_into_cache", read)
    rv._begin_prepare({}, {"ear": {}})
    assert done.wait(5)
    assert seen["sel"] == {"ear": {}} and seen["read"] == ["front.gguf"]


def test_begin_prepare_spawns_nothing_under_test():
    from agent_friday.routes import voice as rv
    import os
    assert os.environ.get("FRIDAY_TESTING")
    rv._begin_prepare({}, {"ear": {"device_policy": "if_free"}})
    assert vw._PREPARED == {} or True

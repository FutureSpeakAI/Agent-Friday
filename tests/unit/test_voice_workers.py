"""Leased, process-bound voice engines (clean-sheet §3.2, §5).

The child is the REAL worker entry point (``agent_friday.voice.worker``) run
with ``--engine fake``, which loads nothing and speaks a tone, so the wire
protocol, the watchdog, idle unload and lease release are exercised end to
end without a GPU. The lease broker is an in-memory double: what is asserted
is that the worker's exit -- by any route -- releases the lease it held.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_friday.services import voice_workers as vw

_SRC = str(Path(__file__).resolve().parents[2] / "src")


@pytest.fixture
def broker(monkeypatch):
    """In-memory lease broker recording every transition."""
    st = {"leases": {}, "renews": 0, "admit": True, "admit_msg": ""}

    def acquire(engine, holder, mib, ttl_s):
        lid = f"lease_{len(st['leases']) + 1}"
        st["leases"][lid] = {"state": "held", "holder": holder, "mib": mib, "ttl": ttl_s}
        return {"granted": True, "lease_id": lid}

    def renew(lid, ttl):
        st["renews"] += 1

    def release(lid):
        if st["leases"].get(lid, {}).get("state") == "held":
            st["leases"][lid]["state"] = "released"

    def state(lid):
        return st["leases"].get(lid, {}).get("state")

    def admit(need, stage):
        if not st["admit"]:
            raise vw.GpuRefused("local_voice_gpu_refused", st["admit_msg"])
        return {"ok": True}
    monkeypatch.setattr(vw, "acquire_lease", acquire)
    monkeypatch.setattr(vw, "renew_lease", renew)
    monkeypatch.setattr(vw, "release_lease", release)
    monkeypatch.setattr(vw, "lease_state", state)
    monkeypatch.setattr(vw, "admit_gpu", admit)
    monkeypatch.setattr(vw, "record_measured_mib", lambda *a, **k: None)
    return st


def _spawn_fake(extra_env=None):
    def _s():
        env = dict(os.environ)
        env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env.update(extra_env or {})
        return subprocess.Popen([sys.executable, "-m", "agent_friday.voice.worker",
                                 "--engine", "fake"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env)
    return _s


def _wait(pred, s=5.0):
    t0 = time.time()
    while time.time() - t0 < s:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# ── §3.2 rule 7 ──────────────────────────────────────────────────────────────

def test_voice_worker_exit_releases_lease(broker):
    exits = []
    w = vw.VoiceWorker("fake", "mouth", idle_s=600, spawn=_spawn_fake(),
                       on_exit=lambda wk, reason: exits.append(reason))
    w.start()
    assert w.lease_id in broker["leases"]
    assert broker["leases"][w.lease_id]["state"] == "held"
    assert w.resident_mib == 7 and w.alive()
    # The child dies out from under the parent (crash, OOM, a stray kill).
    w.proc.kill()
    assert _wait(lambda: not w.alive())
    with pytest.raises(vw.WorkerDied):
        w.transcribe(b"\x00\x00" * 160)
    assert broker["leases"][w.lease_id]["state"] == "released"
    assert exits == ["died"]


def test_stop_releases_lease_and_terminates_child(broker):
    w = vw.VoiceWorker("fake", "ear", idle_s=600, spawn=_spawn_fake()).start()
    pid = w.proc.pid
    w.stop("closed")
    assert not w.alive()
    assert broker["leases"][w.lease_id]["state"] == "released"
    assert w.proc.pid == pid  # same child, gone


def test_idle_unload_exits_and_releases(broker):
    w = vw.VoiceWorker("fake", "mouth", idle_s=0.3, spawn=_spawn_fake()).start()
    assert w.alive()
    assert _wait(lambda: not w.alive(), 6.0)
    assert broker["leases"][w.lease_id]["state"] == "released"
    assert w._exit_reason == "idle"


def test_eviction_stops_the_worker_and_notices(broker):
    seen = []
    w = vw.VoiceWorker("fake", "ear", idle_s=600, spawn=_spawn_fake(),
                       on_evicted=lambda wk: seen.append(wk.stage)).start()
    broker["leases"][w.lease_id]["state"] = "evicted"
    assert _wait(lambda: not w.alive(), 6.0)
    assert seen == ["ear"]
    assert w._exit_reason == "evicted"


# ── the protocol ─────────────────────────────────────────────────────────────

def test_protocol_round_trip(broker):
    w = vw.VoiceWorker("fake", "mouth", idle_s=600, spawn=_spawn_fake(
        {"FRIDAY_VOICE_FAKE_TEXT": "the quick brown fox"})).start()
    try:
        assert w.ping()
        pcm = b"".join(w.synth_stream("Friday is ready to speak with you right now."))
        assert len(pcm) >= 24000 * 2 * 0.6          # >= 0.6 s of 24 kHz PCM16
        assert w.transcribe(b"\x00\x00" * 16000) == "the quick brown fox"
        assert broker["renews"] == 2                 # one per job
        d = w.describe()
        assert d["resident_mib"] == 7 and d["worker_pid"] == w.proc.pid
    finally:
        w.stop()


def test_cancel_abandons_a_running_synth(broker):
    w = vw.VoiceWorker("fake", "mouth", idle_s=600, spawn=_spawn_fake(
        {"FRIDAY_VOICE_FAKE_SLOW_MS": "40"})).start()
    try:
        cancel = threading.Event()
        got = []
        for chunk in w.synth_stream("one two three four five six seven eight nine ten "
                                    "eleven twelve thirteen fourteen fifteen", cancel):
            got.append(chunk)
            if len(got) == 2:
                cancel.set()
        assert len(got) <= 3                         # stopped within a chunk
        assert w.alive()                             # the worker survives a cancel
        assert w.ping()
    finally:
        w.stop()


def test_watchdog_kills_a_stalled_child(broker):
    w = vw.VoiceWorker("fake", "mouth", idle_s=600, stage_budget_ms=150,
                       spawn=_spawn_fake({"FRIDAY_VOICE_FAKE_SLOW_MS": "400"}))
    w.start()
    with pytest.raises(vw.WorkerDied, match="did not answer"):
        list(w.synth_stream("a very long sentence with many many words in it to speak"))
    assert not w.alive()
    assert broker["leases"][w.lease_id]["state"] == "released"


def test_load_failure_releases_lease(broker, monkeypatch):
    def bad_spawn():
        env = dict(os.environ)
        env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.Popen([sys.executable, "-m", "agent_friday.voice.worker",
                                 "--engine", "does-not-exist"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env)
    w = vw.VoiceWorker("fake", "ear", idle_s=600, spawn=bad_spawn)
    with pytest.raises(vw.WorkerDied):
        w.start()
    assert broker["leases"][w.lease_id]["state"] == "released"


# ── §5.3 the GPU queue ───────────────────────────────────────────────────────

def test_gpu_queue_barge_cancels_turn_jobs():
    q = vw.GpuQueue()
    ran = []
    gate = threading.Event()
    started = threading.Event()

    def slow(cancel):
        started.set()
        gate.wait(2.0)
        ran.append("running")
        return "ok"

    def clause(n):
        def _f(cancel):
            ran.append(n)
            return n
        return _f
    d0 = q.submit("turn-1", slow)
    assert started.wait(2.0)                         # it is RUNNING, not queued
    ds = [q.submit("turn-1", clause(i)) for i in range(1, 5)]
    other = q.submit("turn-2", clause("other"))
    assert q.depth() == 6
    dropped = q.cancel_turn("turn-1")
    assert dropped == 4
    assert all(d.cancelled and d.is_set() for d in ds)
    gate.set()
    assert other.wait(2.0) and other.result == "other"
    assert d0.wait(2.0) and d0.cancelled is True      # running job was flagged
    assert ran == ["running", "other"]
    # A late submit for a cancelled turn is refused until the turn is cleared.
    late = q.submit("turn-1", clause("late"))
    assert late.cancelled and late.is_set()
    q.clear_turn("turn-1")
    ok = q.submit("turn-1", clause("after"))
    assert ok.wait(2.0) and ok.result == "after"
    q.stop()
    q._thread.join(2.0)
    assert not q._thread.is_alive()                  # no leaked daemon thread


# ── admission (§3.2 rule 3) ──────────────────────────────────────────────────

def _admission(monkeypatch, *, cuda=True, torch_free_gb=11.5, smi_free_gb=2.0,
               reserve=2560):
    import agent_friday.services.nemo_voice as nv
    import agent_friday.services.hardware_profile as hwp
    monkeypatch.setattr(nv, "gpu_status", lambda fresh=False: {
        "cuda": cuda, "vram_free_gb": torch_free_gb,
        "vram_free_real_gb": smi_free_gb if cuda else None,
        "detail": "fake card"})
    monkeypatch.setattr(vw, "_display_reserve_mib", lambda: reserve)
    monkeypatch.setattr(hwp, "vram_headroom", lambda gpu_index=0, reserve_mib=None: {
        "ok": (smi_free_gb * 1024) >= (reserve_mib or 0), "free_mib": int(smi_free_gb * 1024),
        "display_reserve_mib": reserve_mib})


def test_gpu_admission_refuses_on_conservative_figure(monkeypatch):
    """F2 kept, now the ONLY admission path: torch says 11.5 GB free, nvidia-smi
    says 2.0 GB; against a 2,560 MiB display reserve the ear is refused, and
    the refusal is a sentence with the numbers in it."""
    _admission(monkeypatch, torch_free_gb=11.5, smi_free_gb=2.0)
    with pytest.raises(vw.GpuRefused) as ei:
        vw.admit_gpu(900, "ear")
    assert ei.value.code == "local_voice_gpu_refused"
    assert "2,048 MiB free against a 2,560 MiB display reserve" in ei.value.message
    assert "Running the ear on the CPU" in ei.value.message


def test_gpu_admission_passes_when_conservative_figure_has_room(monkeypatch):
    _admission(monkeypatch, torch_free_gb=11.5, smi_free_gb=6.7)
    d = vw.admit_gpu(900, "ear")
    assert d["ok"] and d["free_mib"] == int(6.7 * 1024) and d["reserve_mib"] == 2560


def test_gpu_admission_refuses_without_cuda(monkeypatch):
    _admission(monkeypatch, cuda=False)
    with pytest.raises(vw.GpuRefused, match="no CUDA device"):
        vw.admit_gpu(600, "mouth")


# ── the factory honours the GPU policy ───────────────────────────────────────

def test_build_ear_falls_to_cpu_with_a_notice_under_if_free(broker, monkeypatch):
    broker["admit"] = False
    broker["admit_msg"] = "GPU voice not loaded: 2,048 MiB free against a 2,560 MiB display reserve."
    vw.release_all()
    del vw.NOTICES[:]

    class _Cpu(vw.CpuWhisperEar):
        def __init__(self, model_size="small"):
            self.model = f"{model_size} int8"

        def load(self, progress=None):
            pass

        def transcribe(self, pcm):
            return "cpu heard it"
    monkeypatch.setattr(vw, "CpuWhisperEar", _Cpu)
    eng = vw.build_ear({"engine": "faster-whisper", "model": "small",
                        "device_policy": "if_free"})
    assert eng.device == "cpu" and eng.transcribe(b"") == "cpu heard it"
    assert vw.NOTICES and vw.NOTICES[-1]["code"] == "local_voice_gpu_refused"
    assert vw.held("ear") is eng
    # `required` refuses instead of falling.
    with pytest.raises(vw.GpuRefused):
        vw.build_ear({"engine": "faster-whisper", "model": "small",
                      "device_policy": "required"})
    vw.release_all()


def test_build_ear_uses_a_worker_when_admitted(broker, monkeypatch):
    vw.release_all()
    monkeypatch.setattr(vw.VoiceWorker, "_default_spawn", lambda self: _spawn_fake()())
    eng = vw.build_ear({"engine": "faster-whisper", "model": "small",
                        "device_policy": "if_free"})
    try:
        assert isinstance(eng, vw.WorkerEar)
        assert eng.describe()["device"] == "fake"
        assert vw.build_ear({"device_policy": "if_free", "model": "small"}) is eng
    finally:
        vw.release_all()
    assert broker["leases"][eng.worker.lease_id]["state"] == "released"


def test_build_mouth_serves_piper_when_kokoro_is_refused(broker, monkeypatch):
    broker["admit"] = False
    broker["admit_msg"] = "no room"
    vw.release_all()

    class _Piper(vw.PiperMouth):
        def __init__(self, voice="en_US-amy-medium"):
            self.voice = voice

        def load(self, progress=None):
            pass

        def synthesize_stream(self, text, cancel=None):
            yield b"\x00\x01" * 2400
    monkeypatch.setattr(vw, "PiperMouth", _Piper)
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: {})
    eng = vw.build_mouth({"engine": "kokoro", "voice": "af_heart",
                          "device_policy": "if_free"})
    assert eng.name == "piper" and eng.device == "cpu"
    pcm, eff = vw.run_mouth_proof({"engine": "kokoro", "voice": "af_heart",
                                   "device_policy": "if_free"}, "hi")
    assert eff["engine"] == "piper" and len(pcm) == 4800
    vw.release_all()

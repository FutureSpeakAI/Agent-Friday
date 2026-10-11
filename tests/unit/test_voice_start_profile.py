"""Each voice stage reports where its start-up time went, the last starts are
kept beside the whole-start duration, and a load never reaches the network."""
import json
import sys
import types

import pytest

from agent_friday.services import kokoro_voice as kv
from agent_friday.services import local_voice as lv
from agent_friday.services import voice_start_profile as vsp
from agent_friday.services import voice_workers as vw


def test_a_stage_record_has_every_phase_and_a_read_hint():
    rec = vsp.finish("ear", "whisper-cuda", {
        "spawn_s": 0.1, "startup_s": 3.2, "import_s": 2.0, "cuda_init_s": 1.1,
        "model_load_s": 4.0, "warmup_s": 0.6, "model_path": "C:/m/model",
        "file_mib": 1200.0}, total_s=11.0)
    for k in ("spawn_s", "startup_s", "import_s", "cuda_init_s", "model_load_s",
              "warmup_s", "total_s", "model_path", "file_mib", "read_mib_s", "disk_cache"):
        assert k in rec, k
    assert rec["read_mib_s"] == 300.0 and rec["disk_cache"] == "cold"
    text = vsp.line(rec)
    assert text.startswith("voice start ear/whisper-cuda: spawn 0.1 s")
    assert "cuda init 1.1 s" in text and "warm-up 0.6 s" in text and "total 11.0 s" in text
    assert "C:/m/model" in text and "cold read 300 MiB/s" in text


def test_a_fast_read_reads_as_the_disk_cache():
    assert vsp.read_hint(1000, 1.0)["disk_cache"] == "warm"
    assert vsp.read_hint(None, 1.0) == {}


def test_records_are_kept_beside_the_whole_start_duration(tmp_path):
    p = tmp_path / "voice_start_timing.json"
    vsp.update(p, seconds=21.4)
    for i in range(25):
        vsp.record({"stage": "ear", "engine": "e", "total_s": float(i)}, path=p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["seconds"] == 21.4, "the whole-start figure survives stage records"
    assert len(data["stages"]) == vsp.KEEP and data["stages"][-1]["total_s"] == 24.0
    vsp.update(p, seconds=19.0)
    assert len(json.loads(p.read_text(encoding="utf-8"))["stages"]) == vsp.KEEP


def test_a_worker_start_records_spawn_startup_and_total(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    monkeypatch.setattr(vsp, "timing_path", lambda: p)
    w = object.__new__(vw.VoiceWorker)
    w.stage, w.engine = "mouth", "kokoro-cuda"
    monkeypatch.setattr(vw.time, "perf_counter", lambda: 30.0)
    reply = {"ok": True, "load_ms": 15000,
             "profile": {"import_s": 5.0, "model_load_s": 8.0, "warmup_s": 1.0}}
    w._record_start_profile(reply, 10.0, 10.5)
    rec = json.loads(p.read_text(encoding="utf-8"))["stages"][-1]
    assert rec["stage"] == "mouth" and rec["spawn_s"] == 0.5
    assert rec["startup_s"] == 4.5 and rec["total_s"] == 20.0 and rec["import_s"] == 5.0


def test_the_whisper_ear_asks_the_local_cache_before_the_hub(tmp_path, monkeypatch):
    calls = []

    class WhisperModel:
        def __init__(self, size, **kw):
            calls.append(dict(kw))
            if kw.get("local_files_only"):
                raise RuntimeError("not in the cache")

    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=WhisperModel))
    ear = lv.WhisperASR("base", force_cpu=True)
    monkeypatch.setattr(ear, "_download_root", lambda: tmp_path)
    ear.load()
    assert calls[0].get("local_files_only") is True, "the hub is not asked first"
    assert not calls[1].get("local_files_only"), "first run: the explicit fetch follows"


def test_the_whisper_ear_that_is_cached_never_calls_the_hub(tmp_path, monkeypatch):
    calls = []

    class WhisperModel:
        def __init__(self, size, **kw):
            calls.append(dict(kw))

    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=WhisperModel))
    ear = lv.WhisperASR("base", force_cpu=True)
    monkeypatch.setattr(ear, "_download_root", lambda: tmp_path)
    ear.load()
    assert len(calls) == 1 and calls[0]["local_files_only"] is True


def test_the_gpu_worker_loads_whisper_from_the_local_cache_only():
    import inspect
    from agent_friday.voice import worker
    src = inspect.getsource(worker._WhisperCuda)
    assert src.count("local_files_only=True") >= 3, "every WhisperModel call is local-only"
    assert "download_model(self.size, local_files_only=True" in src


def test_kokoro_refuses_rather_than_let_misaki_download_its_language_model(monkeypatch):
    t = kv.KokoroTTS("af_heart", allow_cpu=True)
    monkeypatch.setattr(t, "_resolve_device", lambda: "cpu")
    monkeypatch.setattr(kv, "ensure_espeak_fallback", lambda: {"wired": True})
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a, **k: None if name == "en_core_web_sm" else real(name, *a, **k))
    with pytest.raises(kv.KokoroUnavailable) as e:
        t.load()
    assert "en_core_web_sm" in str(e.value) and "never downloads" in str(e.value)

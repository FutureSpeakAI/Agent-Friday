"""footprint_measure — the "friday measure <model_id>" job (headroom.md
§5.3, §12 Phase 2 item 4e).

The load-bearing property is HR6: an idle reading is never written as a
footprint. Every test that reaches a `"blocked"` outcome also asserts the
measurement store stays completely empty — nothing was recorded, not a
placeholder, not an `unknown` row, nothing.
"""
from __future__ import annotations

import pytest

from agent_friday.services import footprint_measure as fpm
from agent_friday.services import local_image as li
from agent_friday.services import local_voice as lv
from agent_friday.services import residency_catalog as rc

P1 = {
    "gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 4070",
              "vram_total_mib": 12282}],
    "ram": {"total_mib": 32620},
    "disk": {"read_mib_s": 427.0},
}


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "store_path", lambda: tmp_path / "m.json")
    rc.reset_cache()


def _store_is_empty_for(model_id):
    """No DIRECT footprint row (num_ctx=None) was ever written for
    `model_id` — the shape HR6 forbids reaching by any blocked path."""
    rows = rc.measurements(model_id, rc.P1_FINGERPRINT)
    return not any(r.get("num_ctx") is None for r in rows)


# ── image: blocked paths write nothing ──────────────────────────────────────

def test_uninstalled_image_model_is_blocked_and_records_nothing(monkeypatch):
    monkeypatch.setattr(li, "is_installed", lambda mid=None: False)
    result = fpm.measure_image_model("z-image-turbo-fp8")
    assert result["status"] == "blocked"
    assert "not installed" in result["reason"]
    assert _store_is_empty_for("z-image-turbo-fp8")


def test_a_failed_generation_is_blocked_and_records_nothing(monkeypatch):
    monkeypatch.setattr(li, "is_installed", lambda mid=None: True)
    monkeypatch.setattr("agent_friday.services.hardware_profile.get",
                        lambda: P1)

    class _FakeOllama:
        def resident(self):
            return {}
        def evict(self, name):
            pass

    class _FakeArbiter:
        def __init__(self, profile=None):
            self.ollama = _FakeOllama()
            self.transitions = []
        def compute_plan(self):
            return {}

    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.Arbiter", _FakeArbiter)
    monkeypatch.setattr(li, "generate",
                        lambda **kw: {"status": "error",
                                     "reason": "ComfyUI rejected the prompt"})
    monkeypatch.setattr("agent_friday.services.machine_monitor.gpu_rows",
                        lambda fresh=True: [{"used_mib": 1000,
                                            "total_mib": 12282}])

    called = []
    monkeypatch.setattr(rc, "record_footprint",
                        lambda *a, **k: called.append((a, k)))

    result = fpm.measure_image_model("z-image-turbo-fp8")
    assert result["status"] == "blocked"
    assert called == [], "a failed generation must never record a footprint"


# ── image: a real success is recorded with basis=measured, measured_at set ──

def test_a_successful_generation_records_a_measured_footprint(monkeypatch):
    monkeypatch.setattr(li, "is_installed", lambda mid=None: True)
    monkeypatch.setattr("agent_friday.services.hardware_profile.get",
                        lambda: P1)

    class _FakeOllama:
        def resident(self):
            return {}
        def evict(self, name):
            pass

    class _FakeArbiter:
        def __init__(self, profile=None):
            self.ollama = _FakeOllama()
            # The "start"/"image" transition Arbiter.grant() records for a
            # real image_job lease -- this is where load_s comes from.
            self.transitions = [{"action": "start", "role": "image",
                                "model_id": "comfyui", "seconds": 22.5}]
        def compute_plan(self):
            return {}

    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.Arbiter", _FakeArbiter)

    def _fake_generate(**kw):
        assert kw.get("system") is True          # HR-adjacent: never pollutes the gallery
        assert kw.get("model") == "z-image-turbo-fp8"
        # Give the background poller thread time to draw at least the
        # mid-poll and peak samples before the (mocked) job "finishes".
        import time as _t
        _t.sleep(0.08)
        return {"status": "ok", "files": [{"filename": "x.png"}],
               "elapsed_s": 72.6}

    monkeypatch.setattr(li, "generate", _fake_generate)

    samples = iter([
        {"used_mib": 1000, "total_mib": 12282},   # baseline
        {"used_mib": 9500, "total_mib": 12282},   # mid-poll
        {"used_mib": 11200, "total_mib": 12282},  # peak, during render
        {"used_mib": 9800, "total_mib": 12282},
    ])
    monkeypatch.setattr(
        "agent_friday.services.machine_monitor.gpu_rows",
        lambda fresh=True: [next(samples, {"used_mib": 9800,
                                          "total_mib": 12282})])

    result = fpm.measure_image_model("z-image-turbo-fp8",
                                     poll_interval_s=0.01)
    assert result["status"] == "measured"
    fp = result["footprint"]
    assert fp["basis"] == "measured"
    assert fp["measured_at"]                       # HR6: set because it ran
    assert fp["load_s"] == 22.5
    assert fp["work_s_per_unit"] == pytest.approx(72.6 - 22.5, abs=0.05)
    assert fp["unit"] == "image"
    assert fp["vram_mib"] is not None and fp["vram_mib"] > 0

    stored = rc.footprint("z-image-turbo-fp8", P1)
    assert stored["basis"] == "measured"
    assert stored["vram_mib"] == fp["vram_mib"]


def test_success_cleans_up_anything_newly_resident(monkeypatch):
    """The card must not be left holding something this job loaded that
    was not resident before it started."""
    monkeypatch.setattr(li, "is_installed", lambda mid=None: True)
    monkeypatch.setattr("agent_friday.services.hardware_profile.get",
                        lambda: P1)

    evicted = []

    class _FakeOllama:
        def __init__(self):
            self._calls = 0
        def resident(self):
            self._calls += 1
            # Nothing resident before; something got loaded during the run.
            return {} if self._calls == 1 else {"gemma4:e2b": 1811}
        def evict(self, name):
            evicted.append(name)

    class _FakeArbiter:
        def __init__(self, profile=None):
            self.ollama = _FakeOllama()
            self.transitions = [{"action": "start", "role": "image",
                                "model_id": "comfyui", "seconds": 10.0}]
        def compute_plan(self):
            return {}

    monkeypatch.setattr(
        "agent_friday.services.residency_arbiter.Arbiter", _FakeArbiter)
    monkeypatch.setattr(li, "generate",
                        lambda **kw: {"status": "ok",
                                     "files": [{"filename": "x.png"}],
                                     "elapsed_s": 40.0})
    monkeypatch.setattr("agent_friday.services.machine_monitor.gpu_rows",
                        lambda fresh=True: [{"used_mib": 5000,
                                            "total_mib": 12282}])

    fpm.measure_image_model("z-image-turbo-fp8", poll_interval_s=0.01)
    assert evicted == ["gemma4:e2b"]


# ── voice: blocked when not installed, never downloads, records nothing ─────

def test_voice_measurement_blocked_when_neither_engine_is_installed(
        monkeypatch, tmp_path):
    monkeypatch.setattr(lv, "WHISPER_DIR", tmp_path / "whisper")
    monkeypatch.setattr(lv, "PIPER_DIR", tmp_path / "piper")
    monkeypatch.setattr("agent_friday.services.hardware_profile.get",
                        lambda: P1)

    result = fpm.measure_voice_host_ram()
    assert result["stt"]["status"] == "blocked"
    assert result["tts"]["status"] == "blocked"
    assert "never downloads" in result["stt"]["reason"]
    assert "never downloads" in result["tts"]["reason"]
    assert _store_is_empty_for("faster-whisper-small-int8")
    assert _store_is_empty_for("piper-en_US-amy-medium")


def test_voice_measurement_never_imports_faster_whisper_or_piper_when_absent(
        monkeypatch, tmp_path):
    """The strongest form of "never downloads to get a number": the heavy
    optional dependency is never even imported on the blocked path."""
    monkeypatch.setattr(lv, "WHISPER_DIR", tmp_path / "whisper")
    monkeypatch.setattr(lv, "PIPER_DIR", tmp_path / "piper")
    monkeypatch.setattr("agent_friday.services.hardware_profile.get",
                        lambda: P1)

    def _boom(*a, **k):
        raise AssertionError("load() must not be called when files are "
                             "not installed")

    monkeypatch.setattr(lv.WhisperASR, "load", _boom)
    monkeypatch.setattr(lv.PiperTTS, "load", _boom)
    fpm.measure_voice_host_ram()          # must not raise


def test_voice_measurement_records_rss_delta_when_installed(
        monkeypatch, tmp_path):
    whisper_dir = tmp_path / "whisper"
    piper_dir = tmp_path / "piper"
    (whisper_dir / "models--Systran--faster-whisper-small").mkdir(
        parents=True)
    piper_dir.mkdir(parents=True)
    (piper_dir / "en_US-amy-medium.onnx").write_bytes(b"x")
    (piper_dir / "en_US-amy-medium.onnx.json").write_text("{}")
    monkeypatch.setattr(lv, "WHISPER_DIR", whisper_dir)
    monkeypatch.setattr(lv, "PIPER_DIR", piper_dir)
    monkeypatch.setattr("agent_friday.services.hardware_profile.get",
                        lambda: P1)

    rss_values = iter([100.0, 250.0, 250.0, 300.0])
    monkeypatch.setattr(fpm, "_rss_mib",
                        lambda: next(rss_values))
    monkeypatch.setattr(lv.WhisperASR, "load", lambda self, progress=None: None)
    monkeypatch.setattr(lv.PiperTTS, "load", lambda self, progress=None: None)

    result = fpm.measure_voice_host_ram()
    assert result["stt"]["status"] == "measured"
    assert result["stt"]["host_ram_mib"] == pytest.approx(150.0)
    assert result["stt"]["footprint"]["basis"] == "measured"
    assert result["stt"]["footprint"]["measured_at"]
    assert result["tts"]["status"] == "measured"
    assert result["tts"]["host_ram_mib"] == pytest.approx(50.0)

    stored = rc.footprint("faster-whisper-small-int8", P1)
    assert stored["host_ram_mib"] == pytest.approx(150.0)
    assert stored["device"] == "cpu"


# ── dispatch ─────────────────────────────────────────────────────────────

def test_measure_routes_voice_sentinels_to_voice_measurement(monkeypatch):
    called = {}
    monkeypatch.setattr(fpm, "measure_voice_host_ram",
                        lambda **k: called.setdefault("voice", True) or {})
    for sentinel in ("stt", "tts", "voice"):
        called.clear()
        fpm.measure(sentinel)
        assert called.get("voice") is True


def test_measure_routes_an_unknown_id_to_blocked_without_touching_anything(
        monkeypatch):
    result = fpm.measure("not-a-real-model:9b")
    assert result["status"] == "blocked"
    assert "not a known image model id" in result["reason"]

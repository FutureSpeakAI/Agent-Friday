"""Tier-1 ⇄ Tier-2 selection, hot-swap, graceful fallback, and perf monitoring.

Exercises the LocalVoiceEngine tier machinery (services/local_voice.py) plus the
provider/health/capability wiring for NeMo — all with mock backends, no GPU, no
torch-CUDA, no NeMo. Proves both tiers coexist and that Tier-1 still works when
the GPU stack is absent (the headline coexistence requirement).
"""
import array

from agent_friday.services import local_voice as lv
from agent_friday.services import nemo_voice as nv
from agent_friday.services.local_voice import LocalVoiceEngine


# ── mock backends (interface-compatible with both tiers) ─────────────────────

class _FakeASR:
    def __init__(self, *a, **k):
        self.model_size = "fake-asr"
        self.loaded = False

    def load(self, progress=None):
        self.loaded = True

    def transcribe(self, pcm16_16k):
        return "transcribed"


class _FakeTTS:
    def __init__(self, *a, **k):
        self.voice = "fake-tts"
        self.loaded = False

    def load(self, progress=None):
        self.loaded = True

    def synthesize(self, text):
        return array.array("h", [1000] * 240).tobytes()


class _ExplodingASR(_FakeASR):
    def load(self, progress=None):
        raise RuntimeError("GPU load boom")


# ── tier resolution ──────────────────────────────────────────────────────────

def test_resolve_tier_cpu_for_local_default():
    eng = LocalVoiceEngine()
    assert eng.resolve_tier({"voice_engine": "local"}) == "cpu"
    assert eng.resolve_tier({"voice_engine": "gemini"}) == "cpu"
    assert eng.resolve_tier({}) == "cpu"


def test_resolve_tier_gpu_when_ready(monkeypatch):
    eng = LocalVoiceEngine()
    monkeypatch.setattr(eng, "_gpu_tier_ready", lambda: True)
    assert eng.resolve_tier({"voice_engine": "local-gpu"}) == "gpu"
    assert eng.resolve_tier({"voice_engine": "auto"}) == "gpu"


def test_resolve_tier_gpu_falls_back_to_cpu_when_not_ready(monkeypatch):
    eng = LocalVoiceEngine()
    monkeypatch.setattr(eng, "_gpu_tier_ready", lambda: False)
    assert eng.resolve_tier({"voice_engine": "local-gpu"}) == "cpu"
    assert eng.resolve_tier({"voice_engine": "auto"}) == "cpu"


# ── hot-swap ─────────────────────────────────────────────────────────────────

def test_select_tier_swaps_and_resets_ready():
    eng = LocalVoiceEngine()
    eng._asr = _FakeASR()
    eng._tts = _FakeTTS()
    eng._ready = True
    eng._tier = "cpu"

    eng.select_tier("gpu")
    assert eng.active_tier() == "gpu"
    assert eng._asr is None and eng._tts is None
    assert eng._ready is False


def test_select_tier_noop_when_same():
    eng = LocalVoiceEngine()
    eng.select_tier("cpu")
    sentinel = _FakeASR()
    eng._asr = sentinel
    eng._ready = True
    eng.select_tier("cpu")            # same tier → must NOT drop the backend
    assert eng._asr is sentinel and eng._ready is True


def test_get_backends_use_nemo_on_gpu_tier(monkeypatch):
    monkeypatch.setattr(nv, "NeMoASR", _FakeASR)
    monkeypatch.setattr(nv, "NeMoTTS", _FakeTTS)
    eng = LocalVoiceEngine()
    eng.select_tier("gpu")
    assert isinstance(eng._get_asr(), _FakeASR)
    assert isinstance(eng._get_tts(), _FakeTTS)


def test_get_backends_use_whisper_piper_on_cpu_tier(monkeypatch):
    monkeypatch.setattr(lv, "WhisperASR", _FakeASR)
    monkeypatch.setattr(lv, "PiperTTS", _FakeTTS)
    eng = LocalVoiceEngine()
    eng.select_tier("cpu")
    assert isinstance(eng._get_asr(), _FakeASR)
    assert isinstance(eng._get_tts(), _FakeTTS)


# ── graceful fallback in ensure_ready ────────────────────────────────────────

def test_ensure_ready_gpu_preflight_fallback_to_cpu(monkeypatch):
    # GPU requested but not runnable → swap to CPU BEFORE importing the heavy
    # stack, then load the CPU backends successfully.
    eng = LocalVoiceEngine()
    monkeypatch.setattr(eng, "_gpu_tier_ready", lambda: False)
    monkeypatch.setattr(lv, "deps_installed", lambda: True)
    monkeypatch.setattr(lv, "WhisperASR", _FakeASR)
    monkeypatch.setattr(lv, "PiperTTS", _FakeTTS)
    eng.select_tier("gpu")

    msgs = []
    assert eng.ensure_ready(progress=msgs.append) is True
    assert eng.active_tier() == "cpu"
    assert any("CPU voice" in m for m in msgs)


def test_ensure_ready_gpu_load_failure_falls_back_to_cpu(monkeypatch):
    # GPU is "ready" so we try it, but the NeMo load raises → fall back to CPU.
    eng = LocalVoiceEngine()
    monkeypatch.setattr(eng, "_gpu_tier_ready", lambda: True)
    monkeypatch.setattr(nv, "nemo_deps_installed", lambda: True)
    monkeypatch.setattr(nv, "NeMoASR", _ExplodingASR)
    monkeypatch.setattr(nv, "NeMoTTS", _FakeTTS)
    monkeypatch.setattr(lv, "deps_installed", lambda: True)
    monkeypatch.setattr(lv, "WhisperASR", _FakeASR)
    monkeypatch.setattr(lv, "PiperTTS", _FakeTTS)
    eng.select_tier("gpu")

    assert eng.ensure_ready() is True
    assert eng.active_tier() == "cpu"      # degraded to the CPU tier


def test_ensure_ready_returns_false_when_no_tier_deps(monkeypatch):
    eng = LocalVoiceEngine()
    monkeypatch.setattr(eng, "_gpu_tier_ready", lambda: False)
    monkeypatch.setattr(lv, "deps_installed", lambda: False)
    eng.select_tier("cpu")
    assert eng.ensure_ready() is False


# ── perf monitoring ──────────────────────────────────────────────────────────

def test_perf_recorded_on_transcribe_and_synthesize():
    eng = LocalVoiceEngine()
    eng._asr = _FakeASR()
    eng._tts = _FakeTTS()
    eng._ready = True

    eng.transcribe(b"\x00\x01" * 100)
    eng.synthesize("hello")
    perf = eng.perf_stats()
    assert perf["asr_ms"] is not None and perf["asr_count"] == 1
    assert perf["tts_ms"] is not None and perf["tts_count"] == 1
    assert perf["tier"] == "cpu"


# ── health carries tier + perf + gpu sub-block ───────────────────────────────

def test_health_includes_tier_perf_and_gpu():
    eng = LocalVoiceEngine()
    h = eng.health()
    assert h["engine"] == "local-voice-lite"
    assert h["active_tier"] in ("cpu", "gpu")
    assert "perf" in h and "tier" in h["perf"]
    assert "gpu" in h and h["gpu"].get("engine") == "nvidia-nemo"


def test_health_unaffected_by_gpu_active_backend(monkeypatch):
    # Even with a GPU backend selected, the Tier-1 health block stays coherent
    # (it reports the local-voice-lite provider, not the live backend).
    monkeypatch.setattr(nv, "NeMoASR", _FakeASR)
    monkeypatch.setattr(nv, "NeMoTTS", _FakeTTS)
    eng = LocalVoiceEngine()
    eng.select_tier("gpu")
    h = eng.health()
    assert h["engine"] == "local-voice-lite"
    assert h["asr_model"] and h["tts_voice"]   # tier-1 names from settings


# ── provider / health / capability wiring ────────────────────────────────────

def test_nemo_provider_registered_and_enabled():
    from agent_friday.services.provider_registry import get_provider_registry
    p = get_provider_registry().get_provider("nvidia-nemo")
    assert p is not None
    assert p["enabled"] is True
    assert p["type"] == "nemo-local"
    assert set(p["capabilities"]) == {"asr", "tts"}


def test_nemo_provider_availability_gated_on_gpu(monkeypatch):
    from agent_friday.services.provider_registry import get_provider_registry
    reg = get_provider_registry()
    monkeypatch.setattr(nv, "gpu_tier_ready", lambda: False)
    assert reg.is_provider_available("nvidia-nemo") is False
    monkeypatch.setattr(nv, "gpu_tier_ready", lambda: True)
    assert reg.is_provider_available("nvidia-nemo") is True


def test_provider_health_nemo_branch():
    from agent_friday.services import provider_health
    h = provider_health.check_provider("nvidia-nemo", use_cache=False)
    assert h["provider"] == "nvidia-nemo"
    assert h["status"] in ("missing", "down", "needs_download", "ok", "error")


def test_capability_router_nemo_unlock_hint():
    from agent_friday.services import capability_router
    settings = {"capability_routing": {
        "tts": {"provider": "nvidia-nemo", "model": "nemo-fastpitch-hifigan"}}}
    res = capability_router.resolve("tts", settings)
    assert res["provider"] == "nvidia-nemo"
    if not res["available"]:
        assert "voice-local-gpu" in (res["unlock_hint"] or "")

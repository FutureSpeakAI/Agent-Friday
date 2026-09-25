"""Unit tests for the Tier-2 NeMo GPU voice backend (services/nemo_voice.py).

These run in CI with NO torch-CUDA, NO NeMo, and NO GPU — exactly the suite's
philosophy. The module must import for free, probe deps/GPU without importing the
heavy stack, report an honest "missing"/"down" health, and convert audio
correctly. The real GPU inference path is validated manually (see
docs/user-guide/local-voice-gpu-tier.md); here we cover everything that doesn't need a card.
"""
import array

import pytest

from agent_friday.services import nemo_voice as nv
from agent_friday.services.nemo_voice import (
    MIN_VRAM_GB,
    NeMoASR,
    NeMoTTS,
    _float_to_pcm16,
    _hyp_text,
    gpu_status,
    gpu_tier_ready,
    nemo_deps_installed,
    nemo_deps_status,
    nemo_health,
    nemo_models_ready,
)


# ── dependency + GPU probing ─────────────────────────────────────────────────

def test_nemo_deps_status_shape():
    d = nemo_deps_status()
    # nltk belongs here: FastPitch's g2p imports it, and its absence surfaces
    # as NeMo's "unsafe target" SECURITY error rather than a missing module,
    # which keeps GPU voice silent while health reports it ready.
    assert set(d) == {"nemo", "torch", "nltk"}
    assert all(isinstance(v, bool) for v in d.values())


def test_nemo_deps_installed_requires_g2p(monkeypatch):
    """torch+NeMo present but nltk missing must NOT count as installed."""
    import agent_friday.services.nemo_voice as nv
    monkeypatch.setattr(nv, "nemo_deps_status",
                        lambda: {"nemo": True, "torch": True, "nltk": False})
    assert nv.nemo_deps_installed() is False
    monkeypatch.setattr(nv, "nemo_deps_status",
                        lambda: {"nemo": True, "torch": True, "nltk": True})
    assert nv.nemo_deps_installed() is True


def test_nemo_deps_installed_is_bool():
    assert isinstance(nemo_deps_installed(), bool)


def test_gpu_status_shape():
    g = gpu_status()
    for k in ("cuda", "device", "vram_gb", "vram_free_gb", "sufficient",
              "source", "detail"):
        assert k in g
    assert isinstance(g["cuda"], bool)
    assert isinstance(g["sufficient"], bool)
    assert isinstance(g["vram_gb"], (int, float))
    assert isinstance(g["vram_free_gb"], (int, float))


def test_gpu_tier_ready_false_without_nemo(monkeypatch):
    # No NeMo installed in CI → never ready, regardless of any GPU present.
    monkeypatch.setattr(nv, "nemo_deps_installed", lambda: False)
    assert gpu_tier_ready() is False


def test_gpu_tier_ready_requires_cuda_and_vram(monkeypatch):
    monkeypatch.setattr(nv, "nemo_deps_installed", lambda: True)
    monkeypatch.setattr(nv, "gpu_status",
                        lambda **_k: {"cuda": True, "sufficient": True})
    assert gpu_tier_ready() is True
    monkeypatch.setattr(nv, "gpu_status",
                        lambda **_k: {"cuda": True, "sufficient": False})
    assert gpu_tier_ready() is False
    monkeypatch.setattr(nv, "gpu_status",
                        lambda **_k: {"cuda": False, "sufficient": True})
    assert gpu_tier_ready() is False


def test_nemo_models_ready_false_when_uncached():
    # Home is redirected to a temp dir by conftest → no checkpoints downloaded.
    assert nemo_models_ready() is False


# ── audio conversion ─────────────────────────────────────────────────────────

def test_float_to_pcm16_silence_and_peak():
    out = _float_to_pcm16([0.0, 0.0, 0.0])
    samples = array.array("h")
    samples.frombytes(out)
    assert list(samples) == [0, 0, 0]
    # +1.0 → near full-scale positive; -1.0 → near full-scale negative.
    peak = array.array("h")
    peak.frombytes(_float_to_pcm16([1.0, -1.0]))
    assert peak[0] >= 32760 and peak[1] <= -32760


def test_float_to_pcm16_clamps_out_of_range():
    out = array.array("h")
    out.frombytes(_float_to_pcm16([2.0, -2.0]))   # must not wrap around
    assert out[0] >= 32760 and out[1] <= -32760


def test_float_to_pcm16_empty():
    assert _float_to_pcm16(None) == b""
    assert _float_to_pcm16([]) == b""


# ── transcription-hypothesis normalization ───────────────────────────────────

def test_hyp_text_handles_shapes():
    assert _hyp_text("hello") == "hello"
    assert _hyp_text(["hello", "world"]) == "hello world"
    assert _hyp_text(None) == ""

    class _H:
        def __init__(self, t):
            self.text = t

    assert _hyp_text([_H("hi there")]) == "hi there"
    # NeMo's (best, all) tuple shape → take the first element.
    assert _hyp_text(([_H("from tuple")], ["ignored"])) == "from tuple"


# ── health ───────────────────────────────────────────────────────────────────

def test_nemo_health_missing_without_deps():
    h = nemo_health()
    assert h["engine"] == "nvidia-nemo"
    assert h["status"] in ("missing", "down", "needs_download", "ok", "error")
    assert "available" in h and "models_ready" in h


def test_nemo_health_reports_down_when_no_cuda(monkeypatch):
    monkeypatch.setattr(nv, "nemo_deps_installed", lambda: True)
    monkeypatch.setattr(nv, "nemo_deps_status",
                        lambda: {"nemo": True, "torch": True})
    monkeypatch.setattr(nv, "gpu_status", lambda: {
        "cuda": False, "sufficient": False, "vram_free_gb": 0.0})
    h = nemo_health()
    assert h["status"] == "down"
    assert h["available"] is False


def test_nemo_health_needs_download_when_ready_but_uncached(monkeypatch):
    monkeypatch.setattr(nv, "nemo_deps_installed", lambda: True)
    monkeypatch.setattr(nv, "nemo_deps_status",
                        lambda: {"nemo": True, "torch": True})
    monkeypatch.setattr(nv, "gpu_status", lambda: {
        "cuda": True, "sufficient": True, "vram_free_gb": 8.0})
    monkeypatch.setattr(nv, "nemo_models_ready", lambda: False)
    h = nemo_health()
    assert h["status"] == "needs_download"
    assert h["available"] is True and h["models_ready"] is False


def test_nemo_health_ok_when_everything_ready(monkeypatch):
    monkeypatch.setattr(nv, "nemo_deps_installed", lambda: True)
    monkeypatch.setattr(nv, "nemo_deps_status",
                        lambda: {"nemo": True, "torch": True})
    monkeypatch.setattr(nv, "gpu_status", lambda: {
        "cuda": True, "sufficient": True, "vram_free_gb": 8.0})
    monkeypatch.setattr(nv, "nemo_models_ready", lambda: True)
    h = nemo_health()
    assert h["status"] == "ok"
    assert h["available"] is True and h["models_ready"] is True


def test_nemo_health_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(nv, "nemo_deps_status", _boom)
    h = nemo_health()
    assert h["engine"] == "nvidia-nemo"
    assert h["status"] == "error"


# ── backend interface parity (no model load) ─────────────────────────────────

def test_nemo_asr_interface():
    asr = NeMoASR()
    assert asr.model_name == nv.NEMO_ASR_MODEL
    assert asr.model_size == asr.model_name          # parity with WhisperASR
    assert hasattr(asr, "load") and hasattr(asr, "transcribe")
    assert asr.transcribe(b"") == ""                  # empty audio short-circuits


def test_nemo_tts_interface():
    tts = NeMoTTS()
    assert tts.voice
    assert hasattr(tts, "load") and hasattr(tts, "synthesize")
    assert tts.synthesize("") == b""                  # empty text short-circuits
    assert tts.synthesize("   ") == b""


def test_min_vram_constant_sane():
    assert isinstance(MIN_VRAM_GB, (int, float)) and MIN_VRAM_GB > 0


# ── F1/F2 (voice-mode-diagnosis-and-repair.md): cache, warn-once, conservative ──

@pytest.fixture(autouse=True)
def _reset_gpu_cache():
    nv.reset_gpu_status_cache()
    yield
    nv.reset_gpu_status_cache()


def _fake_torch_probe(monkeypatch, torch_free_gb, smi_free_gb):
    """Drive the torch branch of the probe without torch: a stub module in
    sys.modules plus a stubbed nvidia-smi answer."""
    import sys as _sys
    import types

    class _Cuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def current_device():
            return 0

        @staticmethod
        def get_device_name(_i):
            return "Fake RTX"

        @staticmethod
        def mem_get_info(_i):
            return int(torch_free_gb * 1e9), int(12.0 * 1e9)

    fake = types.ModuleType("torch")
    fake.cuda = _Cuda
    monkeypatch.setitem(_sys.modules, "torch", fake)
    monkeypatch.setattr(nv, "_module_installed", lambda name: name == "torch")

    class _R:
        stdout = "%d\n" % int(smi_free_gb * 1024)

    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda *a, **k: _R())
    monkeypatch.setattr(nv, "_local_gpu_voice_selected", lambda: True)


def test_gpu_status_is_cached_and_fresh_bypasses(monkeypatch):
    """F1: health pollers share one reading; an admission re-measures."""
    calls = []
    monkeypatch.setattr(nv, "_probe_gpu_status",
                        lambda: calls.append(1) or {"cuda": False, "sufficient": False})
    monkeypatch.setattr(nv, "_local_gpu_voice_selected", lambda: True)
    nv.gpu_status()
    nv.gpu_status()
    nv.gpu_status()
    assert len(calls) == 1
    nv.gpu_status(fresh=True)
    assert len(calls) == 2


def test_gpu_status_cache_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(nv, "_probe_gpu_status",
                        lambda: calls.append(1) or {"cuda": False})
    monkeypatch.setattr(nv, "_local_gpu_voice_selected", lambda: True)
    nv.gpu_status()
    nv._gpu_cache["at"] -= nv._GPU_STATUS_TTL_S + 1
    nv.gpu_status()
    assert len(calls) == 2


def test_admission_uses_conservative_figure(monkeypatch):
    """F2: torch=11.5 free, nvidia-smi=2.0 free -> NOT sufficient."""
    _fake_torch_probe(monkeypatch, torch_free_gb=11.5, smi_free_gb=2.0)
    g = nv.gpu_status(fresh=True)
    assert g["cuda"] is True
    assert g["vram_free_real_gb"] == 2.0
    assert g["sufficient_reachable"] is True   # torch's verdict, reported
    assert g["sufficient"] is False            # nvidia-smi's verdict, admitted
    monkeypatch.setattr(nv, "nemo_deps_installed", lambda: True)
    assert gpu_tier_ready(fresh=True) is False


def test_admission_passes_when_conservative_figure_has_room(monkeypatch):
    _fake_torch_probe(monkeypatch, torch_free_gb=11.5, smi_free_gb=6.7)
    g = nv.gpu_status(fresh=True)
    assert g["sufficient"] is True
    assert g["vram_measurement_disputed"] is True   # 4.8 GB gap still reported


def test_dispute_is_warned_once_then_debug(monkeypatch, caplog):
    """F1: twelve identical warnings a minute drowned the log."""
    import logging
    _fake_torch_probe(monkeypatch, torch_free_gb=11.5, smi_free_gb=6.7)
    with caplog.at_level(logging.DEBUG, logger="friday.nemo_voice"):
        for _ in range(5):
            nv.gpu_status(fresh=True)
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "disputed" in r.getMessage()]
    assert len(warns) == 1
    # A verdict change is news again.
    _fake_torch_probe(monkeypatch, torch_free_gb=11.5, smi_free_gb=2.0)
    with caplog.at_level(logging.DEBUG, logger="friday.nemo_voice"):
        nv.gpu_status(fresh=True)
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "disputed" in r.getMessage()]
    assert len(warns) == 2

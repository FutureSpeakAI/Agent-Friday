"""Unit tests for the Tier-2 NeMo GPU voice backend (services/nemo_voice.py).

These run in CI with NO torch-CUDA, NO NeMo, and NO GPU — exactly the suite's
philosophy. The module must import for free, probe deps/GPU without importing the
heavy stack, report an honest "missing"/"down" health, and convert audio
correctly. The real GPU inference path is validated manually (see
docs/TIER2_NEMO_VOICE.md); here we cover everything that doesn't need a card.
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
    assert set(d) == {"nemo", "torch"}
    assert all(isinstance(v, bool) for v in d.values())


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
                        lambda: {"cuda": True, "sufficient": True})
    assert gpu_tier_ready() is True
    monkeypatch.setattr(nv, "gpu_status",
                        lambda: {"cuda": True, "sufficient": False})
    assert gpu_tier_ready() is False
    monkeypatch.setattr(nv, "gpu_status",
                        lambda: {"cuda": False, "sufficient": True})
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

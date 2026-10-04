"""A local voice session is never refused for its mouth (local voice spec P0).

The GPU admission catch-22: with the brain resident the card has ~1 GB free
against a 2,560 MiB display reserve, so the Kokoro worker is refused. Under
``voice_mouth_gpu = "required"`` that refusal used to raise ``GpuRefused`` out
of ``build_mouth`` and the WS handler refused the whole session. The mouth now
degrades (GPU -> Kokoro on the CPU -> Piper) under every policy and says so;
the manifest proves a CPU mouth under ``required`` with the reason shown; and
the session's ``served_by`` frame names what is speaking (``kokoro@cpu``).

No GPU, no model: the GPU worker and the CPU Kokoro engine are fakes.
"""
import pytest

from agent_friday.services import voice_manifest as vm
from agent_friday.services import voice_workers as vw


class _RefusedWorker:
    """A GPU worker the card refuses, as admission does with the brain resident."""

    def __init__(self, *a, **k):
        pass

    def start(self, progress=None):
        raise vw.GpuRefused("local_voice_gpu_refused",
                            "GPU voice not loaded: 1,228 MiB free against a 2,560 "
                            "MiB display reserve (the mouth needs about 384 MiB).")


class _CpuKokoro(vw.KokoroCpuMouth):
    """Kokoro on the CPU without loading a model."""

    def __init__(self, voice="af_heart"):
        self.voice = voice
        self.device = "cpu"

    def load(self, progress=None):
        self.device = "cpu"

    def synthesize_stream(self, text, cancel=None):
        yield b"\x00\x01" * 24000


@pytest.fixture
def refused_card(monkeypatch):
    vw.release_all()
    monkeypatch.setattr(vw, "VoiceWorker", _RefusedWorker)
    monkeypatch.setattr(vw, "KokoroCpuMouth", _CpuKokoro)
    monkeypatch.setattr("agent_friday.core._load_settings",
                        lambda: {"local_voice_kokoro_allow_cpu": True})
    yield
    vw.release_all()
    del vw.NOTICES[:]


def test_required_gpu_mouth_degrades_to_cpu_instead_of_refusing(refused_card):
    eng = vw.build_mouth({"engine": "kokoro", "voice": "af_heart",
                          "device_policy": "required"})
    assert eng.name == "kokoro" and eng.device == "cpu", (
        "a refused card must leave Friday speaking on the CPU, not refuse the session")
    assert "2,560" in eng.describe()["degraded"], "the degradation must say why"
    assert any(n["code"] == "local_voice_gpu_refused" for n in vw.NOTICES)


def test_the_kokoro_cpu_default_is_on():
    from agent_friday import core
    assert core.DEFAULT_SETTINGS["local_voice_kokoro_allow_cpu"] is True


def test_manifest_proves_a_cpu_mouth_under_required_with_the_reason(monkeypatch):
    monkeypatch.setattr(vm, "_settings", lambda: {"local_voice_tts_engine": "kokoro",
                                                   "voice_mouth_gpu": "required"})
    m = vm.VoiceManifest()
    monkeypatch.setitem(vm.ENGINE_RUNNERS, "mouth",
                        lambda sel, text, prog: (b"\x00\x01" * 24000,
                                                 {"engine": "kokoro", "device": "cpu",
                                                  "degraded": "card busy"}))
    m.prove("mouth")
    st = m.snapshot_stage("mouth")
    assert st["proof"]["state"] == "proven", (
        "a mouth serving on the CPU under 'required' refused the session: %r" % st)
    assert "required the GPU" in st["reason"] and "card busy" in st["reason"]


def test_served_by_names_the_mouth_that_is_actually_speaking(refused_card):
    from agent_friday.routes.voice import _served_by

    class _Ear:
        def describe(self):
            return {"engine": "faster-whisper", "device": "cpu", "model": "base int8"}
    mouth = vw.build_mouth({"engine": "kokoro", "voice": "af_heart",
                            "device_policy": "required"})
    sb = _served_by(_Ear(), mouth, "bonsai2:27b")
    assert sb["mouth"] == "kokoro@cpu"
    # No voice front here: the brain answers, and the frame says at what pace.
    assert sb["mind"] == "bonsai2:27b@local (thinking mode)"
    assert sb["brain"] == "bonsai2:27b@local"
    assert sb["degraded"], "a degraded mouth is stated in the frame"


@pytest.mark.parametrize("value", ["preferred", "if_free", "required", "never"])
def test_mouth_gpu_policy_accepts_preferred(value):
    from agent_friday.routes.core_routes import _check_voice_enums
    assert _check_voice_enums({"voice_mouth_gpu": value}) is None

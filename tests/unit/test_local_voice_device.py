"""The ear uses the GPU when there is one, and CPU is the floor, not the ceiling.

Local voice transcribed on CPU int8 regardless of what the machine had. On an
RTX 4070, measured warm against 4.49s of speech with an identical transcript
either way:

    cpu  int8     2.13s  =  2.1x realtime
    cuda float16  0.13s  = 34.2x realtime

Two seconds of silence after every sentence is not a conversation, and that is
the WARM number — the first utterance of a session also paid ~30s of model
load. Between them, that is what "I couldn't get anything to work" looks like
from the outside.

Tier-1's promise is that local voice runs anywhere, so CPU int8 stays the
fallback: no CUDA, a tight card, or a CUDA build that will not initialise all
land back on it rather than taking voice down.
"""
import sys
import types

import pytest

pytest.importorskip("faster_whisper", reason="local voice deps not installed")

from agent_friday.services.local_voice import WhisperASR  # noqa: E402

GiB = 1024 ** 3


def _fake_torch(available=True, free_gib=8.0):
    t = types.SimpleNamespace()
    t.cuda = types.SimpleNamespace(
        is_available=lambda: available,
        mem_get_info=lambda: (int(free_gib * GiB), int(12 * GiB)),
    )
    return t


@pytest.fixture
def asr():
    return WhisperASR("base")


def _with_torch(monkeypatch, torch_mod):
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


def test_a_capable_gpu_is_used(monkeypatch, asr):
    _with_torch(monkeypatch, _fake_torch(available=True, free_gib=8.0))
    device, compute, why = asr._pick_device()
    assert (device, compute) == ("cuda", "float16"), (
        "a card with 8 GiB free should be used; CPU int8 costs 16x here")
    assert why == ""


def test_no_cuda_falls_back_to_cpu(monkeypatch, asr):
    _with_torch(monkeypatch, _fake_torch(available=False))
    device, compute, why = asr._pick_device()
    assert (device, compute) == ("cpu", "int8")
    assert "CUDA" in why


def test_a_card_the_brain_is_using_is_left_alone(monkeypatch, asr):
    """The model that answers the question matters more than the one that hears it."""
    _with_torch(monkeypatch, _fake_torch(available=True, free_gib=0.4))
    device, compute, why = asr._pick_device()
    assert (device, compute) == ("cpu", "int8"), (
        "with 0.4 GiB free the ear must not push the brain off the GPU")
    assert "free" in why.lower()


def test_a_broken_torch_does_not_take_voice_down(monkeypatch, asr):
    broken = types.SimpleNamespace()

    def boom():
        raise RuntimeError("driver mismatch")

    broken.cuda = types.SimpleNamespace(is_available=boom)
    _with_torch(monkeypatch, broken)
    device, compute, why = asr._pick_device()
    assert (device, compute) == ("cpu", "int8")
    assert why


def test_cuda_that_will_not_initialise_falls_back_rather_than_raising(monkeypatch, asr):
    """A CUDA build that loads but cannot start must not be fatal."""
    _with_torch(monkeypatch, _fake_torch(available=True, free_gib=8.0))
    calls = []

    class FakeModel:
        def __init__(self, size, device=None, compute_type=None, download_root=None):
            calls.append(device)
            if device == "cuda":
                raise RuntimeError("cublas failed to initialise")

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    asr.load()
    assert calls == ["cuda", "cpu"], (
        "it should try the GPU, fail, and land on CPU: got %r" % (calls,))
    assert asr._device == "cpu"
    assert "cublas" in (asr._why_cpu or "")

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

The card is found by CTranslate2 / nvidia-smi (services/gpu_probe), never by
torch: the bundled torch is a CPU-only build that says "no CUDA" on an RTX
laptop. Every test here stubs the probe and installs such a torch, so none
touches a real GPU; CUDA's compute type is int8_float16.
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


@pytest.fixture(autouse=True)
def every_size_is_installed(monkeypatch):
    """The ear loads a model by its folder (feature_models.whisper_model_dir)
    and never downloads one. Here every size is "installed" at a folder named
    after the size, so the recorded load names the size as before."""
    from agent_friday.services import feature_models as fm
    monkeypatch.setattr(fm, "whisper_model_dir", lambda size, first_root=None: size)


@pytest.fixture
def asr():
    return WhisperASR("base")


def _card(available=True, free_gib=8.0):
    return {"cuda": available, "devices": 1 if available else 0, "source": "ctranslate2",
            "free_mib": int(free_gib * 1024) if available else None,
            "detail": "" if available else "CTranslate2 sees no CUDA device"}


def _with_card(monkeypatch, card):
    """What CTranslate2 and the driver report, with a CPU-only torch beside it
    that must not be asked."""
    from agent_friday.services import gpu_probe
    monkeypatch.setattr(gpu_probe, "probe", lambda refresh=False: dict(card))
    monkeypatch.setattr(gpu_probe, "ensure_cublas_on_path", lambda *a, **k: {"added": False})
    # CTranslate2 can find cuBLAS (tests of a card without it are in test_gpu_probe).
    monkeypatch.setattr(gpu_probe, "cublas_status",
                        lambda env=None: {"ok": True, "how": "findable", "dir": None, "reason": ""},
                        raising=False)
    monkeypatch.setattr(gpu_probe, "prepare_cublas_for_cuda", lambda env=None: (True, ""),
                        raising=False)
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(available=False, free_gib=0))


def test_a_capable_gpu_is_used(monkeypatch, asr):
    _with_card(monkeypatch, _card(available=True, free_gib=8.0))
    device, compute, why = asr._pick_device()
    assert (device, compute) == ("cuda", "int8_float16"), (
        "a card with 8 GiB free should be used even though torch is CPU-only; "
        "CPU int8 costs 16x here")
    assert why == ""


def test_no_cuda_falls_back_to_cpu(monkeypatch, asr):
    _with_card(monkeypatch, _card(available=False))
    device, compute, why = asr._pick_device()
    assert (device, compute) == ("cpu", "int8")
    assert "CUDA" in why


def test_a_card_the_brain_is_using_is_left_alone(monkeypatch, asr):
    """The model that answers the question matters more than the one that hears it."""
    _with_card(monkeypatch, _card(available=True, free_gib=0.4))
    device, compute, why = asr._pick_device()
    assert (device, compute) == ("cpu", "int8"), (
        "with 0.4 GiB free the ear must not push the brain off the GPU")
    assert "free" in why.lower()


def test_a_busy_arbiter_gets_the_card(monkeypatch, asr):
    """The arbiter owns the GPU; the ear has a perfectly good CPU path."""
    import agent_friday.services.residency_arbiter as ra
    _with_card(monkeypatch, _card(available=True, free_gib=8.0))
    monkeypatch.setattr(ra, "ARBITER", types.SimpleNamespace(
        status=lambda: {"state": ra.STATE_DEFAULT,
                        "lease": {"kind": "image"}}), raising=False)
    device, _c, why = asr._pick_device()
    assert device == "cpu", "an image lease means the card is spoken for"
    assert "lease" in why


def test_an_arbiter_mid_transition_gets_the_card(monkeypatch, asr):
    import agent_friday.services.residency_arbiter as ra
    _with_card(monkeypatch, _card(available=True, free_gib=8.0))
    monkeypatch.setattr(ra, "ARBITER", types.SimpleNamespace(
        status=lambda: {"state": "transitioning", "lease": None}), raising=False)
    device, _c, why = asr._pick_device()
    assert device == "cpu"
    assert "arbiter" in why.lower()


def test_an_idle_arbiter_does_not_block_the_gpu(monkeypatch, asr):
    import agent_friday.services.residency_arbiter as ra
    _with_card(monkeypatch, _card(available=True, free_gib=8.0))
    monkeypatch.setattr(ra, "ARBITER", types.SimpleNamespace(
        status=lambda: {"state": ra.STATE_DEFAULT, "lease": None}), raising=False)
    assert asr._pick_device()[0] == "cuda"


def test_a_broken_probe_does_not_take_voice_down(monkeypatch, asr):
    from agent_friday.services import gpu_probe
    _with_card(monkeypatch, _card(available=True))

    def boom(refresh=False):
        raise RuntimeError("driver mismatch")

    monkeypatch.setattr(gpu_probe, "probe", boom)
    device, compute, why = asr._pick_device()
    assert (device, compute) == ("cpu", "int8")
    assert why


def test_cuda_that_will_not_initialise_falls_back_rather_than_raising(monkeypatch, asr):
    """A CUDA build that loads but cannot start must not be fatal."""
    _with_card(monkeypatch, _card(available=True, free_gib=8.0))
    calls = []

    class FakeModel:
        def __init__(self, size, device=None, compute_type=None, download_root=None, **kw):
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


# ── the model SIZE follows the device on a fresh install ────────────────────
#
# A fresh install writes the default `local_voice_asr_model` into settings.json,
# so the default has to be a value that means "pick for this machine": on a
# laptop with no usable GPU, whisper "small" on CPU int8 is slow; "base" is the
# size that keeps a sentence feeling like a conversation. A size the user
# picked is used as given.

def _record_loads(monkeypatch):
    calls = []

    class FakeModel:
        cuda_fails = False

        def __init__(self, size, device=None, compute_type=None, download_root=None, **kw):
            calls.append((size, device, compute_type))
            if device == "cuda" and FakeModel.cuda_fails:
                raise RuntimeError("cublas failed to initialise")

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
    return calls, FakeModel


def test_a_fresh_install_on_a_laptop_loads_base_on_cpu_int8(monkeypatch):
    from agent_friday.core import DEFAULT_SETTINGS
    _with_card(monkeypatch, _card(available=False))
    calls, _m = _record_loads(monkeypatch)
    asr = WhisperASR(DEFAULT_SETTINGS["local_voice_asr_model"])
    asr.load()
    assert calls == [("base", "cpu", "int8")], calls
    assert asr.model_size == "base"


def test_a_fresh_install_with_a_capable_gpu_loads_small_on_cuda(monkeypatch):
    from agent_friday.core import DEFAULT_SETTINGS
    _with_card(monkeypatch, _card(available=True, free_gib=8.0))
    calls, _m = _record_loads(monkeypatch)
    WhisperASR(DEFAULT_SETTINGS["local_voice_asr_model"]).load()
    assert calls == [("small", "cuda", "int8_float16")], calls


def test_an_automatic_size_shrinks_when_cuda_falls_back_to_cpu(monkeypatch):
    _with_card(monkeypatch, _card(available=True, free_gib=8.0))
    calls, model = _record_loads(monkeypatch)
    model.cuda_fails = True
    WhisperASR("auto").load()
    assert calls == [("small", "cuda", "int8_float16"), ("base", "cpu", "int8")], calls


def test_a_size_the_user_chose_is_kept_on_cpu(monkeypatch):
    _with_card(monkeypatch, _card(available=False))
    calls, _m = _record_loads(monkeypatch)
    WhisperASR("small").load()
    assert calls == [("small", "cpu", "int8")], calls


def test_the_cpu_ear_never_puts_whisper_on_the_gpu(monkeypatch):
    """The GPU worker was refused (display reserve, arbiter); the fallback is
    the CPU, not an in-process float16 load onto the same card."""
    from agent_friday.services import voice_workers as vw
    _with_card(monkeypatch, _card(available=True, free_gib=8.0))
    calls, _m = _record_loads(monkeypatch)
    ear = vw.CpuWhisperEar("auto")
    ear.load()
    assert calls == [("base", "cpu", "int8")], calls
    assert ear.describe() == {"engine": "faster-whisper", "device": "cpu",
                              "model": "base int8"}


# ── no test reaches the real card through the probe (tests/conftest.py) ─────

def test_a_probe_answer_left_by_one_test_is_seeded():
    """Leaves an answer in the probe's cache; the next test must not see it."""
    import os
    from agent_friday.services import gpu_probe
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == "-1", "the shared fixture hides CUDA"
    gpu_probe._COUNT = {"devices": 1, "source": "ctranslate2", "detail": ""}


def test_b_the_next_test_starts_with_an_empty_probe_cache():
    from agent_friday.services import gpu_probe
    assert gpu_probe._COUNT is None, "conftest resets gpu_probe between tests"
    assert gpu_probe.probe()["cuda"] is False, "and CUDA is hidden, so nothing real is asked"

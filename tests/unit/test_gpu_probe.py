"""The ear finds the graphics card through CTranslate2 or the driver, never
torch, and CTranslate2 can find cuBLAS.

Alt+T took 5-9 s on an RTX 4060 laptop: local_voice._pick_device asked
torch.cuda, and the bundled torch is a CPU-only build, so the ear always ran on
the processor. CTranslate2 (what faster-whisper runs on) saw the card all
along; on the card with int8_float16 the same dictation is about 1.1 s warm,
once cuBLAS 12 (shipped with the llama.cpp runtime) is on PATH.

No test touches a GPU: CUDA_VISIBLE_DEVICES is -1 for every test, ctranslate2
is a stub module, and nvidia-smi is a stubbed subprocess call. Where a test
needs the probe to look past CUDA_VISIBLE_DEVICES, it says so and every
boundary it could reach is already stubbed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types

import pytest

from agent_friday.services.local_voice import WhisperASR

SMI_ONE_CARD = "6144, 8188, NVIDIA GeForce RTX 4060 Laptop GPU\n"


def _probe_module():
    from agent_friday.services import gpu_probe
    return gpu_probe


@pytest.fixture(autouse=True)
def no_real_gpu(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
    try:
        gp = _probe_module()
    except ImportError:          # the old code: no probe, torch decides
        gp = None
    if gp is not None:
        gp.reset()
    yield gp
    if gp is not None:
        gp.reset()


@pytest.fixture
def cpu_only_torch(monkeypatch):
    """The bundled torch: importable, and blind to the card."""
    t = types.ModuleType("torch")
    t.cuda = types.SimpleNamespace(is_available=lambda: False,
                                   mem_get_info=lambda *a: (0, 0),
                                   device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "torch", t)
    return t


def _stub_ct2(monkeypatch, devices):
    calls = []
    mod = types.ModuleType("ctranslate2")

    def count():
        calls.append(1)
        return devices
    mod.get_cuda_device_count = count
    monkeypatch.setitem(sys.modules, "ctranslate2", mod)
    return calls


def _stub_smi(monkeypatch, stdout=SMI_ONE_CARD, returncode=0):
    seen = []
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **k: "nvidia-smi" if name == "nvidia-smi"
                        else real_which(name, *a, **k))

    def run(cmd, **kw):
        seen.append((list(cmd), dict(kw)))
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    return seen


def _look_past_cuda_visible_devices(monkeypatch, gp, *, cublas=True):
    """Only with ctranslate2 and nvidia-smi already stubbed. ``cublas``: and
    CTranslate2 can find cuBLAS (the tests of a card without it say False)."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    if gp is not None:
        monkeypatch.setattr(gp, "_cuda_hidden", lambda: False)
        if cublas:
            monkeypatch.setattr(gp, "cublas_status", lambda env=None: {
                "ok": True, "how": "findable", "dir": None, "reason": ""}, raising=False)


def _no_arbiter(monkeypatch):
    import agent_friday.services.residency_arbiter as ra
    monkeypatch.setattr(ra, "ARBITER", None, raising=False)


# ── the device choice ───────────────────────────────────────────────────────

def test_a_cpu_only_torch_does_not_hide_a_card_ctranslate2_sees(
        monkeypatch, no_real_gpu, cpu_only_torch):
    _stub_ct2(monkeypatch, 1)
    _stub_smi(monkeypatch)
    _look_past_cuda_visible_devices(monkeypatch, no_real_gpu)
    _no_arbiter(monkeypatch)
    device, compute, why = WhisperASR("auto")._pick_device()
    assert (device, compute) == ("cuda", "int8_float16"), (
        "CTranslate2 reports a card with 6 GiB free; torch's CPU build must not "
        "send Alt+T to the processor: got %r" % ((device, compute, why),))
    assert why == ""


def test_nvidia_smi_answers_when_ctranslate2_is_not_importable(monkeypatch, no_real_gpu):
    gp = no_real_gpu
    monkeypatch.setitem(sys.modules, "ctranslate2", None)        # import raises
    seen = _stub_smi(monkeypatch)
    _look_past_cuda_visible_devices(monkeypatch, gp)
    p = gp.probe()
    assert p == {"cuda": True, "devices": 1, "source": "nvidia-smi", "free_mib": 6144,
                 "detail": ""}
    cmd, kw = seen[0]
    assert cmd[1:] == ["--query-gpu=memory.free,memory.total,name",
                       "--format=csv,noheader,nounits"]
    assert kw.get("shell") is False and 0 < kw.get("timeout") <= 10, "bounded, no shell"
    assert gp.pick_ct2_device() == ("cuda", "int8_float16", "")


def test_nvidia_smi_output_is_parsed_and_junk_is_skipped(no_real_gpu):
    gp = no_real_gpu
    rows = gp.parse_smi("6144, 8188, NVIDIA GeForce RTX 4060 Laptop GPU\n"
                        "[N/A], [N/A], Some, Odd, Name\n"
                        "\n"
                        "512, 4096, Card B\n")
    assert rows == [{"free_mib": 6144, "total_mib": 8188,
                     "name": "NVIDIA GeForce RTX 4060 Laptop GPU"},
                    {"free_mib": 512, "total_mib": 4096, "name": "Card B"}]
    assert gp.parse_smi("") == [] and gp.parse_smi("garbage") == []


def test_a_busy_card_and_an_unmeasurable_card_both_leave_the_ear_on_the_cpu(
        monkeypatch, no_real_gpu):
    gp = no_real_gpu
    _stub_ct2(monkeypatch, 1)
    _look_past_cuda_visible_devices(monkeypatch, gp)
    _stub_smi(monkeypatch, stdout="400, 8188, Card\n")
    device, compute, why = gp.pick_ct2_device()
    assert (device, compute) == ("cpu", "int8") and "free" in why
    gp.reset()
    _stub_smi(monkeypatch, stdout="", returncode=9)
    device, compute, why = gp.pick_ct2_device()
    assert (device, compute) == ("cpu", "int8") and "measured" in why


def test_cuda_visible_devices_minus_one_means_no_card_and_nothing_is_asked(
        monkeypatch, no_real_gpu):
    gp = no_real_gpu
    ct2 = _stub_ct2(monkeypatch, 1)
    seen = _stub_smi(monkeypatch)
    p = gp.probe()
    assert p["cuda"] is False and p["source"] == "CUDA_VISIBLE_DEVICES"
    assert ct2 == [] and seen == [], "nvidia-smi ignores the variable; it is not asked"


def test_the_device_count_is_cached_and_torch_is_never_imported(monkeypatch, no_real_gpu):
    gp = no_real_gpu
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    ct2 = _stub_ct2(monkeypatch, 1)
    _stub_smi(monkeypatch)
    _look_past_cuda_visible_devices(monkeypatch, gp)
    gp.probe()
    gp.probe()
    assert len(ct2) == 1, "the count is asked once per process"
    assert "torch" not in sys.modules


def test_the_ear_asks_for_cublas_before_a_cuda_load(monkeypatch, no_real_gpu):
    gp = no_real_gpu
    from agent_friday.services import feature_models as fm
    monkeypatch.setattr(fm, "whisper_model_dir", lambda size, first_root=None: size)
    asked, built = [], []
    monkeypatch.setattr(gp, "prepare_cublas_for_cuda", lambda env=None: asked.append(1) or (True, ""),
                        raising=False)
    monkeypatch.setattr(gp, "pick_ct2_device", lambda need_mib=0: ("cuda", "int8_float16", ""))
    _no_arbiter(monkeypatch)

    class WhisperModel:
        def __init__(self, src, **kw):
            built.append((src, kw.get("device"), kw.get("compute_type")))
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    WhisperASR("auto").load()
    assert asked == [1] and built == [("small", "cuda", "int8_float16")]


# ── cuBLAS on PATH, only when the DLL is really there ──────────────────────

@pytest.fixture
def windows_paths(monkeypatch, no_real_gpu, tmp_path):
    gp = no_real_gpu
    monkeypatch.setattr(gp, "_IS_WINDOWS", True)
    monkeypatch.setattr(gp, "_borrow_allowed", lambda: True)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)   # no ct2 package dir to search
    root = tmp_path / "runtime"
    (root / "llama.cpp-bonsai").mkdir(parents=True)
    monkeypatch.setattr(gp, "_runtime_root", lambda: root)
    return gp, root


def test_path_is_left_alone_when_the_runtime_has_no_cublas(windows_paths, tmp_path):
    gp, root = windows_paths
    env = {"PATH": str(tmp_path / "elsewhere")}
    out = gp.ensure_cublas_on_path(env)
    assert out["added"] is False and env["PATH"] == str(tmp_path / "elsewhere")


def test_the_llama_cpp_cublas_folder_is_prepended_once(windows_paths, tmp_path):
    gp, root = windows_paths
    d = root / "llama.cpp-bonsai"
    (d / gp.CUBLAS_DLL).write_bytes(b"MZ")
    env = {"PATH": str(tmp_path / "elsewhere")}
    out = gp.ensure_cublas_on_path(env)
    assert out["added"] is True and out["dir"] == str(d)
    assert env["PATH"].split(os.pathsep)[0] == str(d)
    again = gp.ensure_cublas_on_path(env)
    assert again["added"] is False, "already findable now"
    assert env["PATH"].split(os.pathsep).count(str(d)) == 1


def test_another_llama_cpp_runtime_with_cublas_is_used(windows_paths, tmp_path):
    gp, root = windows_paths
    other = root / "llama.cpp-ollama"
    other.mkdir()
    (other / gp.CUBLAS_DLL).write_bytes(b"MZ")
    env = {"PATH": ""}
    assert gp.ensure_cublas_on_path(env)["dir"] == str(other)


def test_a_cublas_already_on_path_is_not_shadowed(windows_paths, tmp_path):
    gp, root = windows_paths
    (root / "llama.cpp-bonsai" / gp.CUBLAS_DLL).write_bytes(b"MZ")
    toolkit = tmp_path / "cuda" / "bin"
    toolkit.mkdir(parents=True)
    (toolkit / gp.CUBLAS_DLL).write_bytes(b"MZ")
    env = {"PATH": str(toolkit)}
    assert gp.ensure_cublas_on_path(env)["added"] is False
    assert env["PATH"] == str(toolkit)


def test_the_setting_can_keep_path_untouched(windows_paths, monkeypatch, tmp_path):
    gp, root = windows_paths
    (root / "llama.cpp-bonsai" / gp.CUBLAS_DLL).write_bytes(b"MZ")
    monkeypatch.setattr(gp, "_borrow_allowed", lambda: False)
    env = {"PATH": ""}
    assert gp.ensure_cublas_on_path(env)["added"] is False and env["PATH"] == ""


def test_off_windows_nothing_changes(windows_paths, monkeypatch):
    gp, root = windows_paths
    (root / "llama.cpp-bonsai" / gp.CUBLAS_DLL).write_bytes(b"MZ")
    monkeypatch.setattr(gp, "_IS_WINDOWS", False)
    env = {"PATH": ""}
    assert gp.ensure_cublas_on_path(env)["added"] is False


def test_a_voice_worker_starts_with_cublas_on_its_path(windows_paths, monkeypatch):
    gp, root = windows_paths
    d = root / "llama.cpp-bonsai"
    (d / gp.CUBLAS_DLL).write_bytes(b"MZ")
    monkeypatch.setenv("PATH", "")
    from agent_friday.services import voice_workers as vw
    env = vw._worker_env("whisper-cuda")
    assert env["PATH"].split(os.pathsep)[0] == str(d)
    assert env["HF_HUB_OFFLINE"] == "1"
    assert os.environ["PATH"] == "", "Friday's own PATH is not touched for a worker"


def test_only_the_cuda_ear_worker_gets_the_borrowed_cublas(windows_paths, monkeypatch):
    """The PATH change is scoped to the one process that runs CTranslate2 on
    the card; a Kokoro worker (torch brings its own cuBLAS) is left alone."""
    gp, root = windows_paths
    (root / "llama.cpp-bonsai" / gp.CUBLAS_DLL).write_bytes(b"MZ")
    monkeypatch.setenv("PATH", "")
    from agent_friday.services import voice_workers as vw
    assert vw._worker_env("kokoro-cuda")["PATH"] == ""


def test_the_setting_is_declared_with_a_safe_default():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["local_voice_borrow_llama_cpp_cublas"] is True


# ── a card CTranslate2 cannot use is not chosen; a late failure recovers ───

def test_a_card_without_cublas_leaves_the_ear_on_the_cpu(windows_paths, monkeypatch, tmp_path):
    """The card is seen and free, but no cuBLAS 12 is on PATH, beside
    CTranslate2 or in a llama.cpp runtime: the load used to succeed on CUDA
    and every Alt+T then failed at its first transcription."""
    gp, root = windows_paths
    _stub_ct2(monkeypatch, 1)
    _stub_smi(monkeypatch)
    _look_past_cuda_visible_devices(monkeypatch, gp, cublas=False)
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    device, compute, why = gp.pick_ct2_device()
    assert (device, compute) == ("cpu", "int8"), (device, compute, why)
    assert "cuBLAS" in why and gp.CUBLAS_DLL in why, why


def test_a_borrowable_cublas_keeps_the_card_and_choosing_changes_nothing(
        windows_paths, monkeypatch, tmp_path):
    gp, root = windows_paths
    (root / "llama.cpp-bonsai" / gp.CUBLAS_DLL).write_bytes(b"MZ")
    _stub_ct2(monkeypatch, 1)
    _stub_smi(monkeypatch)
    _look_past_cuda_visible_devices(monkeypatch, gp, cublas=False)
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    assert gp.pick_ct2_device() == ("cuda", "int8_float16", "")
    assert os.environ["PATH"] == str(tmp_path / "elsewhere"), "choosing never edits PATH"
    ok, why = gp.prepare_cublas_for_cuda()
    assert ok and os.environ["PATH"].split(os.pathsep)[0] == str(root / "llama.cpp-bonsai")


def test_the_ear_loads_on_the_cpu_when_cublas_cannot_be_made_findable(monkeypatch, no_real_gpu):
    gp = no_real_gpu
    from agent_friday.services import feature_models as fm
    monkeypatch.setattr(fm, "whisper_model_dir", lambda size, first_root=None: size)
    monkeypatch.setattr(gp, "pick_ct2_device", lambda need_mib=0: ("cuda", "int8_float16", ""))
    monkeypatch.setattr(gp, "prepare_cublas_for_cuda",
                        lambda env=None: (False, "no llama.cpp runtime folder holds it"), raising=False)
    _no_arbiter(monkeypatch)
    built = []

    class WhisperModel:
        def __init__(self, src, **kw):
            built.append((src, kw.get("device"), kw.get("compute_type")))
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    asr = WhisperASR("auto")
    asr.load()
    assert built == [("base", "cpu", "int8")], built
    assert "llama.cpp" in str(asr._why_cpu)


def _two_device_whisper(monkeypatch, cuda_error):
    """faster_whisper whose CUDA model fails at transcription with
    ``cuda_error`` and whose CPU model answers."""
    built, ran = [], []

    class WhisperModel:
        def __init__(self, src, **kw):
            self.device = kw.get("device")
            built.append((src, self.device, kw.get("compute_type")))

        def transcribe(self, audio, **kw):
            ran.append(self.device)
            if self.device == "cuda":
                raise cuda_error
            return [types.SimpleNamespace(text=" hello there")], None
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    return built, ran


def _cuda_ear(monkeypatch, gp):
    from agent_friday.services import feature_models as fm
    monkeypatch.setattr(fm, "whisper_model_dir", lambda size, first_root=None: size)
    monkeypatch.setattr(gp, "pick_ct2_device", lambda need_mib=0: ("cuda", "int8_float16", ""))
    monkeypatch.setattr(gp, "prepare_cublas_for_cuda", lambda env=None: (True, ""), raising=False)
    monkeypatch.setattr(gp, "ensure_cublas_on_path", lambda *a, **k: {"added": False})
    _no_arbiter(monkeypatch)


def test_a_cublas_error_at_transcription_reloads_on_the_cpu_once(monkeypatch, no_real_gpu, caplog):
    import logging
    _cuda_ear(monkeypatch, no_real_gpu)
    built, ran = _two_device_whisper(monkeypatch, RuntimeError(
        "Library cublas64_12.dll is not found or cannot be loaded"))
    asr = WhisperASR("auto")
    pcm = b"\x00\x01" * 1600
    with caplog.at_level(logging.WARNING):
        assert asr.transcribe(pcm) == "hello there", "this Alt+T is answered, from the CPU"
    assert built == [("small", "cuda", "int8_float16"), ("base", "cpu", "int8")], built
    assert any("cublas64_12" in r.getMessage() and r.name.startswith("friday")
               for r in caplog.records), "the reason is in friday.log"
    assert asr.transcribe(pcm) == "hello there"
    assert ran == ["cuda", "cpu", "cpu"] and len(built) == 2, "reloaded once, then stays on the CPU"
    assert "CUDA" in str(asr._why_cpu)


def test_an_error_that_is_not_the_cards_libraries_is_raised(monkeypatch, no_real_gpu):
    _cuda_ear(monkeypatch, no_real_gpu)
    built, _ran = _two_device_whisper(monkeypatch, ValueError("audio is empty"))
    asr = WhisperASR("auto")
    with pytest.raises(ValueError):
        asr.transcribe(b"\x00\x01" * 1600)
    assert built == [("small", "cuda", "int8_float16")], "no reload for a non-CUDA error"


# ── which nvidia-smi row is CTranslate2's card ─────────────────────────────

def test_free_memory_is_read_from_the_card_cuda_visible_devices_names(monkeypatch, no_real_gpu):
    gp = no_real_gpu
    _stub_ct2(monkeypatch, 1)
    _stub_smi(monkeypatch, stdout="300, 4096, Card A\n6144, 8188, Card B\n")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert gp.free_vram_mib(refresh=True) == 6144, "device 0 is physical card 1 here"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    assert gp.free_vram_mib(refresh=True) == 300
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-8a1b2c3d-0000-0000-0000-000000000000")
    assert gp.free_vram_mib(refresh=True) is None, "an id cannot be matched to a row; not guessed"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3")
    assert gp.free_vram_mib(refresh=True) is None, "no such row"

"""Reading the VRAM must not create a CUDA context in the server.

torch.cuda.mem_get_info (and current_device, get_device_name) initialise CUDA
in the calling process, which costs the server about 1.5 GB of private memory
for a number nvidia-smi can supply. A cached reading therefore comes from
nvidia-smi unless CUDA is ALREADY initialised in this process; only an
admission (``fresh=True``) may ask torch.

torch is a stub in sys.modules and nvidia-smi a stubbed subprocess, so the
test needs neither a GPU nor torch.
"""
from __future__ import annotations

import subprocess
import sys
import types

import pytest

from agent_friday.services import nemo_voice as nv


@pytest.fixture(autouse=True)
def _reset():
    nv.reset_gpu_status_cache()
    yield
    nv.reset_gpu_status_cache()


def _install(monkeypatch, *, initialized: bool, smi_line="Fake RTX 4070, 12282, 8192"):
    touched = []

    class _Cuda:
        @staticmethod
        def is_initialized():
            return initialized

        @staticmethod
        def is_available():
            touched.append("is_available")
            return True

        @staticmethod
        def current_device():
            touched.append("current_device")
            return 0

        @staticmethod
        def get_device_name(_i):
            touched.append("get_device_name")
            return "Fake RTX 4070"

        @staticmethod
        def mem_get_info(_i):
            touched.append("mem_get_info")
            return int(11.0 * 1e9), int(12.0 * 1e9)

    fake = types.ModuleType("torch")
    fake.cuda = _Cuda
    fake.version = types.SimpleNamespace(cuda="12.4")
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.setattr(nv, "_module_installed", lambda name: name == "torch")
    monkeypatch.setattr(nv, "_local_gpu_voice_selected", lambda: True)

    def _run(cmd, *a, **k):
        q = next((c for c in cmd if c.startswith("--query-gpu=")), "")
        out = smi_line if "name" in q else smi_line.rsplit(",", 1)[-1].strip()

        class _R:
            stdout = out + "\n" if out else ""
            returncode = 0
        return _R()

    monkeypatch.setattr(subprocess, "run", _run)
    return touched


def test_a_cached_reading_does_not_touch_cuda_when_it_is_not_initialised(monkeypatch):
    touched = _install(monkeypatch, initialized=False)
    g = nv.gpu_status()
    assert touched == [], "the probe initialised CUDA in the server: %s" % touched
    assert g["cuda"] is True
    assert g["device"] == "Fake RTX 4070"
    assert g["vram_gb"] == 12.0
    assert g["vram_free_gb"] == 8.0
    assert g["source"] == "nvidia-smi"
    assert g["sufficient"] is (8.0 >= nv.MIN_VRAM_GB)
    # The admission fields keep their meaning: nvidia-smi's figure IS the
    # conservative one, and a single authority cannot disagree with itself.
    assert g["vram_free_real_gb"] == 8.0
    assert g["sufficient_real"] is g["sufficient"]
    assert g["vram_measurement_disputed"] is False
    for k in ("detail", "contended", "contention_detail", "vram_dispute_gb",
              "sufficient_reachable"):
        assert k in g


def test_low_free_vram_is_still_contended(monkeypatch):
    touched = _install(monkeypatch, initialized=False, smi_line="Fake RTX 4070, 12282, 1024")
    g = nv.gpu_status()
    assert touched == []
    assert g["vram_free_gb"] == 1.0
    assert g["sufficient"] is False and g["contended"] is True


def test_an_initialised_context_is_read_through_torch(monkeypatch):
    touched = _install(monkeypatch, initialized=True)
    g = nv.gpu_status()
    assert "mem_get_info" in touched
    assert g["source"] == "torch"


def test_an_admission_may_ask_torch(monkeypatch):
    touched = _install(monkeypatch, initialized=False)
    g = nv.gpu_status(fresh=True)
    assert "mem_get_info" in touched
    assert g["source"] == "torch"


def test_a_cpu_only_torch_is_not_imported_or_initialised(monkeypatch):
    touched = _install(monkeypatch, initialized=False)
    sys.modules["torch"].version = types.SimpleNamespace(cuda=None)
    from agent_friday.routing import ollama_manager as om

    class _Mgr:
        @staticmethod
        def detect_hardware():
            return {"gpu": "NVIDIA Fake RTX 4070", "vram_gb": 12.0}

    monkeypatch.setattr(om, "get_manager", lambda: _Mgr())
    g = nv.gpu_status()
    assert touched == []
    assert g["cuda"] is False
    assert "CPU-only" in g["detail"]

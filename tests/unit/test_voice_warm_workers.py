"""The voice workers stop paying for libraries they do not use, and a call can
find the kokoro worker already imported.

Measured on live: the whisper worker spent 12-26 s importing (faster_whisper
-> ctranslate2 -> its converters -> transformers and torch), and the kokoro
worker 16-30 s importing transformers and kokoro before a 2 s model load.
"""
import importlib.util
import subprocess
import sys

import pytest

from agent_friday.services import voice_workers as vw


# ── the whisper worker imports no converters and no torch ───────────────────

@pytest.mark.skipif(importlib.util.find_spec("faster_whisper") is None,
                    reason="faster-whisper not installed here")
def test_the_whisper_import_loads_neither_torch_nor_transformers():
    code = (
        "import sys\n"
        "from agent_friday.services.light_imports import faster_whisper_model\n"
        "M = faster_whisper_model()\n"
        "bad = [m for m in ('torch', 'transformers') if sys.modules.get(m) is not None]\n"
        "print('LOADED', bad, 'torch_is_hidden', sys.modules.get('torch', 0) is None)\n")
    import os
    import agent_friday
    src = os.path.dirname(os.path.dirname(agent_friday.__file__))
    env = dict(os.environ, PYTHONPATH=src + os.pathsep + os.environ.get("PYTHONPATH", ""),
               HF_HUB_OFFLINE="1", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=100, env=env)
    out = r.stdout
    assert "LOADED []" in out, (r.stdout, r.stderr[-800:])
    assert "torch_is_hidden False" in out, "torch must not stay hidden after the import"


def test_a_torch_that_is_already_loaded_is_left_alone(monkeypatch):
    import types
    fake = types.ModuleType("torch")
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=object))
    from agent_friday.services import light_imports
    light_imports.faster_whisper_model()
    assert sys.modules["torch"] is fake


def test_the_worker_measures_without_torch_when_torch_is_not_loaded(monkeypatch):
    from agent_friday.voice import worker
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    seen = {}

    class R:
        stdout = "11800\n"

    def run(cmd, **k):
        seen["cmd"] = cmd
        return R()
    monkeypatch.setattr(subprocess, "run", run)
    assert worker._cuda_free_mib() == 11800
    assert seen["cmd"][0] == "nvidia-smi" and "torch" not in sys.modules


def test_the_worker_accepts_the_preimport_flag_and_names_what_it_imports():
    from agent_friday.voice import worker
    import inspect
    assert "--preimport" in inspect.getsource(worker.main)
    worker.preimport("fake")                     # imports nothing, raises nothing


# ── warm workers ────────────────────────────────────────────────────────────

class _Proc:
    def __init__(self):
        self.killed = False
        self._rc = None

    def poll(self):
        return self._rc

    def kill(self):
        self.killed, self._rc = True, -9


@pytest.fixture()
def pool(monkeypatch):
    spawned = []

    def popen(engine, args, preimport=False):
        p = _Proc()
        spawned.append((engine, dict(args), preimport, p))
        return p
    monkeypatch.setattr(vw, "_popen_worker", popen)
    monkeypatch.setattr(vw, "_WARM", {})
    monkeypatch.setattr(vw, "_free_ram_mb", lambda: 8000)
    monkeypatch.setattr(vw, "WARM_TTL_S", 600.0)
    yield spawned
    vw.release_warm("test")


def test_a_warm_worker_is_started_with_the_import_flag(pool):
    assert vw.prewarm("kokoro-cuda", {"voice": "af_heart"}) is True
    engine, args, preimport, _p = pool[0]
    assert engine == "kokoro-cuda" and args == {"voice": "af_heart"} and preimport is True
    assert "--preimport" in vw._worker_cmd(engine, args, True)
    assert "--preimport" not in vw._worker_cmd(engine, args)


def test_a_call_takes_the_warm_worker_instead_of_spawning(pool, monkeypatch):
    vw.prewarm("kokoro-cuda", {"voice": "af_heart"})
    w = vw.VoiceWorker("kokoro-cuda", "mouth", args={"voice": "af_heart"}, lease=False)
    proc = w._default_spawn()
    assert proc is pool[0][3] and len(pool) == 1, "no second process was spawned"
    assert vw.take_warm("kokoro-cuda", {"voice": "af_heart"}) is None, "it is used once"


def test_a_warm_worker_for_another_voice_is_not_used(pool):
    vw.prewarm("kokoro-cuda", {"voice": "af_heart"})
    assert vw.take_warm("kokoro-cuda", {"voice": "bf_emma"}) is None
    assert pool[0][3].killed, "the wrong one is ended, not kept"


def test_a_dead_warm_worker_is_not_handed_out(pool):
    vw.prewarm("kokoro-cuda", {"voice": "af_heart"})
    pool[0][3].kill()
    assert vw.take_warm("kokoro-cuda", {"voice": "af_heart"}) is None


def test_short_free_ram_starts_no_warm_worker(pool, monkeypatch):
    monkeypatch.setattr(vw, "_free_ram_mb", lambda: 1200)
    assert vw.prewarm("kokoro-cuda", {"voice": "af_heart"}) is False
    assert pool == []


def test_only_engines_whose_import_dominates_are_kept_warm(pool):
    assert vw.prewarm("whisper-cuda", {"model": "small"}) is False and pool == []


def test_asking_again_keeps_one_worker_and_refreshes_its_ttl(pool):
    vw.prewarm("kokoro-cuda", {"voice": "af_heart"})
    assert vw.prewarm("kokoro-cuda", {"voice": "af_heart"}) is True
    assert len(pool) == 1


def test_an_idle_warm_worker_ends_at_its_ttl(pool, monkeypatch):
    import time
    monkeypatch.setattr(vw, "WARM_TTL_S", 0.2)
    vw.prewarm("kokoro-cuda", {"voice": "af_heart"})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pool[0][3].killed:
        time.sleep(0.05)
    assert pool[0][3].killed and vw.take_warm("kokoro-cuda", {"voice": "af_heart"}) is None


def test_release_all_ends_the_warm_workers(pool):
    vw.prewarm("kokoro-cuda", {"voice": "af_heart"})
    vw.release_all("test")
    assert pool[0][3].killed


def test_a_warm_start_touches_neither_the_gpu_nor_the_arbiter(pool, monkeypatch):
    monkeypatch.setattr(vw, "acquire_lease", lambda *a, **k: pytest.fail("a lease was taken"))
    monkeypatch.setattr(vw, "admit_gpu", lambda *a, **k: pytest.fail("the card was asked"))
    assert vw.prewarm("kokoro-cuda", {"voice": "af_heart"}) is True

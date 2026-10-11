"""An in-process Kokoro load never reaches the Hugging Face hub.

kokoro's own loaders call hf_hub_download for the config, the weights and the
voice, which asks the hub for the latest revision on every load. The server
process is not run offline (first-run model fetches go through the voice
installer), so KokoroTTS passes the cached files by path and refuses plainly
when one is missing. The hub functions here raise if they are ever called.
"""
import sys
import types

import pytest

from agent_friday.services import kokoro_voice as kv


class _Hub(types.SimpleNamespace):
    pass


@pytest.fixture()
def world(tmp_path, monkeypatch):
    files = {}
    calls = {"hub": 0, "model": None, "pipeline": None, "voices": []}

    def no_hub(*a, **k):
        calls["hub"] += 1
        raise AssertionError("the hub was asked during a load")

    def cached(repo, filename, **k):
        assert repo == "hexgrad/Kokoro-82M"
        return files.get(filename)

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = no_hub
    hub.snapshot_download = no_hub
    hub.try_to_load_from_cache = cached
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    class KModel:
        def __init__(self, config=None, model=None, **kw):
            if config is None or model is None:
                no_hub()                      # kokoro downloads when given no paths
            calls["model"] = {"config": config, "model": model}

        def to(self, device):
            calls["device"] = device
            return self

        def eval(self):
            return self

    class KPipeline:
        def __init__(self, lang_code, model=True, trf=False, device=None):
            if model is True:
                no_hub()                      # kokoro builds (downloads) its own model
            calls["pipeline"] = {"lang": lang_code, "model": model}

        def __call__(self, text, voice=None, speed=1.0):
            calls["voices"].append(voice)
            if not str(voice).endswith(".pt"):
                no_hub()                      # a bare voice name is fetched by name
            import numpy as np
            yield "g", "p", np.zeros(2400, dtype="float32")

    kokoro = types.ModuleType("kokoro")
    kokoro.KModel, kokoro.KPipeline = KModel, KPipeline
    monkeypatch.setitem(sys.modules, "kokoro", kokoro)

    for name in ("config.json", "kokoro-v1_0.pth", "voices/af_heart.pt"):
        p = tmp_path / name.replace("/", "_")
        p.write_bytes(b"x")
        files[name] = str(p)
    monkeypatch.setattr(kv, "ensure_espeak_fallback", lambda: {"wired": True})
    monkeypatch.setattr(kv, "attach_espeak_fallback", lambda p: {"fallback": True})
    monkeypatch.setattr(kv.KokoroTTS, "_resolve_device", lambda self: "cpu")
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n, *a, **k: object() if n == "en_core_web_sm" else real(n, *a, **k))
    return types.SimpleNamespace(files=files, calls=calls)


def test_a_load_passes_cached_paths_and_never_asks_the_hub(world):
    t = kv.KokoroTTS("af_heart", allow_cpu=True)
    t.load()
    c = world.calls
    assert c["hub"] == 0
    assert c["model"] == {"config": world.files["config.json"],
                          "model": world.files["kokoro-v1_0.pth"]}
    assert c["pipeline"]["model"] is not True and c["device"] == "cpu"
    assert t._voice_path == world.files["voices/af_heart.pt"]


def test_synthesis_names_the_voice_by_its_cached_path(world):
    t = kv.KokoroTTS("af_heart", allow_cpu=True)
    t.synthesize("Hello there.")
    assert world.calls["voices"] and all(v.endswith(".pt") for v in world.calls["voices"])
    assert world.calls["hub"] == 0


@pytest.mark.parametrize("missing,word", [("config.json", "config"),
                                          ("kokoro-v1_0.pth", "model"),
                                          ("voices/af_heart.pt", "voice af_heart")])
def test_a_missing_file_is_refused_plainly_without_a_fetch(world, missing, word):
    world.files.pop(missing)
    t = kv.KokoroTTS("af_heart", allow_cpu=True)
    with pytest.raises(kv.KokoroUnavailable) as e:
        t.load()
    assert e.value.code == "local_voice_kokoro_missing"
    assert word in str(e.value) and "never downloads" in str(e.value)
    assert "Settings > Voice" in str(e.value)
    assert world.calls["hub"] == 0 and world.calls["pipeline"] is None

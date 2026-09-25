"""A model's own serving record beats the arbiter's global defaults.

Measured on the 12 GB card, Bonsai2 holds 131,072 tokens only with a q4_0 KV
cache and its vision projector left out. The arbiter appended the global KV
type after the model's declared flags (llama.cpp takes the last one), and
always loaded a projector when it could find one, so neither measurement
could be declared.
"""
from __future__ import annotations

import subprocess

import pytest

from agent_friday.services import model_store
from agent_friday.services import residency_arbiter as ra


def _capture(monkeypatch, tmp_path, record):
    captured = {"cmds": []}

    class DeadProc:
        returncode = 1

        def __init__(self, cmd, **kw):
            captured["cmds"].append(list(cmd))

        def poll(self):
            return 1

        def terminate(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", DeadProc)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(model_store, "get", lambda mid: dict(record))
    monkeypatch.setattr(ra.LlamaServerBackend, "_kv_cache_type", lambda self: "q8_0")
    return captured


def _spawn(tmp_path, **kw):
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    with pytest.raises(ra.TransitionError):
        be._spawn_once(tmp_path / "llama-server.exe", "bonsai2:27b", 131072,
                       gguf_path=tmp_path / "base.gguf", port=8199, **kw)
    return be


def _flag(cmd, name):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == name]


def test_a_declared_kv_type_is_not_overridden_by_the_global_one(monkeypatch, tmp_path):
    cap = _capture(monkeypatch, tmp_path, {"serve_args": [
        "--cache-type-k", "q4_0", "--cache-type-v", "q4_0"]})
    _spawn(tmp_path)
    cmd = cap["cmds"][0]
    assert _flag(cmd, "--cache-type-k") == ["q4_0"]
    assert _flag(cmd, "--cache-type-v") == ["q4_0"]


def test_without_a_declaration_the_global_kv_type_still_applies(monkeypatch, tmp_path):
    cap = _capture(monkeypatch, tmp_path, {})
    _spawn(tmp_path)
    assert _flag(cap["cmds"][0], "--cache-type-k") == ["q8_0"]


def test_vision_on_demand_leaves_the_projector_out(monkeypatch, tmp_path):
    cap = _capture(monkeypatch, tmp_path, {"vision": "on_demand"})
    _spawn(tmp_path, mmproj_path=tmp_path / "mm.gguf")
    assert "--mmproj" not in cap["cmds"][0]


def test_without_vision_on_demand_the_projector_loads(monkeypatch, tmp_path):
    cap = _capture(monkeypatch, tmp_path, {})
    _spawn(tmp_path, mmproj_path=tmp_path / "mm.gguf")
    assert _flag(cap["cmds"][0], "--mmproj") == [str(tmp_path / "mm.gguf")]


def test_an_image_reloads_the_seat_with_its_projector(monkeypatch, tmp_path):
    monkeypatch.setattr(model_store, "get", lambda mid: {"vision": "on_demand"})
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    calls = []
    monkeypatch.setattr(be, "evict", lambda mid: calls.append(("evict", mid)))
    monkeypatch.setattr(be, "_load_locked",
                        lambda mid, ctx, **kw: calls.append(("load", mid, ctx, kw)))
    be._last_load["bonsai2:27b"] = dict(
        num_ctx=131072, gguf_path=tmp_path / "base.gguf", port=8090,
        n_cpu_moe=None, timeout=300, lora_path=None, mmproj_path=tmp_path / "mm.gguf")
    assert be.ensure_vision("bonsai2:27b") is True
    assert calls[0] == ("evict", "bonsai2:27b")
    assert calls[1][2] == 131072 and calls[1][3]["port"] == 8090
    # Once loaded, a second image does not reload it again.
    assert be.ensure_vision("bonsai2:27b") is True
    assert len(calls) == 2


def test_the_reload_spawns_with_the_projector(monkeypatch, tmp_path):
    cap = _capture(monkeypatch, tmp_path, {"vision": "on_demand"})
    be = _spawn(tmp_path, mmproj_path=tmp_path / "mm.gguf")
    be._vision_wanted.add("bonsai2:27b")
    with pytest.raises(ra.TransitionError):
        be._spawn_once(tmp_path / "llama-server.exe", "bonsai2:27b", 131072,
                       gguf_path=tmp_path / "base.gguf", port=8199,
                       mmproj_path=tmp_path / "mm.gguf")
    assert "--mmproj" not in cap["cmds"][0]
    assert _flag(cap["cmds"][-1], "--mmproj") == [str(tmp_path / "mm.gguf")]


def test_a_model_without_vision_on_demand_is_never_reloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(model_store, "get", lambda mid: {})
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    monkeypatch.setattr(be, "evict", lambda mid: pytest.fail("evicted"))
    assert be.ensure_vision("qwen3:4b") is True


def test_a_seat_it_did_not_load_is_not_reloaded(monkeypatch, tmp_path):
    """An adopted seat has no recorded load parameters; guessing them could
    bring it back at the wrong context, so it reports that it cannot."""
    monkeypatch.setattr(model_store, "get", lambda mid: {"vision": "on_demand"})
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    monkeypatch.setattr(be, "evict", lambda mid: pytest.fail("evicted"))
    assert be.ensure_vision("bonsai2:27b") is False
    assert "bonsai2:27b" not in be._vision_wanted


def test_describing_an_image_asks_for_the_projector_first(monkeypatch):
    from agent_friday.services import local_vision as lv
    asked = []
    monkeypatch.setattr(lv, "capability", lambda s=None: {
        "ok": True, "model": "bonsai2:27b", "endpoint": "http://127.0.0.1:1"})
    monkeypatch.setattr(ra, "ensure_vision", lambda mid: asked.append(mid) or False)
    out = lv.describe("aGk=")
    assert asked == ["bonsai2:27b"]
    assert out["ok"] is False and "projector" in out["reason"]

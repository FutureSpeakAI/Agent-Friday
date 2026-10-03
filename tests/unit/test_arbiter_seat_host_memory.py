"""A seat's host-side caches are bounded by the Arbiter, not by llama.cpp's defaults.

llama-server keeps a host-RAM prompt cache (`--cache-ram`, default 8192 MiB)
and, for hybrid and sliding-window models, per-slot context checkpoints
(`--ctx-checkpoints`, default 32). On Bonsai 2 27B each checkpoint is the
whole recurrent state, about 150 MiB, and a saved conversation of 25k tokens
is about 1 GiB. Left at the defaults, the seat's private commit climbs by up
to 8 GiB with use. Every seat command carries explicit bounds; a model record
may still declare its own.
"""
from __future__ import annotations

import subprocess

import pytest

from agent_friday.services import model_store
from agent_friday.services import residency_arbiter as ra


def _spawn_cmd(monkeypatch, tmp_path, record):
    cmds = []

    class DeadProc:
        returncode = 1

        def __init__(self, cmd, **kw):
            cmds.append(list(cmd))

        def poll(self):
            return 1

        def terminate(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", DeadProc)
    monkeypatch.setattr(ra, "_host_ram_total_mib", lambda: 16384)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(model_store, "get", lambda mid: dict(record))
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    with pytest.raises(ra.TransitionError):
        be._spawn_once(tmp_path / "llama-server.exe", "bonsai2:27b", 131072,
                       gguf_path=tmp_path / "base.gguf", port=8199)
    return cmds[0]


def _flag(cmd, name):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == name]


def test_the_prompt_cache_is_bounded(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {})
    assert _flag(cmd, "--cache-ram") == ["3072"]


def test_context_checkpoints_are_bounded(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {})
    assert _flag(cmd, "--ctx-checkpoints") == ["4"]


def test_a_declared_bound_replaces_the_default(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {"serve_args": [
        "--cache-ram", "0", "--ctx-checkpoints", "2"]})
    assert _flag(cmd, "--cache-ram") == ["0"]
    assert _flag(cmd, "--ctx-checkpoints") == ["2"]

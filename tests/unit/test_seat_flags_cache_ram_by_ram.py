"""`--cache-ram` follows the host's RAM: 6144 MiB with 24 GB or more, else 3072.

With one slot on the brain, every background job evicts the chat's state and
the host prompt cache is what hands it back without a re-read. At 3072 MiB a
20k-token chat entry rarely survived a 25-30k-token morning prompt, so most
returns to the chat were full reads. A 32 GB machine can afford 6144; a 16 GB
machine keeps 3072 and accepts the re-read.
"""
from __future__ import annotations

import subprocess

import pytest

from agent_friday.services import model_store
from agent_friday.services import residency_arbiter as ra


def _spawn_cmd(monkeypatch, tmp_path, record, host_mib):
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
    monkeypatch.setattr(ra, "_host_ram_total_mib", lambda: host_mib)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(model_store, "get", lambda mid: dict(record))
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    with pytest.raises(ra.TransitionError):
        be._spawn_once(tmp_path / "llama-server.exe", "bonsai2:27b", 131072,
                       gguf_path=tmp_path / "base.gguf", port=8199)
    return cmds[0]


def _flag(cmd, name):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == name]


def test_a_32gb_machine_gets_the_larger_cache(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {}, 32620)
    assert _flag(cmd, "--cache-ram") == ["6144"]


def test_a_16gb_machine_keeps_the_small_cache(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {}, 16384)
    assert _flag(cmd, "--cache-ram") == ["3072"]


def test_24gb_is_the_line(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {}, 24576)
    assert _flag(cmd, "--cache-ram") == ["6144"]


def test_unknown_ram_is_treated_as_small(monkeypatch):
    monkeypatch.setattr(ra, "_host_ram_total_mib", lambda: None)
    assert ra.LlamaServerBackend.prompt_cache_ram_mib() == 3072


def test_a_declared_value_still_wins(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {"serve_args": ["--cache-ram", "1024"]}, 32620)
    assert _flag(cmd, "--cache-ram") == ["1024"]


def test_checkpoints_are_unchanged_by_ram(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, {}, 32620)
    assert _flag(cmd, "--ctx-checkpoints") == ["4"]

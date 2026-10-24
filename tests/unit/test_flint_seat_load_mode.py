"""Every Flint seat is spawned with its weights loaded, never memory-mapped.

Spec core_models_flint_laya_bonsai2, CT-E29. With the default memory-mapped
mode a Flint seat beside live Friday grew to 11.3 GB of host memory and left
0.7 GB available; with ``--load-mode none`` it held about 5.9 GB. The rule is
applied last at spawn, so no record, override or default can undo it, and it
touches no other model's seat.
"""
from __future__ import annotations

import subprocess

import pytest

from agent_friday.services import flint_seat as fs
from agent_friday.services import model_shortlist as sl
from agent_friday.services import model_store
from agent_friday.services import residency_arbiter as ra


def _flags(cmd, name):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == name]


# ── the rule itself ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("extra", [
    [],
    ["--load-mode", "mmap"],
    ["-lm", "mmap+mlock"],
    ["--load-mode=mlock"],
    ["--mmap", "--mlock"],
    ["--no-mmap"],
    ["--load-mode", "mmap", "--load-mode", "auto"],
    ["-dio", "--direct-io"],
    ["--no-direct-io", "--load-mode"],
])
def test_any_loading_flag_is_replaced_by_load_mode_none(extra):
    cmd = ["llama-server", "-m", "w.gguf", "-c", "32768"] + extra + ["--jinja"]
    out = fs.enforce(cmd)
    assert out[-2:] == ["--load-mode", "none"]
    assert _flags(out, "--load-mode") == ["none"]
    for gone in ("-lm", "--mmap", "--no-mmap", "--mlock", "--no-mlock", "-dio", "--direct-io", "--no-direct-io"):
        assert gone not in out
    assert not any(str(a).startswith("--load-mode=") for a in out)
    # Everything else survives, in order.
    assert [a for a in out[:-2]] == ["llama-server", "-m", "w.gguf", "-c", "32768", "--jinja"]


def test_a_value_less_loading_flag_never_eats_the_next_flag():
    out = fs.enforce(["llama-server", "--load-mode", "--n-cpu-moe", "20", "-lm", "--jinja"])
    assert out == ["llama-server", "--n-cpu-moe", "20", "--jinja", "--load-mode", "none"]


def test_the_seat_environment_carries_no_loading_variable():
    env = {"PATH": "x", "LLAMA_ARG_MMAP": "1", "llama_arg_load_mode": "mmap",
           "LLAMA_ARG_MLOCK": "1", "LLAMA_ARG_DIO": "1", "LLAMA_ARG_CTX_SIZE": "4096"}
    assert fs.spawn_env(env) == {"PATH": "x", "LLAMA_ARG_CTX_SIZE": "4096"}


def test_flint_is_recognised_three_ways_and_bonsai_is_not():
    assert fs.is_flint_seat("x", binary="C:/Users/u/.friday/runtime/LLAMA.CPP-FLINT-b11803/llama-server.exe")
    assert fs.is_flint_seat("flint:40b")                                    # shortlist runtime
    assert fs.is_flint_seat("hf:someone-flint", record={"architecture": "flint"})
    assert fs.is_flint_seat("x", binary=r"C:\Users\u\.friday\runtime\llama.cpp-flint\llama-server.exe")
    assert not fs.is_flint_seat("bonsai2:27b", record={"architecture": "qwen35"},
                                binary=r"C:\Users\u\.friday\runtime\llama.cpp-bonsai\llama-server.exe")


def test_the_shortlist_declares_the_measured_seat():
    sl.reload_for_tests()
    args = sl.get("flint:40b")["serve"]["serve_args"]
    assert _flags(args, "--load-mode") == ["none"]
    assert _flags(args, "--cache-ram") == ["0"]


# ── at spawn ─────────────────────────────────────────────────────────────────

def _capture(monkeypatch, tmp_path, record):
    captured = {"cmds": []}

    class DeadProc:
        returncode = 1

        def __init__(self, cmd, **kw):
            captured["cmds"].append(list(cmd))
            captured.setdefault("envs", []).append(kw.get("env"))

        def poll(self):
            return 1

        def terminate(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", DeadProc)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(model_store, "get", lambda mid: dict(record))
    monkeypatch.setattr(ra.LlamaServerBackend, "_kv_cache_type", lambda self: "q8_0")
    return captured


def _spawn(tmp_path, model_id, binary):
    be = ra.LlamaServerBackend(binary=binary)
    with pytest.raises(ra.TransitionError):
        be._spawn_once(binary, model_id, 32768, gguf_path=tmp_path / "w.gguf", port=8199,
                       n_cpu_moe=20)


def test_a_flint_seat_spawns_with_load_mode_none_even_if_its_record_says_mmap(monkeypatch, tmp_path):
    cap = _capture(monkeypatch, tmp_path, {
        "architecture": "flint",
        "serve_args": ["--load-mode", "mmap", "--mlock", "--n-cpu-moe", "20"]})
    _spawn(tmp_path, "flint:40b", tmp_path / "llama.cpp-flint" / "llama-server.exe")
    cmd = cap["cmds"][0]
    assert cmd[-2:] == ["--load-mode", "none"]
    assert _flags(cmd, "--load-mode") == ["none"] and "--mlock" not in cmd


def test_a_flint_seat_spawns_without_loading_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("LLAMA_ARG_MMAP", "1")
    cap = _capture(monkeypatch, tmp_path, {"architecture": "flint"})
    _spawn(tmp_path, "flint:40b", tmp_path / "llama.cpp-flint" / "llama-server.exe")
    env = cap["envs"][0]
    assert env is not None and "LLAMA_ARG_MMAP" not in env


def test_without_the_rule_module_a_flint_seat_is_refused_and_others_still_spawn(monkeypatch, tmp_path):
    import sys
    import agent_friday.services as pkg
    monkeypatch.setitem(sys.modules, "agent_friday.services.flint_seat", None)   # import fails
    monkeypatch.delattr(pkg, "flint_seat", raising=False)
    cap = _capture(monkeypatch, tmp_path, {"architecture": "flint"})
    with pytest.raises(ra.TransitionError) as e:
        ra.LlamaServerBackend(binary=tmp_path / "x.exe")._spawn_once(
            tmp_path / "llama.cpp-flint" / "llama-server.exe", "flint:40b", 32768,
            gguf_path=tmp_path / "w.gguf", port=8199)
    assert "Flint seat rule" in str(e.value) and cap["cmds"] == []
    cap2 = _capture(monkeypatch, tmp_path, {"architecture": "qwen35"})
    _spawn(tmp_path, "bonsai2:27b", tmp_path / "llama.cpp-bonsai" / "llama-server.exe")
    assert cap2["cmds"], "another model's seat is still spawned"


def test_another_models_seat_is_untouched(monkeypatch, tmp_path):
    cap = _capture(monkeypatch, tmp_path, {"architecture": "qwen35", "serve_args": ["--mlock"]})
    _spawn(tmp_path, "bonsai2:27b", tmp_path / "llama.cpp-bonsai" / "llama-server.exe")
    cmd = cap["cmds"][0]
    assert "--load-mode" not in cmd and "--mlock" in cmd

"""A fresh brain seat receives its canonical head as a one-token request.

Every llama-server restart is a cold read for the hybrid brain. The Arbiter
fires seat-ready hooks the moment a seat answers /health (spawned or
adopted), and `services.seat_warm` uses the first one to send the stable head
of the system prompt, above the volatile marker, as a one-token completion,
so the first real turn reads only itself.
"""
from __future__ import annotations

import contextlib
import io
import subprocess

from agent_friday.services import model_store
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import seat_warm


class _LiveProc:
    returncode = None

    def __init__(self, cmd, **kw):
        self.cmd = list(cmd)

    def poll(self):
        return None

    def terminate(self):
        pass


@contextlib.contextmanager
def _ok(*a, **k):
    r = io.BytesIO(b'{"status":"ok"}')
    r.status = 200
    yield r


def _spawn_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", _LiveProc)
    monkeypatch.setattr(ra.urllib.request, "urlopen", _ok)
    monkeypatch.setattr(ra, "_host_ram_total_mib", lambda: 16384)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(model_store, "get", lambda mid: {})
    monkeypatch.setattr(ra, "_publish_endpoints", lambda *a, **k: None)
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    be._spawn_once(tmp_path / "llama-server.exe", "bonsai2:27b", 131072,
                   gguf_path=tmp_path / "base.gguf", port=8199)
    ra.join_seat_ready_hooks(5.0)


def test_the_arbiter_fires_seat_ready_hooks_when_health_answers(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ra, "SEAT_READY_HOOKS", [lambda m, p: seen.append((m, p))])
    _spawn_ready(monkeypatch, tmp_path)
    assert seen == [("bonsai2:27b", 8199)]


def test_a_failing_hook_never_costs_the_seat(monkeypatch, tmp_path):
    def _boom(m, p):
        raise RuntimeError("hook broke")
    seen = []
    monkeypatch.setattr(ra, "SEAT_READY_HOOKS", [_boom, lambda m, p: seen.append(m)])
    _spawn_ready(monkeypatch, tmp_path)
    assert seen == ["bonsai2:27b"]


def _fake_agent(monkeypatch, calls):
    from agent_friday.services import agent as ag

    def _gen(messages, **kw):
        calls.append({"messages": messages, **kw})
        return "", []
    monkeypatch.setattr(ag, "_generate_agent", _gen)


def test_the_warm_sends_the_head_alone_as_one_token(monkeypatch):
    calls = []
    _fake_agent(monkeypatch, calls)
    monkeypatch.setattr(seat_warm, "_is_brain", lambda m: True)
    monkeypatch.setattr(seat_warm, "canonical_head", lambda ws="": "PERSONA\nTOOLS\n")
    out = seat_warm.warm("bonsai2:27b", 8199)
    assert out["warmed"] is True
    assert len(calls) == 1
    call = calls[0]
    assert call["max_tokens"] == 1
    assert call["model"] == "bonsai2:27b"
    assert call["system"] == "PERSONA\nTOOLS\n"
    assert call["session_ctx"]["prefix_warm"] is True


def test_the_canonical_head_stops_at_the_volatile_marker(monkeypatch):
    from agent_friday.services import model_router as mr
    from agent_friday.services.prompt_cache import VOLATILE_MARKER
    monkeypatch.setattr(mr, "_get_friday_system_prompt",
                        lambda *a, **k: "HEAD\n" + VOLATILE_MARKER + "\nnow 09:00\n")
    monkeypatch.setattr(mr, "_vault_local_only", lambda: False)
    assert seat_warm.canonical_head() == "HEAD\n"


def test_only_the_brain_seat_is_warmed(monkeypatch):
    calls = []
    _fake_agent(monkeypatch, calls)
    monkeypatch.setattr(seat_warm, "_is_brain", lambda m: False)
    out = seat_warm.warm("gemma4:e2b", 8198)
    assert out["warmed"] is False
    assert calls == []


def test_the_setting_turns_it_off(monkeypatch):
    calls = []
    _fake_agent(monkeypatch, calls)
    monkeypatch.setattr(seat_warm, "_is_brain", lambda m: True)
    monkeypatch.setattr("agent_friday.core._load_settings",
                        lambda: {"seat_prefix_warm": False})
    assert seat_warm.warm("bonsai2:27b", 8199)["warmed"] is False
    assert calls == []


def test_install_wires_the_warm_to_the_ready_hook(monkeypatch, tmp_path):
    calls = []
    _fake_agent(monkeypatch, calls)
    monkeypatch.setattr(seat_warm, "_is_brain", lambda m: True)
    monkeypatch.setattr(seat_warm, "canonical_head", lambda ws="": "HEAD\n")
    monkeypatch.setattr(seat_warm, "_installed", False)
    monkeypatch.setattr(ra, "SEAT_READY_HOOKS", [])
    seat_warm.install()
    _spawn_ready(monkeypatch, tmp_path)
    assert len(calls) == 1 and calls[0]["system"] == "HEAD\n"

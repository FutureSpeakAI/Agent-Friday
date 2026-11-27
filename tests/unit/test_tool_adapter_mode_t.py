"""Mode T: a tool-skill adapter on the brain seat, applied only on Laya's confident short tool
turns and never on vault turns (services/tool_adapter).

The rule is walked over every small case; the payload tests drive `_call_openai` to the
transport; the seat tests build the spawn command and run the readiness check.
"""
from __future__ import annotations

import io
import itertools
import json
import subprocess
import urllib.request

import pytest

from agent_friday.core import DEFAULT_SETTINGS
from agent_friday.services import model_store
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import tool_adapter as ta
from tests.unit.test_reasoning_effort_follows_turn_shape import (DEEP, DEGRADED, ORDINARY,
                                                                 REFLEX, _payload_for)

ON = {"tool_adapter": {"mode": "tool_turns"}}
MODE_T_SEAT = {"lora": "/x/fw27b-lora.gguf", "lora_apply": "per_request"}


def test_the_setting_ships_off_and_off_is_the_plain_base():
    assert DEFAULT_SETTINGS["tool_adapter"] == {"mode": "off"}
    assert ta.mode(dict(DEFAULT_SETTINGS)) is ta.Mode.OFF
    assert ta.strength(dict(DEFAULT_SETTINGS), REFLEX, {}) == 0


def test_strength_is_one_exactly_on_a_confident_tool_turn_that_is_not_vault_work():
    modes = [None, {}, {"tool_adapter": {"mode": "off"}}, ON,
             {"tool_adapter": {"mode": "always"}}, {"tool_adapter": None}]
    shapes = [None, {}, REFLEX, ORDINARY, DEEP, DEGRADED, dict(REFLEX, degraded=True)]
    ctxs = [None, {}, {"vault_access": False}, {"vault_access": True}]
    for settings, shape, ctx in itertools.product(modes, shapes, ctxs):
        want = int(settings is ON and shape is REFLEX and not (ctx or {}).get("vault_access"))
        assert ta.strength(settings, shape, ctx) == want, (settings, shape, ctx)


def test_only_a_per_request_seat_gets_a_request_field():
    assert ta.request_field(None, ON, REFLEX, {}) is None
    assert ta.request_field({"lora": "/x/a.gguf"}, ON, REFLEX, {}) is None, \
        "an always-on adapter seat (FridayWeaver-1.0 today) is left exactly as it was"
    assert ta.request_field({"lora_apply": "per_request"}, ON, REFLEX, {}) is None
    assert ta.request_field(MODE_T_SEAT, ON, REFLEX, {}) == [{"id": 0, "scale": 1}]
    assert ta.request_field(MODE_T_SEAT, ON, REFLEX, {"vault_access": True}) == \
        [{"id": 0, "scale": 0}]


# ── the payload ─────────────────────────────────────────────────────────────

def _seat(monkeypatch, record):
    monkeypatch.setattr(model_store, "get", lambda mid: dict(record) if mid == "bonsai2:27b" else None)


def test_the_payload_names_strength_one_on_a_tool_turn_and_zero_on_a_vault_turn(monkeypatch):
    _seat(monkeypatch, MODE_T_SEAT)
    p = _payload_for(monkeypatch, ON, session_ctx={"turn_shape": REFLEX})
    assert p["lora"] == [{"id": 0, "scale": 1}]
    p = _payload_for(monkeypatch, ON, session_ctx={"turn_shape": REFLEX, "vault_access": True})
    assert p["lora"] == [{"id": 0, "scale": 0}]
    p = _payload_for(monkeypatch, ON, session_ctx={"turn_shape": ORDINARY})
    assert p["lora"] == [{"id": 0, "scale": 0}]


def test_a_seat_without_a_per_request_adapter_gets_no_lora_field(monkeypatch):
    _seat(monkeypatch, {"lora": None})
    assert "lora" not in _payload_for(monkeypatch, ON, session_ctx={"turn_shape": REFLEX})


# ── the seat ────────────────────────────────────────────────────────────────

def _spawn_cmd(monkeypatch, tmp_path, record):
    captured = {}

    class DeadProc:
        returncode = 1

        def __init__(self, cmd, **kw):
            captured["cmd"] = list(cmd)

        def poll(self):
            return 1

        def terminate(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", DeadProc)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(model_store, "get", lambda mid: dict(record) if mid == "b" else None)
    lora = tmp_path / "fw27b-lora.gguf"
    lora.write_bytes(b"GGUF")
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    with pytest.raises(ra.TransitionError):
        be._spawn_once(tmp_path / "llama-server.exe", "b", 32768,
                       gguf_path=tmp_path / "base.gguf", port=8199, lora_path=lora)
    return captured["cmd"]


def test_a_per_request_seat_loads_its_adapter_unapplied(monkeypatch, tmp_path):
    cmd = _spawn_cmd(monkeypatch, tmp_path, MODE_T_SEAT)
    assert "--lora" in cmd and "--lora-init-without-apply" in cmd
    cmd = _spawn_cmd(monkeypatch, tmp_path, {"lora": "/x/a.gguf"})
    assert "--lora" in cmd and "--lora-init-without-apply" not in cmd


def _lora_list(monkeypatch, rows):
    class R(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: R(json.dumps(rows).encode()))


def test_the_readiness_check_accepts_scale_zero_only_for_a_per_request_seat(monkeypatch):
    _lora_list(monkeypatch, [{"id": 0, "path": "/x/fw27b-lora.gguf", "scale": 0.0}])
    check = ra.LlamaServerBackend._adapter_missing
    assert check(8199, "/x/fw27b-lora.gguf", unapplied_ok=True) is None
    assert check(8199, "/x/fw27b-lora.gguf"), "an always-on seat at scale 0 is still refused"
    _lora_list(monkeypatch, [])
    assert check(8199, "/x/fw27b-lora.gguf", unapplied_ok=True), \
        "a per-request seat whose adapter did not load is refused"


def test_the_store_keeps_lora_apply_only_beside_an_adapter(monkeypatch, tmp_path):
    monkeypatch.setattr(model_store, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(model_store, "registry_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(model_store, "describe", lambda p: {"path": str(p), "can_generate": True})
    base, lora = tmp_path / "base.gguf", tmp_path / "a.gguf"
    base.write_bytes(b"GGUF")
    lora.write_bytes(b"GGUF")
    assert model_store.register("m", base, lora=lora, lora_apply="per_request")["lora_apply"] \
        == "per_request"
    assert "lora_apply" not in model_store.register("m", base, lora_apply="per_request")

"""Mode T: a tool-skill adapter on the brain seat, applied only on Laya's confident short tool
turns whose words the sensitivity check rates public (services/tool_adapter). Every unsure
or failing path names strength 0, the plain base.

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
ASK = [{"role": "system", "content": "s"}, {"role": "user", "content": "turn on the lights"}]
PRIVATE_ASK = [{"role": "user", "content": "what's my social security number and bank balance"}]
READING_RESULTS = ASK + [{"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
                         {"role": "tool", "content": "ok"}]
#: Critic B3 on 1e757b691: after an empty reply the local loop appends an empty assistant
#: message and a role-"user" nudge (services/agent.py); the tool result is still in context.
NUDGED = READING_RESULTS + [{"role": "assistant", "content": ""},
                            {"role": "user", "content": "(Automated check: answer the user.)"}]
MODE_T_SEAT = {"lora": "/x/fw27b-lora.gguf", "lora_apply": "per_request"}


def test_the_setting_ships_off_and_off_is_the_plain_base():
    assert DEFAULT_SETTINGS["tool_adapter"] == {"mode": "off"}
    assert ta.mode(dict(DEFAULT_SETTINGS)) is ta.Mode.OFF
    assert ta.strength(dict(DEFAULT_SETTINGS), REFLEX, ASK, {}) == 0


def test_strength_is_one_exactly_on_a_confident_public_tool_choice(monkeypatch):
    from agent_friday.services import sensitivity_classifier as sc
    monkeypatch.setattr(sc, "classify", lambda text, **k: sc.Tier.PRIVATE if "secret" in text
                        else sc.Tier.PUBLIC)
    modes = [None, {}, {"tool_adapter": {"mode": "off"}}, ON,
             {"tool_adapter": {"mode": "always"}}, {"tool_adapter": None}]
    shapes = [None, {}, REFLEX, ORDINARY, DEEP, DEGRADED, dict(REFLEX, degraded=True)]
    convos = [None, [], ASK, READING_RESULTS, [{"role": "user", "content": "a secret"}],
              [{"role": "user", "content": [{"type": "text", "text": "lights"}]}], NUDGED,
              [{"role": "assistant", "content": "", "function_call": {"name": "x"}},
               {"role": "user", "content": "lights"}]]
    ctxs = [None, {}, {"vault_access": False}, {"vault_access": True}]
    for settings, shape, convo, ctx in itertools.product(modes, shapes, convos, ctxs):
        want = int(settings is ON and shape is REFLEX
                   and (convo is convos[2] or convo is convos[5])
                   and not (ctx or {}).get("vault_access"))
        assert ta.strength(settings, shape, convo, ctx) == want, (settings, shape, convo, ctx)


def test_the_real_classifier_holds_a_private_ask_at_zero_whatever_the_routing():
    """Critic B1 on 1de01f63c: the router's vault verdict is None when vault_local_only is off,
    so the rule asks the classifier itself."""
    assert ta.strength(ON, REFLEX, PRIVATE_ASK, {}) == 0
    assert ta.strength(ON, REFLEX, PRIVATE_ASK, None) == 0


def test_a_classifier_failure_is_private(monkeypatch):
    from agent_friday.services import sensitivity_classifier as sc

    def boom(*a, **k):
        raise RuntimeError("classifier down")
    monkeypatch.setattr(sc, "classify", boom)
    assert ta.strength(ON, REFLEX, ASK, {}) == 0
    assert ta.request_field(MODE_T_SEAT, ON, REFLEX, ASK, {}) == [{"id": 0, "scale": 0}]


def test_a_misspelt_lora_apply_is_an_error_not_an_always_on_seat():
    assert ta.parse_apply("per_request") is ta.Apply.PER_REQUEST
    assert ta.parse_apply(None) is None
    with pytest.raises(ValueError):
        ta.parse_apply("per-request")


def test_only_a_per_request_seat_gets_a_request_field(monkeypatch):
    from agent_friday.services import sensitivity_classifier as sc
    monkeypatch.setattr(sc, "classify", lambda text, **k: sc.Tier.PUBLIC)
    assert ta.request_field(None, ON, REFLEX, ASK, {}) is None
    assert ta.request_field({"lora": "/x/a.gguf"}, ON, REFLEX, ASK, {}) is None, \
        "an always-on adapter seat (FridayWeaver-1.0 today) is left exactly as it was"
    assert ta.request_field({"lora_apply": "per_request"}, ON, REFLEX, ASK, {}) is None
    assert ta.request_field(MODE_T_SEAT, ON, REFLEX, ASK, {}) == [{"id": 0, "scale": 1}]
    assert ta.request_field(MODE_T_SEAT, ON, REFLEX, ASK, {"vault_access": True}) == \
        [{"id": 0, "scale": 0}]


# ── the payload ─────────────────────────────────────────────────────────────

def _seat(monkeypatch, record):
    monkeypatch.setattr(model_store, "get", lambda mid: dict(record) if mid == "bonsai2:27b" else None)


def test_the_payload_names_strength_one_on_a_tool_turn_and_zero_on_a_vault_turn(monkeypatch):
    from agent_friday.services import sensitivity_classifier as sc
    monkeypatch.setattr(sc, "classify", lambda text, **k: sc.Tier.PUBLIC)
    _seat(monkeypatch, MODE_T_SEAT)
    p = _payload_for(monkeypatch, ON, session_ctx={"turn_shape": REFLEX})
    assert p["lora"] == [{"id": 0, "scale": 1}]
    p = _payload_for(monkeypatch, ON, session_ctx={"turn_shape": REFLEX, "vault_access": True})
    assert p["lora"] == [{"id": 0, "scale": 0}]
    p = _payload_for(monkeypatch, ON, session_ctx={"turn_shape": ORDINARY})
    assert p["lora"] == [{"id": 0, "scale": 0}]
    monkeypatch.setattr(sc, "classify", lambda text, **k: sc.Tier.SENSITIVE)
    p = _payload_for(monkeypatch, ON, session_ctx={"turn_shape": REFLEX})
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


def test_an_adapter_seat_whose_record_cannot_be_read_is_not_started(monkeypatch, tmp_path):
    """Critic note on 1de01f63c: guessing "not per-request" would start the adapter applied."""
    def locked(mid):
        raise OSError("locked")
    for broken in (lambda mid: None, locked):
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
        monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
        monkeypatch.setattr(model_store, "get", broken)
        lora = tmp_path / "fw27b-lora.gguf"
        lora.write_bytes(b"GGUF")
        be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
        with pytest.raises(ra.TransitionError, match="record cannot be read"):
            be._spawn_once(tmp_path / "llama-server.exe", "b", 32768,
                           gguf_path=tmp_path / "base.gguf", port=8199, lora_path=lora)


def _lora_list(monkeypatch, rows):
    class R(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: R(json.dumps(rows).encode()))


def test_a_per_request_seat_must_come_up_at_exactly_scale_zero(monkeypatch):
    check = ra.LlamaServerBackend._adapter_missing
    _lora_list(monkeypatch, [{"id": 0, "path": "/x/fw27b-lora.gguf", "scale": 0.0}])
    assert check(8199, "/x/fw27b-lora.gguf", per_request=True) is None
    assert check(8199, "/x/fw27b-lora.gguf"), "an always-on seat at scale 0 is still refused"
    _lora_list(monkeypatch, [{"id": 0, "path": "/x/fw27b-lora.gguf", "scale": 1.0}])
    assert check(8199, "/x/fw27b-lora.gguf", per_request=True), \
        "a per-request seat that came up applied is refused"
    _lora_list(monkeypatch, [])
    assert check(8199, "/x/fw27b-lora.gguf", per_request=True), \
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
    with pytest.raises(ValueError):
        model_store.register("m", base, lora=lora, lora_apply="per-request")

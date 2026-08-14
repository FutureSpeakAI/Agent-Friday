"""FR-1 hardening — resolve_local_seat() actually enforced inside
_call_ollama's real dispatch path (not just unit-tested in isolation).

Same manager-boundary-stub pattern as test_provider_loop_regression.py: the
real _call_ollama / _oai_agentic_loop runs end-to-end, only the Ollama HTTP
call is faked, so this proves the seat check is genuinely wired into the
live code path a real request takes — not just that resolve_local_seat()
works when called directly.
"""
from __future__ import annotations

import pytest

import agent_friday.services.model_router as smr
from agent_friday.services import model_seat_gate as gate

pytestmark = pytest.mark.real_provider_paths


class _FakeOllamaManager:
    base_url = "http://localhost:11434"

    def __init__(self):
        self.calls = []

    def is_available(self):
        return True

    def chat_completion(self, messages, model, tools=None, temperature=0.7,
                        max_tokens=4096):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        return {
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "model": model,
        }


ANTHROPIC_TOOL = {
    "name": "read_file", "description": "Read a file.",
    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"]},
}


@pytest.fixture
def fake_manager(monkeypatch):
    import agent_friday.routing.ollama_manager as ollama_manager
    fake = _FakeOllamaManager()
    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **k: fake)
    return fake


@pytest.fixture
def isolated_gate_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "GATE_DIR", tmp_path / "live")
    monkeypatch.setattr(gate, "EVIDENCE_DIR", tmp_path / "evidence")
    # 2026-08-14: the gate now invalidates a green fallback that isn't in
    # the live daemon inventory. These tests' green records must count as
    # installed — and must never consult the REAL daemon from a unit run.
    monkeypatch.setattr(gate, "_installed_local_models",
                        lambda: {"gemma4:latest", "gemma3:4b",
                                 "red-model:latest"})
    return tmp_path


def _mark_green(model, provider="local", ts=100):
    gate.save_status(model, provider, {"model": model, "provider": provider,
                                       "passed": True, "score": "10/10",
                                       "timestamp": ts})


def _mark_red(model, provider="local", ts=100):
    gate.save_status(model, provider, {"model": model, "provider": provider,
                                       "passed": False, "score": "0/10",
                                       "timestamp": ts})


class TestSeatEnforcementInRealDispatch:
    def test_green_seat_dispatches_to_the_requested_model_unmodified(
            self, fake_manager, isolated_gate_dirs):
        _mark_green("gemma4:latest")
        text, trace = smr._call_ollama(
            [{"role": "user", "content": "hi"}], model="gemma4:latest",
            tools=[ANTHROPIC_TOOL],
        )
        assert text == "ok"
        assert fake_manager.calls[0]["model"] == "gemma4:latest"
        # No seat-refusal note injected for a clean green seat.
        assert not any("hasn't passed the tool-calling conformance gate" in
                       (m.get("content") or "") for m in fake_manager.calls[0]["messages"])

    def test_red_seat_is_substituted_with_the_last_known_green_model(
            self, fake_manager, isolated_gate_dirs):
        _mark_red("gemma3:4b")
        _mark_green("gemma4:latest")
        text, trace = smr._call_ollama(
            [{"role": "user", "content": "what's on my calendar?"}],
            model="gemma3:4b", tools=[ANTHROPIC_TOOL],
        )
        assert text == "ok"
        # The ACTUAL dispatched model was swapped, not the red one.
        assert fake_manager.calls[0]["model"] == "gemma4:latest"

    def test_red_seat_with_no_fallback_drops_tools_entirely(
            self, fake_manager, isolated_gate_dirs):
        _mark_red("gemma3:4b")
        text, trace = smr._call_ollama(
            [{"role": "user", "content": "what's on my calendar?"}],
            model="gemma3:4b", tools=[ANTHROPIC_TOOL],
        )
        assert text == "ok"
        # tools=None reaches Ollama — a red model with no fallback gets NO
        # tool schema at all, not a downgraded-but-still-tooled call.
        assert fake_manager.calls[0]["tools"] is None
        assert fake_manager.calls[0]["model"] == "gemma3:4b"  # no fallback model exists

    def test_never_gated_model_is_refused_same_as_red(self, fake_manager, isolated_gate_dirs):
        _mark_green("gemma4:latest")
        text, trace = smr._call_ollama(
            [{"role": "user", "content": "hi"}], model="brand-new-model:latest",
            tools=[ANTHROPIC_TOOL],
        )
        assert fake_manager.calls[0]["model"] == "gemma4:latest"

    def test_toolfree_turn_is_never_gated_at_all(self, fake_manager, isolated_gate_dirs):
        # No tools requested -> nothing to protect -> the gate must not
        # interfere with ordinary tool-free chat even on a red model.
        _mark_red("gemma3:4b")
        text, trace = smr._call_ollama(
            [{"role": "user", "content": "hi"}], model="gemma3:4b", tools=None,
        )
        assert fake_manager.calls[0]["model"] == "gemma3:4b"
        assert fake_manager.calls[0]["tools"] is None

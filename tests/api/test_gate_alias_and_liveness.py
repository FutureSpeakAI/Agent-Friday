"""Defects #4 and #6 — gate-store alias mismatch + probe truth.

#4: a brain seated as 'qwen3.6-35b-a3b-iq4nl' (llama-cpp-brain,
OpenAI-compatible local descriptor) must be diagnosed through its own
protocol, not Ollama's. The gate dispatches via the descriptor's endpoint,
and descriptor-declared models count as installed.

#6 (server side): the health probe must never send a foreign id to a
provider — probing Anthropic with the qwen alias 404s (shown down), and
probing Ollama with a deleted model shows it down while healthy.
"""
from __future__ import annotations

import json

from agent_friday.services import model_seat_gate as gate
from agent_friday.services import provider_health as ph

BRAIN = "qwen3.6-35b-a3b-iq4nl"
BRAIN_PROV = {
    "name": "llama-cpp-brain", "label": "Local brain (llama.cpp)",
    "type": "openai-compatible", "base_url": "http://127.0.0.1:8081/v1",
    "classification": "local", "models": [BRAIN], "enabled": True,
}


def _reg_with_brain(monkeypatch):
    class FakeReg:
        def get_enabled_providers(self):
            return [BRAIN_PROV]

    import agent_friday.services.provider_registry as pr
    monkeypatch.setattr(pr, "get_provider_registry", lambda: FakeReg())


class TestGateSpeaksTheBrainsProtocol:
    def test_gate_dispatch_targets_the_descriptor_endpoint(self, monkeypatch):
        _reg_with_brain(monkeypatch)
        chat_fn, via = gate._gate_chat_fn(BRAIN, "http://localhost:11434")
        assert via == "llama-cpp-brain", (
            "the gate must talk llama-server for the brain, not Ollama")

    def test_ollama_models_still_use_the_daemon(self, monkeypatch):
        _reg_with_brain(monkeypatch)
        chat_fn, via = gate._gate_chat_fn("gemma4:e4b", "http://localhost:11434")
        assert via == "ollama"

    def test_descriptor_models_count_as_installed(self, monkeypatch):
        _reg_with_brain(monkeypatch)
        import agent_friday.routing.ollama_manager as om

        class DeadMgr:
            def is_available(self):
                return False

        monkeypatch.setattr(om, "get_manager", lambda url=None: DeadMgr())
        installed = gate._installed_local_models()
        assert installed is not None and BRAIN in installed, (
            "a green brain must not be invalidated as 'not installed' just "
            "because it isn't an Ollama tag")


class TestNothingRefusesASeat:
    """No seat gate, so no alias refusal.

    The user may set any model at any seat. What is pinned is that the id
    under which a model is seated is the id that gets dispatched, alias or
    not, and that a near-name record (qwen3.6:35b vs qwen3.6-35b-a3b-iq4nl) is
    never borrowed — a score does not transfer between two ids that merely
    look alike.
    """

    def test_a_descriptor_id_is_dispatched_as_itself(self):
        seat = gate.resolve_local_seat(BRAIN)
        assert seat["model"] == BRAIN
        assert seat.get("seat_ok") is not False

    def test_a_near_name_record_has_no_effect_either_way(self, monkeypatch,
                                                        tmp_path):
        """It cannot refuse, and it must not be silently borrowed either."""
        monkeypatch.setattr(gate, "GATE_DIR", tmp_path)
        (tmp_path / "local__qwen3.6_35b.json").write_text(json.dumps({
            "model": "qwen3.6:35b", "provider": "local", "passed": True,
            "timestamp": 100, "score": "10/10"}), encoding="utf-8")
        seat = gate.resolve_local_seat(BRAIN)
        assert seat["model"] == BRAIN, "a lookalike record must not be adopted"

class TestProbeModelChoice:
    def test_anthropic_probe_never_gets_a_foreign_id(self, monkeypatch):
        import agent_friday.core as core_mod
        monkeypatch.setattr(core_mod, "_load_settings",
                            lambda: {"orchestrator_model": BRAIN})
        monkeypatch.setattr(ph, "_load_settings",
                            lambda: {"orchestrator_model": BRAIN},
                            raising=False)
        chosen = ph.resident_model_for(
            {"type": "anthropic", "models": ["claude-sonnet-5"]})
        assert str(chosen).startswith("claude"), (
            f"anthropic probe would 404 on {chosen!r} and /api/health would "
            f"show a healthy provider as down")

    def test_ollama_probe_skips_uninstalled_configured_model(self, monkeypatch):
        import agent_friday.routing.ollama_manager as om

        class Mgr:
            def list_models(self):
                return [{"name": "gemma4:e2b", "size_gb": 4.0},
                        {"name": "gemma4:e4b", "size_gb": 8.0}]

        monkeypatch.setattr(om, "get_manager", lambda url=None: Mgr())
        import agent_friday.core as core_mod
        monkeypatch.setattr(
            core_mod, "_load_settings",
            lambda: {"model_routing": {"local_model": "gemma4:latest"}})
        chosen = ph.resident_model_for({"type": "ollama"})
        assert chosen in ("gemma4:e2b", "gemma4:e4b"), (
            f"probing deleted {chosen!r} reports a healthy daemon as down")

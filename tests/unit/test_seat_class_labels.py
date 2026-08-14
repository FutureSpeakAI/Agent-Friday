"""2026-08-14 defect #7 — a local OpenAI-compatible provider (the llama.cpp
brain at 127.0.0.1) must classify and render as LOCAL, not cloud.

Live evidence: seat lines said "orchestrator seat (cloud)
qwen3.6-35b-a3b-iq4nl" and the routing panel said "· cloud" for a model
served on-device at port 8081.
"""
from __future__ import annotations

from agent_friday.services import seat_transparency as st

BRAIN = "qwen3.6-35b-a3b-iq4nl"
BRAIN_PROV = {
    "name": "llama-cpp-brain", "type": "openai-compatible",
    "base_url": "http://127.0.0.1:8081/v1", "classification": "local",
    "models": [BRAIN], "enabled": True,
}
ANTHROPIC_PROV = {
    "name": "anthropic", "type": "anthropic",
    "models": ["claude-sonnet-5"], "enabled": True,
}


def _reg(monkeypatch, provs):
    class FakeReg:
        def get_enabled_providers(self):
            return provs

    import agent_friday.services.provider_registry as pr
    monkeypatch.setattr(pr, "get_provider_registry", lambda: FakeReg())


class TestEffectiveSeatClass:
    def test_local_descriptor_orchestrator_renders_local(self, monkeypatch):
        _reg(monkeypatch, [BRAIN_PROV, ANTHROPIC_PROV])
        model, seat = st.effective_seat({
            "orchestrator_model": BRAIN,
            "capability_routing": {"reasoning": {"provider": "llama-cpp-brain",
                                                 "model": BRAIN}},
            "model_routing": {"mode": "smart", "local_model": "gemma4:e4b"},
        })
        assert model == BRAIN
        assert seat == "local", (
            "an on-device llama-server seat labeled 'cloud' is the #7 lie")

    def test_claude_orchestrator_still_cloud(self, monkeypatch):
        _reg(monkeypatch, [BRAIN_PROV, ANTHROPIC_PROV])
        model, seat = st.effective_seat({
            "orchestrator_model": "claude-sonnet-5",
            "capability_routing": {"reasoning": {"provider": "anthropic",
                                                 "model": "claude-sonnet-5"}},
            "model_routing": {"mode": "smart"},
        })
        assert (model, seat) == ("claude-sonnet-5", "cloud")

    def test_local_only_mode_unchanged(self, monkeypatch):
        _reg(monkeypatch, [BRAIN_PROV])
        model, seat = st.effective_seat({
            "orchestrator_model": "claude-sonnet-5",
            "model_routing": {"mode": "local_only", "local_model": "gemma4:e4b"},
        })
        assert (model, seat) == ("gemma4:e4b", "local")


class TestAnnouncerLabel:
    def test_orchestrator_label_carries_no_hardcoded_cloud(self):
        assert "(cloud)" not in st._WATCHED["orchestrator_model"], (
            "the seat line hardcoded '(cloud)' regardless of the provider")


class TestHealthCarriesClassification:
    def test_probe_result_includes_classification(self, monkeypatch):
        from agent_friday.services import provider_health as ph
        monkeypatch.setattr(ph, "_provider", lambda name: BRAIN_PROV,
                            raising=False)
        # Force the openai-compatible branch to fail fast at connection —
        # the classification must still ride the result.
        res = ph.inference_probe("llama-cpp-brain", prov=BRAIN_PROV,
                                 use_cache=False)
        assert res is not None
        assert res.get("classification") == "local", (
            "the UI can only label the seat honestly if health says which "
            "providers are on-device")

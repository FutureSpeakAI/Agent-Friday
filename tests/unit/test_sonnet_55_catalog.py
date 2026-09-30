"""The selectable Sonnet 5.5 entry carries its published limits and rates."""
import pytest

from agent_friday.services import cost_meter, model_catalog, prompt_cache, reasoning_trace
from agent_friday.services.provider_registry import ProviderRegistry
from agent_friday.routing.model_router import CLOUD_COST_PER_1K, provider_family


MODEL = "claude-sonnet-5-5"


@pytest.fixture(autouse=True)
def catalog_without_machine_probes(monkeypatch):
    registry = ProviderRegistry()
    anthropic = registry.get_provider("anthropic")
    monkeypatch.setattr(registry, "get_enabled_providers", lambda: [anthropic])
    monkeypatch.setattr(registry, "list_providers", lambda: [anthropic])
    monkeypatch.setattr(registry, "is_provider_available", lambda name: True)
    monkeypatch.setattr(model_catalog, "get_provider_registry", lambda: registry)
    monkeypatch.setattr(model_catalog, "_arbiter_seat_entries", lambda: [])
    monkeypatch.setattr(model_catalog, "_friday_store_entries", lambda **kw: [])
    monkeypatch.setattr(model_catalog, "_discovered_models", lambda provider: ([], False))
    monkeypatch.setattr(model_catalog, "_voice_engines", lambda registry: [])
    monkeypatch.setattr(model_catalog, "_tts_engines", lambda: [])
    monkeypatch.setattr(model_catalog, "_served_context_window", lambda model: None)
    monkeypatch.setattr(model_catalog, "_CTX_CACHE", {})


def test_sonnet_55_is_selectable_in_both_agent_roles():
    catalog = model_catalog.build_catalog()
    for role in ("orchestrator", "subagent"):
        matches = [entry for entry in catalog["roles"][role] if entry["id"] == MODEL]
        assert len(matches) == 1
        assert matches[0]["label"] == "Claude Sonnet 5.5"
    assert model_catalog.context_window_for(MODEL) == 1_000_000
    assert model_catalog.max_output_for(MODEL) == 128_000


def test_sonnet_55_pricing_and_cache_threshold():
    assert cost_meter.PRICING[MODEL] == {"in": 0.002, "out": 0.010}
    assert CLOUD_COST_PER_1K[MODEL] == pytest.approx(0.006)
    assert prompt_cache._min_cacheable(MODEL) == 512


def test_sonnet_55_uses_the_native_claude_route_and_adaptive_summary():
    assert provider_family(MODEL) == "anthropic"
    assert reasoning_trace.anthropic_thinking(MODEL) == {
        "type": "adaptive", "display": "summarized"}

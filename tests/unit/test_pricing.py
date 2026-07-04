"""Unit tests for services/pricing — the tiered price lookup (spec §6.4).

Tier order: discovery cache → descriptor static → legacy PRICING table → None.
The critical invariant: unknown price is None, NEVER 0 (unknown ≠ free).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import model_discovery as md
from agent_friday.services import pricing


class TestPriceTiers:
    def test_discovery_cache_wins(self):
        md.write_cache("pricing-test-prov", [
            {"id": "test/model-a", "price_in": 0.25, "price_out": 0.75}],
            ttl_s=3600)
        prov = {"name": "pricing-test-prov", "type": "openai-compatible",
                "base_url": "https://x.test/v1",
                "pricing": {"static": {"test/model-a": {"in": 9.0, "out": 9.0}}}}
        try:
            p = pricing.price(prov, "test/model-a")
            assert p["source"] == "discovery"
            assert p["in_per_1m"] == pytest.approx(0.25)
            assert p["out_per_1m"] == pytest.approx(0.75)
        finally:
            md.invalidate_cache("pricing-test-prov")

    def test_descriptor_static_second(self):
        prov = {"name": "pricing-test-prov2", "type": "openai-compatible",
                "base_url": "https://x.test/v1",
                "pricing": {"static": {"m": {"in": 3.0, "out": 15.0}}}}
        p = pricing.price(prov, "m")
        assert p["source"] == "static"
        assert p["in_per_1m"] == 3.0 and p["out_per_1m"] == 15.0

    def test_v1_blended_cost_per_1k_read(self):
        prov = {"name": "pricing-test-prov3", "type": "openai-compatible",
                "base_url": "https://x.test/v1",
                "cost_per_1k": {"m": 0.03}}
        p = pricing.price(prov, "m")
        assert p["source"] == "static-blended"
        assert p["in_per_1m"] == pytest.approx(30.0)

    def test_legacy_pricing_table_third(self):
        p = pricing.price("anthropic", "claude-sonnet-5")
        assert p is not None
        assert p["source"] == "dataset"
        assert p["in_per_1m"] == pytest.approx(3.0)   # 0.003/1K → 3/1M
        assert p["out_per_1m"] == pytest.approx(15.0)

    def test_unknown_is_none_not_zero(self):
        """Unknown ≠ free — the cost meter logs tokens with cost=None rather
        than pretending $0."""
        assert pricing.price("openrouter", "totally/unknown-model-xyz") is None
        assert pricing.cost_usd("openrouter", "totally/unknown-model-xyz",
                                1000, 1000) is None

    def test_local_provider_is_zero(self):
        p = pricing.price("ollama-local", "gemma3:4b")
        assert p == {"in_per_1m": 0.0, "out_per_1m": 0.0, "source": "local"}

    def test_cost_usd_math(self):
        prov = {"name": "pricing-math", "type": "openai-compatible",
                "base_url": "https://x.test/v1",
                "pricing": {"static": {"m": {"in": 1.0, "out": 2.0}}}}
        # 500K in + 250K out at $1/$2 per 1M = 0.5 + 0.5 = 1.0
        assert pricing.cost_usd(prov, "m", 500_000, 250_000) == pytest.approx(1.0)

    def test_blended_per_1k(self):
        prov = {"name": "pricing-blend", "type": "openai-compatible",
                "base_url": "https://x.test/v1",
                "pricing": {"static": {"m": {"in": 1.0, "out": 3.0}}}}
        assert pricing.blended_per_1k(prov, "m") == pytest.approx(0.002)


class TestCostMeterIntegration:
    def test_meter_honors_openrouter_usage_cost(self, monkeypatch):
        """OpenRouter usage accounting reports the exact billed cost — it must
        override local price math."""
        from agent_friday.services import cost_meter
        recorded = {}
        def _fake_record(provider, model, in_tok=0, out_tok=0, **kw):
            recorded.update(provider=provider, model=model,
                            cost_usd=kw.get("cost_usd"))
            return kw.get("cost_usd") or 0.0
        monkeypatch.setattr(cost_meter, "record", _fake_record)
        cost_meter.meter("openrouter", "meta-llama/llama-4-maverick",
                         {"prompt_tokens": 100, "completion_tokens": 50,
                          "cost": 0.000123})
        assert recorded["cost_usd"] == pytest.approx(0.000123)

    def test_meter_without_cost_field_unchanged(self, monkeypatch):
        from agent_friday.services import cost_meter
        recorded = {}
        def _fake_record(provider, model, in_tok=0, out_tok=0, **kw):
            recorded.update(cost_usd=kw.get("cost_usd"))
            return 0.0
        monkeypatch.setattr(cost_meter, "record", _fake_record)
        cost_meter.meter("anthropic", "claude-sonnet-5",
                         {"input_tokens": 10, "output_tokens": 5})
        assert recorded["cost_usd"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

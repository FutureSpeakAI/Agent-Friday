"""Gauntlet finding Q11 part (b): five opt-in provider catalogs -- Groq,
Mistral, DeepSeek, xAI, Cohere (routing/provider_descriptors.py
BUILTIN_EXTRA_PROVIDERS) -- metered as exactly $0 for every call,
structurally, through all three pricing-resolution tiers: cost_meter.
PRICING had no entries for their models, their descriptors' cost_per_1k/
pricing.static fields are empty by construction, and their real /models
endpoints don't return a pricing object for the generic discovery parser
to capture (docs/audits/gauntlet-2026-09-03/findings.jsonl, Q11).

The finding is explicit that the CODE PATH itself (_call_openai ->
agent.py's _oai_agentic_loop -> cost_meter.meter()) already works
correctly -- confirmed elsewhere in this audit for other openai-compatible
providers -- so the only real gap is the empty price table. This test
therefore targets `cost_meter.price_for()`/`cost_for()` directly: the
actual functions every metered call resolves a price through, not a
source-text pin, and not a reconstruction of the whole multi-provider HTTP
call machinery to prove something that finding already confirmed works.
"""
from __future__ import annotations

import agent_friday.services.cost_meter as cost_meter
import agent_friday.routing.provider_descriptors as pd


class TestExtraProviderModelsAreNoLongerFree:
    def test_mistral_declared_models_price_nonzero(self):
        """Mistral declares its model ids explicitly in
        BUILTIN_EXTRA_PROVIDERS (models=(...)) -- these keys are GUARANTEED
        to match what a real call actually sends as `model`."""
        mistral = next(p for p in pd.BUILTIN_EXTRA_PROVIDERS if p["name"] == "mistral")
        assert mistral["models"], "mistral's descriptor lost its declared model list"
        for model in mistral["models"]:
            cost = cost_meter.cost_for(model, 1000, 1000)
            assert cost > 0, (
                f"mistral model {model!r} still meters as $0 -- exactly the "
                "invisible-spend gap this finding named"
            )

    def test_deepseek_declared_models_price_nonzero(self):
        deepseek = next(p for p in pd.BUILTIN_EXTRA_PROVIDERS if p["name"] == "deepseek")
        assert deepseek["models"]
        for model in deepseek["models"]:
            cost = cost_meter.cost_for(model, 1000, 1000)
            assert cost > 0, f"deepseek model {model!r} still meters as $0"

    def test_at_least_one_common_model_priced_per_provider(self):
        """Groq, xAI, and Cohere declare an EMPTY models=() tuple -- their
        real model list comes from live discovery, so this asserts against
        this fix's best-guess well-known ids rather than the (empty)
        descriptor list, and says so plainly rather than pretending full
        coverage."""
        for provider_name, model_id in (
            ("groq", "llama-3.3-70b-versatile"),
            ("xai", "grok-4"),
            ("cohere", "command-r-plus"),
        ):
            prov = next(p for p in pd.BUILTIN_EXTRA_PROVIDERS if p["name"] == provider_name)
            assert not prov.get("models"), (
                f"{provider_name}'s descriptor now declares explicit models "
                "-- if BUILTIN_EXTRA_PROVIDERS changed, re-check this test's "
                "premise about discovery-only model ids"
            )
            cost = cost_meter.cost_for(model_id, 1000, 1000)
            assert cost > 0, (
                f"{provider_name}'s well-known model {model_id!r} still "
                "meters as $0"
            )

    def test_price_for_returns_nonzero_in_and_out_for_every_new_row(self):
        """Every row this fix added must actually have both directions
        priced -- a row present but zeroed in one direction is the same
        invisible-spend bug in a subtler form."""
        for model in (
            "mistral-large-latest", "mistral-small-latest",
            "deepseek-chat", "deepseek-reasoner",
            "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
            "mixtral-8x7b-32768", "grok-4", "grok-4-fast",
            "command-r-plus", "command-r", "command-a",
        ):
            rate = cost_meter.price_for(model)
            assert rate["in"] > 0, f"{model!r} has a zeroed input rate"
            assert rate["out"] > 0, f"{model!r} has a zeroed output rate"

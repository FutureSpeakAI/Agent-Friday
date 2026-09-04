"""Gauntlet finding Q11 part (b) / F50: five opt-in provider catalogs --
Groq, Mistral, DeepSeek, xAI, Cohere (routing/provider_descriptors.py
BUILTIN_EXTRA_PROVIDERS) -- metered as exactly $0 for every call,
structurally, through all three pricing-resolution tiers (docs/audits/
gauntlet-2026-09-03/findings.jsonl, Q11).

The finding is explicit that the CODE PATH itself (_call_openai ->
agent.py's _oai_agentic_loop -> cost_meter.meter()) already works
correctly -- confirmed elsewhere in this audit for other openai-compatible
providers -- so the only real gap is the price table.

An earlier draft of this fix filled the table with rates sourced from
public pricing-aggregator pages rather than each provider's own page --
findings.jsonl F50 is the write-up of that lapse, caught before it shipped
to a user-facing cost panel. Only mistral-large-latest could be verified
directly against Mistral's own pricing page (mistral.ai/pricing: "$0.5/M
tokens in and $1.5/M tokens out"). Every other model this finding named
is now DELIBERATELY UNPRICED (cost_meter.UNPRICED_MODELS): still metered
(the call is logged with real token counts) but with cost_usd stored as
SQL NULL rather than a guessed number -- the honest resolution the
delegation asked for once a rate couldn't be verified against the
provider's own page. This is a real improvement over the original
$0-for-everyone bug even though it isn't full-coverage: a NULL cost is
visibly "not priced" to any consumer of this data; a silent $0 looks like
a verified free call, which none of these are.
"""
from __future__ import annotations

import agent_friday.services.cost_meter as cost_meter
import agent_friday.routing.provider_descriptors as pd


class TestExtraProviderModelsAreNoLongerSilentlyFree:
    def test_mistral_large_is_verified_and_priced_nonzero(self):
        """The one model in this finding with a rate confirmed directly
        against the provider's own pricing page."""
        cost = cost_meter.cost_for("mistral-large-latest", 1000, 1000)
        assert cost is not None and cost > 0, (
            "mistral-large-latest should be priced -- its rate is verified "
            "directly against mistral.ai/pricing"
        )

    def test_mistral_declared_models_are_either_priced_or_explicitly_unpriced(self):
        """Mistral declares its model ids explicitly in
        BUILTIN_EXTRA_PROVIDERS (models=(...)) -- these keys are GUARANTEED
        to match what a real call actually sends as `model`. Every one must
        resolve to a real price OR an explicit, deliberate "unpriced" --
        never silently $0."""
        mistral = next(p for p in pd.BUILTIN_EXTRA_PROVIDERS if p["name"] == "mistral")
        assert mistral["models"], "mistral's descriptor lost its declared model list"
        for model in mistral["models"]:
            cost = cost_meter.cost_for(model, 1000, 1000)
            is_deliberately_unpriced = model in cost_meter.UNPRICED_MODELS
            assert (cost is not None and cost > 0) or is_deliberately_unpriced, (
                f"mistral model {model!r} meters as a silent $0 -- it must "
                "either have a real verified rate or be explicitly listed "
                "in UNPRICED_MODELS"
            )

    def test_deepseek_declared_models_are_explicitly_unpriced_not_silently_zero(self):
        """No verifiable rate was found on DeepSeek's own pricing page for
        these exact model ids (whose current docs list deepseek-v4-flash/
        -pro instead, suggesting deepseek-chat/deepseek-reasoner may
        themselves be stale -- worth a follow-up, not fabricated here)."""
        deepseek = next(p for p in pd.BUILTIN_EXTRA_PROVIDERS if p["name"] == "deepseek")
        assert deepseek["models"]
        for model in deepseek["models"]:
            assert model in cost_meter.UNPRICED_MODELS, (
                f"deepseek model {model!r} should be in UNPRICED_MODELS "
                "unless a real rate from DeepSeek's own page has since been "
                "added for it"
            )
            assert cost_meter.price_for(model) is None
            assert cost_meter.cost_for(model, 1000, 1000) is None

    def test_groq_xai_cohere_common_models_are_explicitly_unpriced(self):
        """Groq, xAI, and Cohere declare an EMPTY models=() tuple -- their
        real model list comes from live discovery. These well-known ids are
        a best guess at what a user would actually pick, and (like
        deepseek above) have no rate verified against the provider's own
        page -- explicitly unpriced rather than guessed."""
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
            assert model_id in cost_meter.UNPRICED_MODELS
            assert cost_meter.cost_for(model_id, 1000, 1000) is None

    def test_unpriced_call_is_logged_with_null_cost_not_a_fabricated_zero(self, monkeypatch, tmp_path):
        """End to end: recording a call for an UNPRICED_MODELS model must
        store cost_usd as NULL, not silently coerce to 0.0 -- the exact
        distinction F50 is about (a real gap the UI can show as "not
        priced" vs. a number that looks like a verified fact)."""
        monkeypatch.setattr(cost_meter, "DB_PATH", tmp_path / "test_costs.db")
        monkeypatch.setattr(cost_meter, "_CONN", None)
        cost_meter.record("cohere", "command-r", input_tokens=100, output_tokens=50)
        cost_meter.flush()
        conn = cost_meter._conn()
        row = conn.execute(
            "SELECT cost_usd FROM cost_calls WHERE model=?", ("command-r",)
        ).fetchone()
        assert row is not None, "the call was not logged at all"
        assert row[0] is None, (
            f"an unpriced model's call was stored with cost_usd={row[0]!r} "
            "instead of NULL -- this is exactly the 'plausible figure in a "
            "field the product will display as fact' defect F50 describes"
        )

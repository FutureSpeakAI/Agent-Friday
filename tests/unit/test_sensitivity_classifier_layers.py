"""Unit tests for the 4-layer sensitivity classifier. Layers 2-4 (Presidio,
embeddings, local LLM) degrade gracefully when their optional deps are absent,
so these tests focus on the deterministic layers (regex + keyword) and the
aggregation/fail-closed logic, mocking the heavier layers where needed.
"""
from __future__ import annotations

import pytest

from agent_friday.services import sensitivity_classifier as sc
from agent_friday.services.sensitivity_classifier import Tier, classify


def _no_heavy(monkeypatch):
    """Disable the optional layers so we test the deterministic core."""
    monkeypatch.setattr(sc, "_presidio_tier", lambda t: 0)
    monkeypatch.setattr(sc, "_embedding_tier", lambda t: (0, 0.0))
    monkeypatch.setattr(sc, "_local_llm_tier", lambda t: 0)


class TestRegexLayer:
    @pytest.mark.parametrize("text", [
        "123-45-6789", "SSN 123 45 6789",  # pragma: allowlist secret
    ])
    def test_ssn_is_sensitive(self, text, monkeypatch):
        _no_heavy(monkeypatch)
        assert classify(text) == Tier.SENSITIVE

    def test_credit_card_sensitive(self, monkeypatch):
        _no_heavy(monkeypatch)
        assert classify("4111 1111 1111 1111") == Tier.SENSITIVE

    @pytest.mark.parametrize("key", [
        "sk-ant-api03-ABCDEFGHIJKLMNOP1234", "AIzaSyABCDEFGHIJKLMNOP1234567",  # pragma: allowlist secret
    ])
    def test_api_key_sensitive(self, key, monkeypatch):
        _no_heavy(monkeypatch)
        assert classify(f"key {key}") == Tier.SENSITIVE

    def test_routing_number_is_private(self, monkeypatch):
        _no_heavy(monkeypatch)
        # A bare 9-digit token → PRIVATE (regex tier 2), not SENSITIVE.
        assert classify("routing 987654321 here") == Tier.PRIVATE

    def test_regex_only_helper(self):
        assert sc._regex_tier("123-45-6789") == Tier.SENSITIVE  # pragma: allowlist secret
        assert sc._regex_tier("nothing here") == 0


class TestKeywordLayer:
    @pytest.mark.parametrize("kw", [
        "my bank account details", "medical diagnosis", "custody hearing",
        "social security", "tax return figures",
    ])
    def test_tier3_keywords_sensitive(self, kw, monkeypatch):
        _no_heavy(monkeypatch)
        assert classify(kw) == Tier.SENSITIVE

    @pytest.mark.parametrize("kw", [
        "my phone number", "home address", "my daughter", "personal note",
    ])
    def test_tier2_keywords_private(self, kw, monkeypatch):
        _no_heavy(monkeypatch)
        assert classify(kw) == Tier.PRIVATE

    def test_keyword_helper(self):
        assert sc._keyword_tier("bank account") == Tier.SENSITIVE
        assert sc._keyword_tier("home address") == Tier.PRIVATE
        assert sc._keyword_tier("the weather is nice") == 0


class TestAggregationAndDefaults:
    def test_public_default_for_neutral_text(self, monkeypatch):
        _no_heavy(monkeypatch)
        assert classify("The weather in Paris is lovely today.") == Tier.PUBLIC

    def test_default_override_for_empty(self):
        assert classify("", default=Tier.PRIVATE) == Tier.PRIVATE
        assert classify(None, default=Tier.SENSITIVE) == Tier.SENSITIVE

    def test_most_sensitive_wins(self, monkeypatch):
        # regex→0, keyword→PRIVATE, presidio→SENSITIVE ⇒ SENSITIVE
        monkeypatch.setattr(sc, "_embedding_tier", lambda t: (0, 0.0))
        monkeypatch.setattr(sc, "_local_llm_tier", lambda t: 0)
        monkeypatch.setattr(sc, "_presidio_tier", lambda t: Tier.SENSITIVE)
        assert classify("home address") == Tier.SENSITIVE

    def test_two_private_signals_escalate(self, monkeypatch):
        # keyword PRIVATE + presidio PRIVATE → escalate to SENSITIVE
        monkeypatch.setattr(sc, "_embedding_tier", lambda t: (0, 0.0))
        monkeypatch.setattr(sc, "_local_llm_tier", lambda t: 0)
        monkeypatch.setattr(sc, "_presidio_tier", lambda t: Tier.PRIVATE)
        assert classify("my home address") == Tier.SENSITIVE

    def test_embedding_semantic_flag(self, monkeypatch):
        # Even with no keyword/regex hit, a high-similarity embedding → SENSITIVE.
        monkeypatch.setattr(sc, "_presidio_tier", lambda t: 0)
        monkeypatch.setattr(sc, "_local_llm_tier", lambda t: 0)
        monkeypatch.setattr(sc, "_embedding_tier", lambda t: (Tier.SENSITIVE, 0.9))
        assert classify("something with no obvious keywords") == Tier.SENSITIVE

    def test_layers_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(sc, "_presidio_tier", lambda t: (_ for _ in ()).throw(AssertionError))
        # use_presidio=False must skip the presidio layer entirely (no call).
        assert classify("neutral text", use_presidio=False, use_embeddings=False) == Tier.PUBLIC


class TestLegacyAlias:
    def test_classify_legacy_matches(self, monkeypatch):
        _no_heavy(monkeypatch)
        assert sc.classify_legacy("bank account") == Tier.SENSITIVE
        assert sc.classify_legacy("hello world") == Tier.PUBLIC


class TestGracefulDegradation:
    def test_missing_embedder_returns_zero(self, monkeypatch):
        monkeypatch.setattr(sc, "_load_embedder", lambda: None)
        monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None, raising=False)
        assert sc._embedding_tier("anything") == (0, 0.0)

    def test_missing_presidio_returns_zero(self, monkeypatch):
        monkeypatch.setattr(sc, "_load_presidio", lambda: None)
        assert sc._presidio_tier("anything") == 0

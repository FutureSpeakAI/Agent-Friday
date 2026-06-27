"""Unit tests for the unified sensitivity classifier.

All classification runs locally; no network calls are made.
Tests use synthetic data — no real PII.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services.sensitivity_classifier import (
    Tier,
    classify,
    classify_legacy,
    TIER_3_KEYWORDS,
    TIER_2_KEYWORDS,
    _regex_tier,
    _keyword_tier,
)


# ── Tier constants ─────────────────────────────────────────────────────────────

class TestTierConstants:
    def test_public_is_lowest(self):
        assert Tier.PUBLIC < Tier.PRIVATE < Tier.SENSITIVE

    def test_names_round_trip(self):
        assert Tier.NAMES[Tier.PUBLIC] == "TIER_1"
        assert Tier.NAMES[Tier.PRIVATE] == "TIER_2"
        assert Tier.NAMES[Tier.SENSITIVE] == "TIER_3"


# ── Layer 1a: Regex ───────────────────────────────────────────────────────────

class TestRegexLayer:
    def test_ssn_format_detected(self):
        assert _regex_tier("His SSN is 123-45-6789") == Tier.SENSITIVE  # pragma: allowlist secret

    def test_ssn_space_format(self):
        assert _regex_tier("SSN 123 45 6789") == Tier.SENSITIVE  # pragma: allowlist secret

    def test_api_key_sk_ant_detected(self):
        assert _regex_tier("key=sk-ant-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234") == Tier.SENSITIVE  # pragma: allowlist secret

    def test_api_key_sk_detected(self):
        assert _regex_tier("OPENAI_KEY=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ") == Tier.SENSITIVE  # pragma: allowlist secret

    def test_no_match_public_text(self):
        assert _regex_tier("today is a good day for a walk") == 0

    def test_nine_digit_routing_is_private(self):
        result = _regex_tier("routing 123456789")
        assert result in (Tier.PRIVATE, Tier.SENSITIVE)


# ── Layer 1b: Keywords ────────────────────────────────────────────────────────

class TestKeywordLayer:
    def test_financial_keyword(self):
        assert _keyword_tier("financial records on file") == Tier.SENSITIVE

    def test_ssn_keyword(self):
        assert _keyword_tier("ssn for this person") == Tier.SENSITIVE

    def test_custody_keyword(self):
        assert _keyword_tier("custody hearing scheduled") == Tier.SENSITIVE

    def test_family_keyword_private(self):
        assert _keyword_tier("my family contact") == Tier.PRIVATE

    def test_neutral_text(self):
        assert _keyword_tier("the quick brown fox") == 0

    def test_sensitive_beats_private(self):
        # Text with both tier-2 and tier-3 markers → tier-3 wins
        assert _keyword_tier("family medical diagnosis") == Tier.SENSITIVE

    def test_keywords_list_nonempty(self):
        assert len(TIER_3_KEYWORDS) > 5
        assert len(TIER_2_KEYWORDS) > 3


# ── classify() — integrated behaviour ─────────────────────────────────────────

class TestClassify:
    def test_empty_uses_default(self):
        assert classify("") == Tier.PUBLIC  # default for empty input

    def test_none_uses_default(self):
        assert classify(None) == Tier.PUBLIC  # pragma: no branch

    def test_public_text(self):
        # Generic public content with no signals → PUBLIC (the base default).
        # Fail-closed behaviour kicks in only when embedding similarity is >= 0.50.
        result = classify(
            "the weather forecast for tomorrow",
            use_presidio=False,
            use_embeddings=False,
        )
        assert result == Tier.PUBLIC

    def test_sensitive_keyword_wins(self):
        result = classify(
            "custody hearing and divorce settlement",
            use_presidio=False,
            use_embeddings=False,
        )
        assert result == Tier.SENSITIVE

    def test_financial_keyword_wins(self):
        result = classify(
            "bank account and routing number",
            use_presidio=False,
            use_embeddings=False,
        )
        assert result == Tier.SENSITIVE

    def test_ssn_regex_wins(self):
        result = classify(
            "ID is 123-45-6789",  # pragma: allowlist secret
            use_presidio=False,
            use_embeddings=False,
        )
        assert result == Tier.SENSITIVE

    def test_most_sensitive_wins(self):
        # Mixed content: SENSITIVE overrides PRIVATE
        result = classify(
            "contact info and medical diagnosis",
            use_presidio=False,
            use_embeddings=False,
        )
        assert result == Tier.SENSITIVE


# ── classify_legacy() — backward-compat PUBLIC default ───────────────────────

class TestClassifyLegacy:
    def test_default_is_public_not_private(self):
        # Legacy callers expect PUBLIC default for unknown content.
        result = classify_legacy("the quick brown fox jumps over the lazy dog",
                                 use_presidio=False, use_embeddings=False)
        assert result == Tier.PUBLIC

    def test_sensitive_content_still_escalates(self):
        result = classify_legacy("financial account balance",
                                 use_presidio=False, use_embeddings=False)
        assert result == Tier.SENSITIVE

    def test_none_returns_public(self):
        assert classify_legacy(None) == Tier.PUBLIC


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

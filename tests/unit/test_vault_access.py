"""Unit tests for vault_access — the zero-trust gate that decides what vault
content a cloud provider is ever allowed to see. This is security-critical: a
false 'allow' leaks SSNs / custody data / financials to Anthropic. Every test
uses synthetic data."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import agent_friday.privacy.vault_access as va
from agent_friday.privacy.vault_access import Tier, VaultAccessControl, VaultAccessDenied


@pytest.fixture
def vac():
    return VaultAccessControl(enabled=True)


# ── Provider trust boundary ───────────────────────────────────────────────────
class TestCanAccess:
    @pytest.mark.parametrize("provider", ["ollama", "local", "OLLAMA", "Local"])
    def test_local_providers_allowed(self, vac, provider):
        assert vac.can_access(provider) is True

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "claude", ""])
    def test_cloud_providers_denied(self, vac, provider):
        assert vac.can_access(provider) is False


# ── Tier classification (most-sensitive wins) ─────────────────────────────────
class TestClassify:
    def test_sensitive_keywords(self, vac):
        assert vac.classify("His SSN is on file") == Tier.SENSITIVE
        assert vac.classify("the custody hearing") == Tier.SENSITIVE
        assert vac.classify("financial account balance") == Tier.SENSITIVE

    def test_private_keywords(self, vac):
        assert vac.classify("emergency contact details") == Tier.PRIVATE

    def test_public_default(self, vac):
        assert vac.classify("today's weather forecast") == Tier.PUBLIC
        assert vac.classify("") == Tier.PUBLIC

    def test_none_is_public(self, vac):
        assert vac.classify(None) == Tier.PUBLIC

    def test_sensitive_beats_private(self, vac):
        # Content with BOTH a private and a sensitive marker must classify SENSITIVE.
        assert vac.classify("emergency contact and his ssn") == Tier.SENSITIVE

    def test_custom_default(self, vac):
        assert vac.classify("plain text", default=Tier.PRIVATE) == Tier.PRIVATE


# ── gate_content: the actual leak-prevention logic ────────────────────────────
class TestGateContent:
    def test_local_gets_everything(self, vac):
        secret = "SSN 123-45-6789"  # pragma: allowlist secret
        assert vac.gate_content(secret, "ollama") == secret

    def test_cloud_public_passthrough(self, vac):
        pub = "general public news summary"
        assert vac.gate_content(pub, "anthropic") == pub

    def test_cloud_sensitive_gets_nothing(self, vac):
        out = vac.gate_content("his ssn and custody file", "anthropic")
        assert "123" not in out
        assert out == "" or "vault" in out.lower()

    def test_cloud_private_redacted_not_raw(self, vac):
        raw = "emergency contact: 555-1234"
        out = vac.gate_content(raw, "anthropic")
        assert "555-1234" not in out  # raw private data must not survive

    def test_deny_fallback_raises_on_sensitive(self, vac):
        with pytest.raises(VaultAccessDenied):
            vac.gate_content("financial custody ssn", "anthropic", fallback="deny")

    def test_explicit_tier_overrides_classification(self, vac):
        # Force SENSITIVE even on innocuous text → cloud gets nothing.
        out = vac.gate_content("hello world", "anthropic", tier=Tier.SENSITIVE)
        assert "hello world" not in out


# ── assemble_prompt: multi-section composition ────────────────────────────────
class TestAssemblePrompt:
    # assemble_prompt takes (tier_int, text) tuples.
    def test_local_keeps_all_sections(self, vac):
        sections = [(Tier.PUBLIC, "public note"), (Tier.SENSITIVE, "his ssn 123-45-6789")]  # pragma: allowlist secret
        out = vac.assemble_prompt(sections, "ollama")
        assert "public note" in out
        assert "123-45-6789" in out  # pragma: allowlist secret

    def test_cloud_drops_sensitive_keeps_public(self, vac):
        sections = [(Tier.PUBLIC, "public note"), (Tier.SENSITIVE, "his ssn 123-45-6789")]  # pragma: allowlist secret
        out = vac.assemble_prompt(sections, "anthropic")
        assert "public note" in out          # TIER_1 survives
        assert "123-45-6789" not in out       # TIER_3 dropped  # pragma: allowlist secret

    def test_cloud_private_section_redacted(self, vac):
        sections = [(Tier.PRIVATE, "emergency contact 555-1234")]
        out = vac.assemble_prompt(sections, "anthropic")
        assert "555-1234" not in out

    def test_deny_fallback_raises(self, vac):
        with pytest.raises(VaultAccessDenied):
            vac.assemble_prompt([(Tier.SENSITIVE, "ssn")], "anthropic", fallback="deny")


# ── stats / audit ─────────────────────────────────────────────────────────────
class TestStats:
    def test_stats_shape(self, vac):
        vac.gate_content("his ssn", "anthropic")
        vac.gate_content("public", "ollama")
        s = vac.stats()
        assert isinstance(s, dict)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

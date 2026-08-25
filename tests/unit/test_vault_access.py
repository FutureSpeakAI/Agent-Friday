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


# ── Contact-shaped PII: the 2026-08-25 leak ───────────────────────────────────
# "emergency contact: 555-1234" classified TIER_1 and vault_access.gate_content
# handed it to Anthropic verbatim. The cause was not one missing keyword: a
# phone number, a street address and a masked account tail had NO detector at
# any layer that ships. Presidio (Layer 2) has never been installed anywhere,
# and embeddings (Layer 3) are excluded from the frozen .exe AND switched off
# outright on this path (classify passes use_embeddings=False). So these tests
# deliberately assert against the DETERMINISTIC layers only — they must hold in
# the packaged binary, not just on a dev box with sentence-transformers present.
class TestContactShapedPII:
    @pytest.mark.parametrize("raw", [
        "emergency contact: 555-1234",
        "Call Dana at 555-123-4567",  # pragma: allowlist secret
        "Sarah Chen (312) 555-0199",
        "reach me on +1 312 555 0199",
        "mom: 555.867.5309",  # pragma: allowlist secret
        "babysitter Kayla, 555-2210",
        "I live at 1420 Maple Street, Apt 3B, Chicago IL 60614",
        "ship it to 88 Rowan Ave, Evanston",
    ])
    def test_contact_pii_is_withheld_from_cloud(self, vac, raw):
        out = vac.gate_content(raw, "anthropic")
        digits = [tok for tok in raw.replace(",", " ").split() if any(c.isdigit() for c in tok)]
        for tok in digits:
            assert tok not in out, f"{tok!r} from {raw!r} reached the cloud payload"

    @pytest.mark.parametrize("raw", [
        "Chase account ending 4417",
        "policy number BX-99120384",
        "checking balance is 4,210.55 as of Tuesday",
        "todo: call the accountant about the account balance",
    ])
    def test_financial_shapes_get_nothing(self, vac, raw):
        # TIER_3 → cloud gets the empty string, not a placeholder.
        assert vac.gate_content(raw, "anthropic") == ""

    def test_local_still_gets_the_raw_value(self, vac):
        raw = "emergency contact: 555-1234"
        assert vac.gate_content(raw, "ollama") == raw


# ── Over-correction guards ────────────────────────────────────────────────────
# The opposite failure has bitten this project repeatedly: CDC flu guidance
# withheld as medical records, "courtesy" matching "court", "Sovereign Vault"
# nuking Friday's own system prompt, "family picture-book aesthetic" force-
# routing a storybook prompt onto a local seat that could not fit the payload.
# Tightening the classifier must not undo that work, so it is asserted here.
# These are cloud-path assertions (egress semantics), which is where the
# loosening was done.
class TestNotOverRedacted:
    @pytest.mark.parametrize("benign", [
        "CDC seasonal flu vaccination guidance for adults",
        "Thank you for the courtesy of a quick reply",
        "family picture-book aesthetic, warm palette",
        "nano banana family of models",
        "What is the weather going to be like tomorrow?",
        "Remind me to buy milk on Friday",
        "the crisis hotline is 1-800-273-8255",   # toll-free is never personal  # pragma: allowlist secret
    ])
    def test_benign_text_survives_the_cloud_gate(self, benign):
        from agent_friday.services.sensitivity_classifier import classify, Tier
        tier = classify(benign, egress=True, use_presidio=False, use_embeddings=False)
        assert tier == Tier.PUBLIC, f"over-redacted: {benign!r} rated {Tier.NAMES[tier]}"


# ── Falsifiability ────────────────────────────────────────────────────────────
def test_private_cases_are_falsifiable(vac, monkeypatch):
    """A passing privacy test is only evidence if it could have failed.

    Neutralise the classifier so everything reads PUBLIC — the exact shape of
    "someone broke the gate" — and confirm the assertions above stop holding.
    If this test fails, the tests in this file are vacuous and prove nothing.
    """
    monkeypatch.setattr(va, "_sc_classify_legacy", lambda content, **kw: Tier.PUBLIC)

    leaked = vac.gate_content("emergency contact: 555-1234", "anthropic")

    assert "555-1234" in leaked, (
        "with the classifier neutralised the contact detail should have leaked; "
        "it did not, so these tests are not actually exercising the gate"
    )

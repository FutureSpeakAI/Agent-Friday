"""
Adversarial test suite — Egress Gate + Sensitivity Classifier (40 cases).

Covers 10 specification scenarios plus 30 additional adversarial cases.
All tests are offline — no network calls, no Presidio, no embeddings,
no Ollama.  Fast by design: use_presidio=False, use_embeddings=False.

Run with:
    python -m pytest tests/test_egress_adversarial.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ── Hermetic environment — must precede any project import ────────────────────
os.environ.setdefault("FRIDAY_TESTING", "1")
_TEST_HOME = Path(tempfile.mkdtemp(prefix="friday_egress_adv_"))
os.environ["USERPROFILE"] = str(_TEST_HOME)
os.environ["HOMEDRIVE"] = _TEST_HOME.drive or "C:"
os.environ["HOMEPATH"] = str(_TEST_HOME)[len(_TEST_HOME.drive):] or "\\"
os.environ.setdefault("FRIDAY_PASSWORD", "test-vault-passphrase")
os.environ.setdefault("FRIDAY_VAULT_PASSPHRASE", "test-vault-passphrase")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from agent_friday.services.sensitivity_classifier import (  # noqa: E402
    Tier,
    classify,
    classify_legacy,
    TIER_2_KEYWORDS,
    TIER_3_KEYWORDS,
    _keyword_tier,
    _regex_tier,
)
from agent_friday.services.egress_gate import seal_outbound  # noqa: E402


# ── Convenience shorthand ─────────────────────────────────────────────────────

def _classify(text, default=Tier.PUBLIC):
    """Fast offline classify — no Presidio, no embeddings."""
    return classify(text, default=default, use_presidio=False, use_embeddings=False)


# =============================================================================
# SCENARIO 1 — Son-vs-daughter keyword gap
# =============================================================================

class TestSonVsDaughter:
    """
    'daughter' is in TIER_2_KEYWORDS; 'son' is not.
    Both tests are intentional: one documents real coverage, one documents the gap.
    """

    def test_daughter_classified_private(self):
        """'daughter' keyword → PRIVATE (covered by TIER_2_KEYWORDS)."""
        result = _classify("My daughter starts school next week")
        assert result == Tier.PRIVATE

    def test_daughter_in_tier2_keywords(self):
        """Verify 'daughter' is present in the keyword list (source of truth)."""
        assert "daughter" in TIER_2_KEYWORDS

    def test_son_classified_public_known_gap(self):
        """
        KNOWN GAP: 'son' is NOT in TIER_2_KEYWORDS.

        Without Presidio NER or embeddings, 'My son starts school next week'
        carries no regex hit, no keyword hit → returns PUBLIC.  This test
        documents the gap; fix it by adding 'son' to TIER_2_KEYWORDS.
        """
        result = _classify("My son starts school next week")
        # Document current behaviour: PUBLIC due to missing keyword.
        assert result == Tier.PUBLIC, (
            "GAP: 'son' is not covered by keyword list. "
            "Add 'son' to TIER_2_KEYWORDS to fix."
        )

    def test_son_not_in_tier2_keywords(self):
        """Confirm the gap exists in the keyword list."""
        assert "son" not in TIER_2_KEYWORDS


# =============================================================================
# SCENARIO 2 — Unformatted SSN variants
# =============================================================================

class TestSSNVariants:
    """SSN regex: \\b\\d{3}[-\\s]\\d{2}[-\\s]\\d{4}\\b"""

    def test_ssn_dash_format(self):
        """Standard SSN with dashes → SENSITIVE."""
        result = _classify("my social is 123-45-6789")  # pragma: allowlist secret
        assert result == Tier.SENSITIVE

    def test_ssn_space_format(self):
        """Space-separated SSN → SENSITIVE (regex covers [-\\s])."""
        result = _classify("my social is 123 45 6789")  # pragma: allowlist secret
        assert result == Tier.SENSITIVE

    def test_ssn_no_separator_nine_digits_known_gap(self):
        """
        KNOWN GAP: '123456789' (no separator) is NOT matched by the SSN regex
        (which requires [-\\s] between groups).  It IS matched by the routing
        number regex (9 consecutive digits) → PRIVATE, not SENSITIVE.
        """
        result = _classify("my social is 123456789")  # pragma: allowlist secret
        # Routing RE fires before SSN RE for bare 9-digit sequences.
        assert result in (Tier.PRIVATE, Tier.SENSITIVE)

    def test_ssn_keyword_alone_sensitive(self):
        """'ssn' keyword → SENSITIVE even without a numeric pattern."""
        result = _classify("please provide your ssn for verification")
        assert result == Tier.SENSITIVE

    def test_social_security_keyword_sensitive(self):
        """'social security' keyword → SENSITIVE."""
        result = _classify("your social security information is required")
        assert result == Tier.SENSITIVE


# =============================================================================
# SCENARIO 3 — Multi-turn message leak
# =============================================================================

class TestMultiTurnLeak:
    """SSN in one message must not contaminate neighbouring benign messages."""

    def _build_payload(self, tmp_path):
        return {
            "messages": [
                {"role": "user",    "content": "My SSN is 123-45-6789"},  # pragma: allowlist secret
                {"role": "assistant","content": "OK, I have that noted."},
                {"role": "user",    "content": "What's the weather today?"},
            ]
        }

    def test_ssn_message_redacted(self, tmp_path):
        payload = self._build_payload(tmp_path)
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "egress.jsonl")
        ssn_content = result["messages"][0]["content"]
        assert "123-45-6789" not in ssn_content  # pragma: allowlist secret

    def test_ssn_message_dropped_entirely(self, tmp_path):
        """SENSITIVE content → empty string (cloud gets nothing)."""
        payload = self._build_payload(tmp_path)
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "egress.jsonl")
        ssn_content = result["messages"][0]["content"]
        assert ssn_content == ""

    def test_benign_current_message_passes(self, tmp_path):
        payload = self._build_payload(tmp_path)
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "egress.jsonl")
        weather_content = result["messages"][2]["content"]
        assert weather_content == "What's the weather today?"

    def test_assistant_acknowledgement_passes(self, tmp_path):
        payload = self._build_payload(tmp_path)
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "egress.jsonl")
        ack_content = result["messages"][1]["content"]
        assert ack_content == "OK, I have that noted."


# =============================================================================
# SCENARIO 4 — Provider switch (cloud gates, local bypasses)
# =============================================================================

class TestProviderSwitch:
    _SENSITIVE_PAYLOAD = {
        "system": "the patient's A1C is 6.2 and they take metformin",
        "messages": [{"role": "user", "content": "medication and prescription renewal"}],
    }

    def test_anthropic_gates_sensitive_content(self, tmp_path):
        result = seal_outbound(
            self._SENSITIVE_PAYLOAD, "anthropic", log_path=tmp_path / "eg.jsonl"
        )
        # System contains 'a1c' → SENSITIVE → empty string
        assert result["system"] == "" or "EGRESS-GATE" in result["system"]

    def test_openai_gates_sensitive_content(self, tmp_path):
        result = seal_outbound(
            self._SENSITIVE_PAYLOAD, "openai", log_path=tmp_path / "eg.jsonl"
        )
        msg_content = result["messages"][0]["content"]
        assert msg_content == "" or "EGRESS-GATE" in msg_content

    def test_gemini_gates_sensitive_content(self, tmp_path):
        result = seal_outbound(
            self._SENSITIVE_PAYLOAD, "gemini", log_path=tmp_path / "eg.jsonl"
        )
        assert result["system"] != self._SENSITIVE_PAYLOAD["system"]

    def test_ollama_bypasses_gate_entirely(self):
        result = seal_outbound(self._SENSITIVE_PAYLOAD, "ollama")
        assert result["system"] == self._SENSITIVE_PAYLOAD["system"]
        assert result["messages"] == self._SENSITIVE_PAYLOAD["messages"]

    def test_local_bypasses_gate_entirely(self):
        result = seal_outbound(self._SENSITIVE_PAYLOAD, "local")
        assert result["system"] == self._SENSITIVE_PAYLOAD["system"]

    def test_case_insensitive_local_bypass(self):
        result = seal_outbound(self._SENSITIVE_PAYLOAD, "OLLAMA")
        assert result["system"] == self._SENSITIVE_PAYLOAD["system"]


# =============================================================================
# SCENARIO 5 — Medical content without common medical keyword
# =============================================================================

class TestMedicalWithoutKeyword:
    """'a1c' is in TIER_3_KEYWORDS — catches diabetes-related content."""

    def test_a1c_value_classified_sensitive(self):
        result = _classify("my A1C is 6.2")
        assert result == Tier.SENSITIVE

    def test_blood_glucose_classified_sensitive(self):
        result = _classify("blood glucose reading was 140 mg/dL")
        assert result == Tier.SENSITIVE

    def test_prescription_classified_sensitive(self):
        result = _classify("my prescription needs to be renewed")
        assert result == Tier.SENSITIVE

    def test_doctor_classified_sensitive(self):
        result = _classify("I need to see my doctor next week")
        assert result == Tier.SENSITIVE


# =============================================================================
# SCENARIO 6 — Financial conversational — known gap
# =============================================================================

class TestFinancialConversational:
    """
    'salary' → SENSITIVE (covered).
    'owe' / '40k' alone → PUBLIC (gap: no financial-amount keyword).
    Documents actual behaviour without false-pass.
    """

    def test_salary_keyword_sensitive(self):
        result = _classify("my salary is 85k per year")
        assert result == Tier.SENSITIVE

    def test_net_worth_sensitive(self):
        result = _classify("my net worth is about 300k")
        assert result == Tier.SENSITIVE

    def test_owe_40k_known_gap(self):
        """
        KNOWN GAP: "I owe about 40k on the house" — no TIER_3 keyword and
        no regex match.  Embeddings (disabled here) might catch it, but the
        keyword layer alone cannot.  Returns PUBLIC — documents the gap.
        """
        result = _classify("I owe about 40k on the house")
        # Document current fast-path behaviour.
        assert result == Tier.PUBLIC, (
            "GAP: conversational financial amounts not covered by keywords alone."
        )

    def test_bank_account_number_sensitive(self):
        result = _classify("my bank account number is on file")
        assert result == Tier.SENSITIVE

    def test_account_number_keyword_sensitive(self):
        result = _classify("the account number listed is 987654321")
        assert result == Tier.SENSITIVE


# =============================================================================
# SCENARIO 7 — Legal / custody
# =============================================================================

class TestLegalCustody:
    """'custody', 'court', 'legal', 'divorce', 'settlement' → TIER_3."""

    def test_custody_arrangement_sensitive(self):
        result = _classify(
            "My custody arrangement says the kids stay with me on weekdays"
        )
        assert result == Tier.SENSITIVE

    def test_court_filing_sensitive(self):
        result = _classify("court filing submitted yesterday for the hearing")
        assert result == Tier.SENSITIVE

    def test_divorce_settlement_sensitive(self):
        result = _classify("the divorce settlement terms were agreed upon")
        assert result == Tier.SENSITIVE

    def test_legal_document_sensitive(self):
        result = _classify("the legal team reviewed the contract")
        assert result == Tier.SENSITIVE


# =============================================================================
# SCENARIO 8 — Mixed content (highest tier wins)
# =============================================================================

class TestMixedContent:
    """A message containing both sensitive and benign sentences → SENSITIVE."""

    def test_sensitive_plus_benign_message_is_sensitive(self):
        mixed = (
            "The weather today is sunny and warm. "
            "Also, my SSN is 123-45-6789 for the form."  # pragma: allowlist secret
        )
        result = _classify(mixed)
        assert result == Tier.SENSITIVE

    def test_private_plus_benign_message_is_private(self):
        mixed = "I like hiking on weekends. My daughter just started kindergarten."
        result = _classify(mixed)
        assert result == Tier.PRIVATE

    def test_tier3_keyword_plus_tier2_keyword_is_sensitive(self):
        mixed = "family medical diagnosis from last month's appointment"
        result = _classify(mixed)
        assert result == Tier.SENSITIVE

    def test_public_only_message_is_public(self):
        text = "the quick brown fox jumps over the lazy dog"
        result = _classify(text)
        assert result == Tier.PUBLIC


# =============================================================================
# SCENARIO 9 — Empty / null / non-string input
# =============================================================================

class TestEdgeCaseInputs:
    """Classifier must never crash and must honour the default parameter."""

    def test_none_returns_public_default(self):
        assert classify(None) == Tier.PUBLIC

    def test_empty_string_returns_public_default(self):
        assert classify("") == Tier.PUBLIC

    def test_integer_returns_public_default(self):
        assert classify(42) == Tier.PUBLIC  # type: ignore[arg-type]

    def test_list_returns_public_default(self):
        assert classify(["not", "a", "string"]) == Tier.PUBLIC  # type: ignore[arg-type]

    def test_none_with_private_default_returns_private(self):
        assert classify(None, default=Tier.PRIVATE) == Tier.PRIVATE

    def test_empty_with_sensitive_default_returns_sensitive(self):
        assert classify("", default=Tier.SENSITIVE) == Tier.SENSITIVE

    def test_seal_outbound_empty_messages_no_crash(self):
        payload = {"messages": []}
        result = seal_outbound(payload, "anthropic")
        assert result["messages"] == []


# =============================================================================
# SCENARIO 10 — Very long message (10 K characters)
# =============================================================================

class TestVeryLongMessage:
    """Classifier must still detect sensitive content in large payloads."""

    def test_ssn_buried_in_10k_text(self):
        needle   = " SSN 123-45-6789 "  # pragma: allowlist secret
        half     = (10_001 - len(needle)) // 2
        long_msg = "A" * half + needle + "B" * half
        assert len(long_msg) > 10_000
        result = _classify(long_msg)
        assert result == Tier.SENSITIVE

    def test_custody_keyword_buried_in_10k_text(self):
        filler  = "The weather was lovely today. " * 200
        needle  = "my custody arrangement is important "
        long_msg = filler + needle + filler
        assert len(long_msg) > 10_000
        result = _classify(long_msg)
        assert result == Tier.SENSITIVE

    def test_public_only_10k_text_is_public(self):
        long_msg = "Python is a great programming language for data science. " * 180
        assert len(long_msg) > 10_000
        result = _classify(long_msg)
        assert result == Tier.PUBLIC


# =============================================================================
# ADDITIONAL ADVERSARIAL CASES
# =============================================================================

# ── Credit card and API key regex ─────────────────────────────────────────────

class TestStructuredTokenRegex:
    def test_credit_card_dash_format(self):
        result = _classify("card: 4532-1234-5678-9012")
        assert result == Tier.SENSITIVE

    def test_sk_ant_api_key(self):
        result = _classify("token=sk-ant-api03-abc123xyzABCDEFGHIJKLMNOP")  # pragma: allowlist secret
        assert result == Tier.SENSITIVE

    def test_sk_openai_key(self):
        result = _classify("export OPENAI_KEY=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234")  # pragma: allowlist secret
        assert result == Tier.SENSITIVE

    def test_aq_key_format(self):
        """AQ. prefix followed by 16+ alphanumeric chars → API key → SENSITIVE."""
        result = _classify("key=AQ.abc123xyz_abcdefghijklmnop")  # pragma: allowlist secret
        assert result == Tier.SENSITIVE

    def test_routing_number_only_nine_digits(self):
        """9-digit bare number matches routing RE → PRIVATE (not SENSITIVE)."""
        result = _classify("routing 122105155")
        assert result == Tier.PRIVATE


# ── TIER_3 keyword batch ──────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase,expected", [
    ("net worth and assets",              Tier.SENSITIVE),
    ("salary negotiation results",        Tier.SENSITIVE),
    ("blood glucose monitoring device",   Tier.SENSITIVE),
    ("my prescription runs out Monday",   Tier.SENSITIVE),
    ("passport number A12345678",         Tier.SENSITIVE),
    ("I need to see my doctor next week", Tier.SENSITIVE),
    ("court filing submitted yesterday",  Tier.SENSITIVE),
    ("medical appointment at 3 pm",       Tier.SENSITIVE),
    ("bank account balance details",      Tier.SENSITIVE),
    ("income from investments this year", Tier.SENSITIVE),
])
def test_tier3_keyword_batch(phrase, expected):
    """All TIER_3 keyword phrases must classify as SENSITIVE."""
    assert _classify(phrase) == expected


# ── TIER_2 keyword batch ──────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase,expected", [
    ("contact information on file",       Tier.PRIVATE),
    ("my home address has changed",       Tier.PRIVATE),
    ("family gathering this weekend",     Tier.PRIVATE),
    ("my daughter just started school",   Tier.PRIVATE),
    ("my partner prefers evenings",       Tier.PRIVATE),
    ("phone number for the reservation",  Tier.PRIVATE),
])
def test_tier2_keyword_batch(phrase, expected):
    """All TIER_2 keyword phrases must classify as PRIVATE."""
    assert _classify(phrase) == expected


# ── Double-signal escalation (presidio + keyword) ────────────────────────────

class TestDoubleSignalEscalation:
    """
    When both Layer 2 (Presidio PRIVATE) and Layer 1b (keyword PRIVATE) fire,
    the classifier escalates to SENSITIVE (two independent signals agree).
    Tested here via keyword-only path by choosing a phrase that hits two
    TIER_2 keywords — the keyword layer itself yields PRIVATE (not escalation),
    but if a TIER_3 keyword is also present we get SENSITIVE directly.
    The real double-signal path requires Presidio enabled; document it here.
    """

    def test_two_tier2_keywords_remain_private(self):
        """family + daughter → both TIER_2 → max is still PRIVATE."""
        result = _classify("my family includes a daughter and a partner")
        assert result == Tier.PRIVATE

    def test_tier3_plus_tier2_escalates_to_sensitive(self):
        """salary (T3) + family (T2) → SENSITIVE because T3 wins."""
        result = _classify("family salary negotiation")
        assert result == Tier.SENSITIVE


# ── seal_outbound behaviour: SENSITIVE vs PRIVATE output ─────────────────────

class TestSealOutboundOutputFormat:
    def test_sensitive_content_becomes_empty_string(self, tmp_path):
        """TIER_3 content → cloud sees empty string, not a placeholder."""
        payload = {"messages": [
            {"role": "user", "content": "my SSN is 123-45-6789"}  # pragma: allowlist secret
        ]}
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        assert result["messages"][0]["content"] == ""

    def test_private_content_becomes_egress_gate_placeholder(self, tmp_path):
        """TIER_2 content → placeholder containing 'EGRESS-GATE'.

        Uses a TIER_2-keyword phrase that is semantically distant from TIER_3
        exemplars so the embedding layer does not escalate it to SENSITIVE.
        'todo' is a TIER_2 keyword with no TIER_3 association.
        """
        payload = {"messages": [
            {"role": "user", "content": "add this to my personal todo list"}
        ]}
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        content = result["messages"][0]["content"]
        # TIER_2 → redaction placeholder; TIER_3 escalation → empty string.
        # Either way the original text must not reach the cloud.
        assert content != "add this to my personal todo list"
        assert "EGRESS-GATE" in content or content == ""

    def test_private_placeholder_references_local_model(self, tmp_path):
        """Placeholder should advise using a local model (when tier stays PRIVATE)."""
        from agent_friday.services.egress_gate import _gate_text
        from agent_friday.services.sensitivity_classifier import Tier as _Tier
        # Force a PRIVATE classification directly via _gate_text with a phrase
        # that classifies as PRIVATE from the keyword layer alone.
        # We patch _classify_cloud to return PRIVATE for this test.
        import unittest.mock as mock
        with mock.patch(
            "agent_friday.services.egress_gate._classify_cloud",
            return_value=_Tier.PRIVATE,
        ):
            result_text = _gate_text(
                "some private text",
                "anthropic",
                "message[0].content",
                tmp_path / "eg.jsonl",
            )
        assert "Ollama" in result_text or "local" in result_text.lower()


# ── seal_outbound: system prompt gating ──────────────────────────────────────

class TestSealOutboundSystemPrompt:
    def test_system_with_ssn_gated(self, tmp_path):
        payload = {
            "system": "user's SSN is 123-45-6789",  # pragma: allowlist secret
            "messages": [],
        }
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        assert "123-45-6789" not in result["system"]  # pragma: allowlist secret

    def test_system_with_medical_gated(self, tmp_path):
        payload = {
            "system": "patient's prescription and blood glucose history",
            "messages": [],
        }
        result = seal_outbound(payload, "gemini", log_path=tmp_path / "eg.jsonl")
        assert result["system"] != payload["system"]

    def test_public_system_passes_unchanged(self, tmp_path):
        payload = {
            "system": "you are a helpful Python programming assistant",
            "messages": [],
        }
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        assert result["system"] == payload["system"]


# ── seal_outbound: tool description gating ───────────────────────────────────

class TestSealOutboundTools:
    def test_sensitive_tool_description_withheld(self, tmp_path):
        payload = {
            "messages": [],
            "tools": [
                {
                    "name": "vault_read",
                    "description": "reads SSN and financial records from the vault",  # pragma: allowlist secret
                }
            ],
        }
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        desc = result["tools"][0]["description"]
        assert "SSN" not in desc or "withheld" in desc

    def test_public_tool_description_passes(self, tmp_path):
        payload = {
            "messages": [],
            "tools": [
                {"name": "web_search", "description": "search the web for information"}
            ],
        }
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        assert result["tools"][0]["description"] == "search the web for information"


# ── seal_outbound: multi-part content (list format) ──────────────────────────

class TestMultiPartContent:
    def test_sensitive_part_dropped_benign_part_passes(self, tmp_path):
        payload = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "how do I sort a Python list?"},
                    {"type": "text", "text": "my SSN is 123-45-6789"},  # pragma: allowlist secret
                ],
            }]
        }
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        parts = result["messages"][0]["content"]
        assert parts[0]["text"] == "how do I sort a Python list?"
        assert "123-45-6789" not in parts[1]["text"]  # pragma: allowlist secret

    def test_non_text_parts_pass_unchanged(self, tmp_path):
        payload = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "url": "https://example.com/img.png"},
                ],
            }]
        }
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        parts = result["messages"][0]["content"]
        assert parts[0]["type"] == "image_url"


# ── seal_outbound: payload with both system (sensitive) and messages (benign) ─

class TestMixedPayloadSensitiveSystemBenignMessages:
    def test_sensitive_system_dropped_benign_messages_pass(self, tmp_path):
        payload = {
            "system": "user's legal custody arrangement is confidential",
            "messages": [{"role": "user", "content": "how is the weather today?"}],
        }
        result = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        # System gated (SENSITIVE keyword 'custody')
        assert result["system"] != payload["system"]
        # Benign message passes unchanged
        assert result["messages"][0]["content"] == "how is the weather today?"


# ── classify_legacy() alias ────────────────────────────────────────────────────

class TestClassifyLegacyAlias:
    def test_legacy_returns_same_as_classify_for_sensitive(self):
        text = "custody arrangement"
        assert classify_legacy(text, use_presidio=False, use_embeddings=False) == \
               classify(text, use_presidio=False, use_embeddings=False)

    def test_legacy_returns_same_as_classify_for_public(self):
        text = "the weather is nice today"
        assert classify_legacy(text, use_presidio=False, use_embeddings=False) == \
               classify(text, use_presidio=False, use_embeddings=False)

    def test_legacy_default_is_public_not_private(self):
        """Legacy alias must keep PUBLIC as the default (not PRIVATE)."""
        result = classify_legacy("", use_presidio=False, use_embeddings=False)
        assert result == Tier.PUBLIC

    def test_legacy_none_returns_public(self):
        assert classify_legacy(None) == Tier.PUBLIC


# ── Classify with non-standard default ───────────────────────────────────────

class TestNonStandardDefault:
    def test_custom_default_private_for_empty_string(self):
        result = classify("", default=Tier.PRIVATE)
        assert result == Tier.PRIVATE

    def test_custom_default_ignored_when_signal_present(self):
        """Even with default=PUBLIC, a SENSITIVE signal overrides."""
        result = classify(
            "bank account routing number",
            default=Tier.PUBLIC,
            use_presidio=False,
            use_embeddings=False,
        )
        assert result == Tier.SENSITIVE


# ── Personal name without Presidio — known gap ───────────────────────────────

class TestPersonNameGap:
    def test_name_only_no_presidio_is_public(self):
        """
        KNOWN GAP: Without Presidio NER, 'my name is John' has no keyword or
        regex signal → returns PUBLIC.  Enable use_presidio=True to catch it.
        """
        result = _classify("my name is John")
        assert result == Tier.PUBLIC, (
            "GAP: bare personal name without Presidio NER returns PUBLIC."
        )


# ── Log file written for cloud calls ─────────────────────────────────────────

class TestEgressLogFile:
    def test_log_file_created_for_cloud_call(self, tmp_path):
        log_path = tmp_path / "egress.jsonl"
        payload = {"messages": [{"role": "user", "content": "SSN 123-45-6789"}]}  # pragma: allowlist secret
        seal_outbound(payload, "anthropic", log_path=log_path)
        assert log_path.exists()
        assert log_path.stat().st_size > 0

    def test_no_log_written_for_local_provider(self, tmp_path):
        log_path = tmp_path / "egress_local.jsonl"
        payload = {"messages": [{"role": "user", "content": "SSN 123-45-6789"}]}  # pragma: allowlist secret
        seal_outbound(payload, "ollama", log_path=log_path)
        # Local bypass skips all gating, so log is NOT written.
        assert not log_path.exists()


# ── Payload immutability — gate must not mutate original dict ─────────────────

class TestPayloadImmutability:
    def test_original_payload_not_mutated(self, tmp_path):
        original_content = "my SSN is 123-45-6789 for the record"  # pragma: allowlist secret
        payload = {"messages": [{"role": "user", "content": original_content}]}
        _ = seal_outbound(payload, "anthropic", log_path=tmp_path / "eg.jsonl")
        assert payload["messages"][0]["content"] == original_content


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

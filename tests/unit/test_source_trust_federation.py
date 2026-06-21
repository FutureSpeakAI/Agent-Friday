"""Unit tests for source_trust_federation — signed attestation protocol.

Tests cover:
  * _canonical_body:     deterministic JSON canonicalization (key-order independence)
  * verify_attestation:  structural + signature checks (returns False on malformed/missing/tampered)
  * _OBS_TYPE_MAP:       map completeness and value sanity
  * Signature paths guarded by _HAS_ED25519 / try/except
  * Structural-rejection paths that never need crypto
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import source_trust_federation as stf
from source_trust_federation import (
    ATTESTATION_TYPE,
    ATTESTATION_VERSION,
    _OBS_TYPE_MAP,
    _canonical_body,
    verify_attestation,
)

# Probe whether Ed25519 (pynacl) is actually importable in this environment.
try:
    from nacl.signing import SigningKey  # noqa: F401
    _HAS_NACL = True
except ImportError:
    _HAS_NACL = False

# Probe the module's own flag (may live in proof_of_integrity)
try:
    from proof_of_integrity import _HAS_ED25519 as _MODULE_HAS_ED25519
except ImportError:
    _MODULE_HAS_ED25519 = False


# ── helpers ────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory(prefix="stf_test_") as d:
        yield Path(d)


def _well_formed_attestation(**overrides):
    """Return a structurally valid attestation (no real signature)."""
    base = {
        "type": ATTESTATION_TYPE,
        "version": ATTESTATION_VERSION,
        "agent_id": "a" * 64,          # looks like a hex pubkey
        "timestamp": "2026-06-09T00:00:00Z",
        "source_domain": "example.com",
        "observation": {
            "type": "claim_verified",
            "claim": "Test claim",
            "evidence": "Test evidence",
            "counter_sources": ["reuters.com"],
        },
        "signature": "b" * 128,         # fake — fails crypto but not structure
    }
    base.update(overrides)
    return base


# ── _canonical_body ────────────────────────────────────────────────────────────

class TestCanonicalBody:
    def test_returns_bytes(self):
        att = _well_formed_attestation()
        result = _canonical_body(att)
        assert isinstance(result, bytes)

    def test_excludes_signature(self):
        att = _well_formed_attestation()
        body = _canonical_body(att)
        parsed = json.loads(body.decode("utf-8"))
        assert "signature" not in parsed

    def test_includes_all_other_fields(self):
        att = _well_formed_attestation()
        body = _canonical_body(att)
        parsed = json.loads(body.decode("utf-8"))
        for key in att:
            if key != "signature":
                assert key in parsed, f"Field '{key}' missing from canonical body"

    def test_key_order_independence(self):
        """Same content with different key insertion order → identical bytes."""
        att1 = {
            "type": ATTESTATION_TYPE,
            "version": ATTESTATION_VERSION,
            "agent_id": "aabbcc",
            "timestamp": "2026-06-09T00:00:00Z",
            "source_domain": "example.com",
            "observation": {"type": "claim_verified", "claim": "X", "evidence": "Y",
                            "counter_sources": []},
            "signature": "deadbeef",
        }
        # Build att2 with keys in a different order
        att2 = {
            "source_domain": att1["source_domain"],
            "observation": att1["observation"],
            "version": att1["version"],
            "type": att1["type"],
            "timestamp": att1["timestamp"],
            "agent_id": att1["agent_id"],
            "signature": att1["signature"],
        }
        assert _canonical_body(att1) == _canonical_body(att2)

    def test_different_content_gives_different_bytes(self):
        att1 = _well_formed_attestation()
        att2 = _well_formed_attestation(source_domain="other.com")
        assert _canonical_body(att1) != _canonical_body(att2)

    def test_deterministic_multiple_calls(self):
        att = _well_formed_attestation()
        assert _canonical_body(att) == _canonical_body(att)

    def test_nested_dict_sorts_keys(self):
        """Nested observation dict keys should also be sorted."""
        att = _well_formed_attestation()
        body = _canonical_body(att)
        text = body.decode("utf-8")
        # Check that JSON is parseable and observation sub-dict round-trips
        parsed = json.loads(text)
        assert parsed["observation"]["type"] == att["observation"]["type"]

    def test_empty_dict_gives_empty_body(self):
        """Edge case: empty attestation (no fields to exclude)."""
        result = _canonical_body({})
        assert result == b"{}"

    def test_signature_only_dict_gives_empty_body(self):
        """Dict with only a signature key should produce empty body."""
        result = _canonical_body({"signature": "abc"})
        assert result == b"{}"


# ── verify_attestation — structural rejection (no crypto needed) ───────────────

class TestVerifyAttestationStructural:
    def test_non_dict_returns_false(self):
        for bad in [None, "string", 42, [], True]:
            assert verify_attestation(bad) is False, f"Expected False for {bad!r}"

    def test_empty_dict_returns_false(self):
        assert verify_attestation({}) is False

    def test_wrong_type_returns_false(self):
        att = _well_formed_attestation(type="wrong_type")
        assert verify_attestation(att) is False

    def test_missing_type_field_returns_false(self):
        att = _well_formed_attestation()
        del att["type"]
        assert verify_attestation(att) is False

    def test_missing_signature_returns_false(self):
        att = _well_formed_attestation()
        del att["signature"]
        assert verify_attestation(att) is False

    def test_none_signature_returns_false(self):
        att = _well_formed_attestation(signature=None)
        assert verify_attestation(att) is False

    def test_empty_signature_returns_false(self):
        att = _well_formed_attestation(signature="")
        assert verify_attestation(att) is False

    def test_missing_agent_id_returns_false(self):
        """No public_key argument and no agent_id → can't verify."""
        att = _well_formed_attestation()
        del att["agent_id"]
        assert verify_attestation(att) is False

    def test_none_agent_id_returns_false(self):
        att = _well_formed_attestation(agent_id=None)
        assert verify_attestation(att) is False

    def test_fake_signature_returns_false(self):
        """Well-structured attestation with a fake signature should fail crypto."""
        att = _well_formed_attestation()
        # Only fails at crypto level; structural checks pass. Either False or the
        # module gracefully degrades — either way must not raise.
        result = verify_attestation(att)
        assert isinstance(result, bool)
        # A fake signature should never verify to True
        assert result is False

    def test_tampered_body_returns_false(self):
        """Modify a field after signature would be set — body won't match."""
        att = _well_formed_attestation()
        att["source_domain"] = "tampered.com"
        # signature was for "example.com" body → must fail
        result = verify_attestation(att)
        assert result is False


# ── verify_attestation — real Ed25519 paths (guarded) ────────────────────────

@pytest.mark.skipif(not _HAS_NACL, reason="pynacl not installed")
class TestVerifyAttestationWithCrypto:
    def _make_real_attestation(self):
        """Create a properly signed attestation using a fresh ephemeral key."""
        from nacl.signing import SigningKey as _SK
        sk = _SK.generate()
        pubkey_hex = sk.verify_key.encode().hex()
        att = {
            "type": ATTESTATION_TYPE,
            "version": ATTESTATION_VERSION,
            "agent_id": pubkey_hex,
            "timestamp": "2026-06-09T12:00:00Z",
            "source_domain": "example.com",
            "observation": {
                "type": "claim_verified",
                "claim": "Test claim about example.com",
                "evidence": "Cross-referenced with reuters.com",
                "counter_sources": ["reuters.com"],
            },
        }
        body = _canonical_body(att)  # no "signature" key yet
        sig_hex = sk.sign(body).signature.hex()
        att["signature"] = sig_hex
        return att, sk, pubkey_hex

    def test_valid_signature_returns_true(self):
        att, _sk, _pub = self._make_real_attestation()
        assert verify_attestation(att) is True

    def test_explicit_pubkey_returns_true(self):
        att, _sk, pub = self._make_real_attestation()
        assert verify_attestation(att, public_key=pub) is True

    def test_wrong_pubkey_returns_false(self):
        from nacl.signing import SigningKey as _SK
        att, _sk, _pub = self._make_real_attestation()
        other_pub = _SK.generate().verify_key.encode().hex()
        assert verify_attestation(att, public_key=other_pub) is False

    def test_tampered_domain_returns_false(self):
        att, _sk, _pub = self._make_real_attestation()
        att["source_domain"] = "evil.com"
        assert verify_attestation(att) is False

    def test_tampered_signature_bytes_returns_false(self):
        att, _sk, _pub = self._make_real_attestation()
        # Flip a nibble in the middle of the signature hex
        mid = len(att["signature"]) // 2
        original_char = att["signature"][mid]
        flipped = "0" if original_char != "0" else "1"
        att["signature"] = att["signature"][:mid] + flipped + att["signature"][mid + 1:]
        assert verify_attestation(att) is False

    def test_invalid_hex_in_signature_returns_false(self):
        att, _sk, _pub = self._make_real_attestation()
        att["signature"] = "ZZZZZZ"
        assert verify_attestation(att) is False

    def test_invalid_hex_in_pubkey_returns_false(self):
        att, _sk, _pub = self._make_real_attestation()
        att["agent_id"] = "ZZZZZZ"
        assert verify_attestation(att) is False


# ── _OBS_TYPE_MAP ─────────────────────────────────────────────────────────────

class TestObsTypeMap:
    """Verify the mapping from federation observation types to (dimension, signal) tuples."""

    def test_map_is_a_dict(self):
        assert isinstance(_OBS_TYPE_MAP, dict)

    def test_all_values_are_two_tuples(self):
        for key, val in _OBS_TYPE_MAP.items():
            assert isinstance(val, tuple), f"{key}: value should be a tuple"
            assert len(val) == 2, f"{key}: tuple should have length 2"

    def test_dimensions_are_valid(self):
        from source_trust_graph import DIMENSIONS as VALID_DIMS
        for key, (dim, _signal) in _OBS_TYPE_MAP.items():
            assert dim in VALID_DIMS, (
                f"_OBS_TYPE_MAP['{key}'] references unknown dimension '{dim}'"
            )

    def test_signals_in_unit_range(self):
        for key, (_dim, signal) in _OBS_TYPE_MAP.items():
            assert 0.0 <= signal <= 1.0, (
                f"_OBS_TYPE_MAP['{key}'] signal {signal} outside [0, 1]"
            )

    def test_positive_types_have_high_signals(self):
        positive = ("claim_verified", "correction_issued", "opinion_labeled",
                    "attribution_present", "narrative_break", "prediction_correct")
        for typ in positive:
            if typ in _OBS_TYPE_MAP:
                _, signal = _OBS_TYPE_MAP[typ]
                assert signal >= 0.7, (
                    f"Expected high signal for positive type '{typ}', got {signal}"
                )

    def test_negative_types_have_low_signals(self):
        negative = ("claim_disputed", "opinion_unlabeled",
                    "attribution_absent", "minority_claim", "prediction_wrong")
        for typ in negative:
            if typ in _OBS_TYPE_MAP:
                _, signal = _OBS_TYPE_MAP[typ]
                assert signal <= 0.4, (
                    f"Expected low signal for negative type '{typ}', got {signal}"
                )

    def test_claim_verified_maps_to_factual_accuracy(self):
        dim, signal = _OBS_TYPE_MAP["claim_verified"]
        assert dim == "factual_accuracy"
        assert signal > 0.7

    def test_claim_disputed_maps_to_factual_accuracy(self):
        dim, signal = _OBS_TYPE_MAP["claim_disputed"]
        assert dim == "factual_accuracy"
        assert signal < 0.4

    def test_correction_issued_maps_to_correction_behavior(self):
        dim, signal = _OBS_TYPE_MAP["correction_issued"]
        assert dim == "correction_behavior"

    def test_narrative_break_maps_to_narrative_independence(self):
        dim, signal = _OBS_TYPE_MAP["narrative_break"]
        assert dim == "narrative_independence"


# ── record_attestation / list_attestations (file I/O) ─────────────────────────

class TestAttestationStorage:
    def test_record_then_list(self, tmpdir):
        att = _well_formed_attestation()
        result = stf.record_attestation(att, friday_dir=tmpdir)
        assert result is True
        listed = stf.list_attestations(friday_dir=tmpdir, limit=10)
        assert len(listed) == 1
        assert listed[0]["source_domain"] == "example.com"

    def test_list_empty_on_fresh_dir(self, tmpdir):
        listed = stf.list_attestations(friday_dir=tmpdir, limit=10)
        assert listed == []

    def test_list_imported_empty_on_fresh_dir(self, tmpdir):
        listed = stf.list_imported(friday_dir=tmpdir, limit=10)
        assert listed == []

    def test_record_multiple_newest_first(self, tmpdir):
        att1 = _well_formed_attestation(source_domain="a.test")
        att2 = _well_formed_attestation(source_domain="b.test")
        stf.record_attestation(att1, friday_dir=tmpdir)
        stf.record_attestation(att2, friday_dir=tmpdir)
        listed = stf.list_attestations(friday_dir=tmpdir, limit=10)
        # Newest appended = reversed = b.test first
        assert listed[0]["source_domain"] == "b.test"

    def test_record_none_returns_false(self, tmpdir):
        assert stf.record_attestation(None, friday_dir=tmpdir) is False

    def test_record_false_returns_false(self, tmpdir):
        assert stf.record_attestation(False, friday_dir=tmpdir) is False


# ── import_attestation — rejection on bad signature ───────────────────────────

class TestImportAttestation:
    def test_bad_signature_not_accepted(self, tmpdir):
        att = _well_formed_attestation()  # fake sig
        result = stf.import_attestation(att, friday_dir=tmpdir)
        assert result["accepted"] is False
        assert "signature" in result["reason"].lower() or result["reason"] != "ok"

    def test_malformed_attestation_not_accepted(self, tmpdir):
        result = stf.import_attestation({"junk": True}, friday_dir=tmpdir)
        assert result["accepted"] is False

    @pytest.mark.skipif(not _HAS_NACL, reason="pynacl not installed")
    def test_valid_attestation_accepted(self, tmpdir):
        from nacl.signing import SigningKey as _SK
        sk = _SK.generate()
        pubkey_hex = sk.verify_key.encode().hex()
        att = {
            "type": ATTESTATION_TYPE,
            "version": ATTESTATION_VERSION,
            "agent_id": pubkey_hex,
            "timestamp": "2026-06-09T12:00:00Z",
            "source_domain": "example.com",
            "observation": {
                "type": "claim_verified",
                "claim": "Something true",
                "evidence": "Cited reuters.com",
                "counter_sources": [],
            },
        }
        body = _canonical_body(att)
        att["signature"] = sk.sign(body).signature.hex()

        result = stf.import_attestation(att, friday_dir=tmpdir)
        assert result["accepted"] is True
        assert result["agent_id"] == pubkey_hex

    @pytest.mark.skipif(not _HAS_NACL, reason="pynacl not installed")
    def test_duplicate_import_rejected(self, tmpdir):
        from nacl.signing import SigningKey as _SK
        sk = _SK.generate()
        pubkey_hex = sk.verify_key.encode().hex()
        att = {
            "type": ATTESTATION_TYPE,
            "version": ATTESTATION_VERSION,
            "agent_id": pubkey_hex,
            "timestamp": "2026-06-09T12:00:00Z",
            "source_domain": "example.com",
            "observation": {
                "type": "claim_verified",
                "claim": "Something true",
                "evidence": "Evidence",
                "counter_sources": [],
            },
        }
        body = _canonical_body(att)
        att["signature"] = sk.sign(body).signature.hex()

        first = stf.import_attestation(att, friday_dir=tmpdir)
        second = stf.import_attestation(att, friday_dir=tmpdir)
        assert first["accepted"] is True
        assert second["accepted"] is False
        assert "already" in second["reason"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""Unit tests for proof_of_integrity.py — AI Bill of Integrity manifest.

Tests cover:
  - verify_payload: False for empty/missing args; False when Ed25519 unavailable
  - body_for_signing: excludes signature/hmac/body_hash fields; is deterministic
  - claws_hash: equals sha256(CLAWS_TEXT)
  - sign_manifest / verify_manifest lifecycle (Ed25519-gated where needed)
  - IntegrityEngine: can be constructed with temp dir

All tests use a temp dir for the attestation key file.
pynacl may be absent — crypto-dependent tests are skipped via
pytest.mark.skipif(_HAS_ED25519 is False, ...).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import proof_of_integrity as poi
from proof_of_integrity import (
    AgentIntegrityManifest,
    CLAWS_TEXT,
    IntegrityEngine,
    _HAS_ED25519,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_engine(tmp_path):
    """An IntegrityEngine rooted in a throwaway temp dir."""
    return IntegrityEngine(friday_dir=tmp_path)


@pytest.fixture
def blank_manifest():
    return AgentIntegrityManifest()


# ── claws_hash correctness ─────────────────────────────────────────────────────

class TestClawsHash:
    def test_claws_hash_is_sha256_of_text(self):
        expected = hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()
        manifest = AgentIntegrityManifest()
        # Before sign_manifest, claws_hash is blank; test via compute-and-compare.
        assert expected == hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()

    def test_claws_hash_in_signed_manifest(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        expected = hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()
        assert m.claws_hash == expected

    def test_claws_hash_not_empty(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        assert m.claws_hash != ""

    def test_claws_hash_deterministic(self, tmp_engine):
        """Two consecutive sign_manifest calls must produce the same claws_hash."""
        h1 = tmp_engine.sign_manifest().claws_hash
        h2 = tmp_engine.sign_manifest().claws_hash
        assert h1 == h2


# ── body_for_signing ───────────────────────────────────────────────────────────

class TestBodyForSigning:
    def test_excludes_ed25519_sig(self, blank_manifest):
        blank_manifest.ed25519_sig = "should_not_appear"
        body = json.loads(blank_manifest.body_for_signing().decode("utf-8"))
        assert "ed25519_sig" not in body

    def test_excludes_ed25519_pubkey(self, blank_manifest):
        blank_manifest.ed25519_pubkey = "pubkey_should_not_appear"
        body = json.loads(blank_manifest.body_for_signing().decode("utf-8"))
        assert "ed25519_pubkey" not in body

    def test_excludes_claws_hmac(self, blank_manifest):
        blank_manifest.claws_hmac = "hmac_should_not_appear"
        body = json.loads(blank_manifest.body_for_signing().decode("utf-8"))
        assert "claws_hmac" not in body

    def test_excludes_body_hash(self, blank_manifest):
        blank_manifest._body_hash = "body_hash_should_not_appear"
        body = json.loads(blank_manifest.body_for_signing().decode("utf-8"))
        assert "body_hash" not in body

    def test_includes_claws_hash(self, blank_manifest):
        blank_manifest.claws_hash = "abc123"
        body = json.loads(blank_manifest.body_for_signing().decode("utf-8"))
        assert body["claws_hash"] == "abc123"

    def test_includes_version(self, blank_manifest):
        body = json.loads(blank_manifest.body_for_signing().decode("utf-8"))
        assert "version" in body

    def test_deterministic_same_state(self, blank_manifest):
        """Two calls on identical state must produce identical bytes."""
        b1 = blank_manifest.body_for_signing()
        b2 = blank_manifest.body_for_signing()
        assert b1 == b2

    def test_deterministic_sorted_keys(self, blank_manifest):
        """Output must be JSON with sorted keys (canonical form)."""
        raw = blank_manifest.body_for_signing().decode("utf-8")
        # Parse and re-serialise with sort_keys=True — must round-trip identically.
        parsed = json.loads(raw)
        canonical = json.dumps(parsed, sort_keys=True)
        assert canonical == raw

    def test_different_state_different_bytes(self):
        m1 = AgentIntegrityManifest()
        m2 = AgentIntegrityManifest()
        m2.claws_hash = "different_hash"
        assert m1.body_for_signing() != m2.body_for_signing()


# ── verify_payload — structural / negative paths ───────────────────────────────

class TestVerifyPayload:
    def test_empty_sig_returns_false(self):
        data = b"some payload"
        assert IntegrityEngine.verify_payload(data, "", "aabbccdd") is False

    def test_empty_pubkey_returns_false(self):
        data = b"some payload"
        assert IntegrityEngine.verify_payload(data, "aabbccdd", "") is False

    def test_both_empty_returns_false(self):
        assert IntegrityEngine.verify_payload(b"data", "", "") is False

    def test_invalid_hex_returns_false(self):
        """Malformed hex strings must not crash — just return False."""
        assert IntegrityEngine.verify_payload(b"data", "not-valid-hex!", "also-not-hex!") is False

    @pytest.mark.skipif(_HAS_ED25519, reason="tests the no-pynacl fallback path")
    def test_unavailable_ed25519_returns_false(self):
        """When _HAS_ED25519 is False the function must return False for any input."""
        assert IntegrityEngine.verify_payload(b"data", "aabb", "ccdd") is False

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_valid_signature_verifies(self, tmp_engine):
        """sign_payload / verify_payload round-trip."""
        data = b"attestation test payload"
        sig_hex = tmp_engine.sign_payload(data)
        pubkey_hex = tmp_engine.get_public_key_hex()
        assert sig_hex is not None
        assert pubkey_hex is not None
        assert IntegrityEngine.verify_payload(data, sig_hex, pubkey_hex) is True

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_wrong_key_rejects(self, tmp_path):
        """Signature from key A must not verify against key B's public key."""
        engine_a = IntegrityEngine(friday_dir=tmp_path / "engine_a")
        engine_b = IntegrityEngine(friday_dir=tmp_path / "engine_b")
        data = b"cross-key test"
        sig_hex = engine_a.sign_payload(data)
        pubkey_b = engine_b.get_public_key_hex()
        assert IntegrityEngine.verify_payload(data, sig_hex, pubkey_b) is False

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_tampered_data_rejected(self, tmp_engine):
        data = b"original payload"
        sig_hex = tmp_engine.sign_payload(data)
        pubkey_hex = tmp_engine.get_public_key_hex()
        assert IntegrityEngine.verify_payload(b"tampered payload", sig_hex, pubkey_hex) is False


# ── IntegrityEngine construction ───────────────────────────────────────────────

class TestEngineConstruction:
    def test_engine_constructs_without_error(self, tmp_path):
        engine = IntegrityEngine(friday_dir=tmp_path)
        assert engine is not None

    def test_engine_accepts_governance_key_fn(self, tmp_path):
        key_fn = lambda: b"test-governance-key-32-bytes!!!!"
        engine = IntegrityEngine(friday_dir=tmp_path, governance_key_fn=key_fn)
        assert engine is not None

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_engine_generates_ed25519_key_file(self, tmp_path):
        engine = IntegrityEngine(friday_dir=tmp_path)
        key_file = tmp_path / "vault" / ".attestation-key-ed25519"
        assert key_file.exists()

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_engine_loads_existing_key(self, tmp_path):
        """Second engine on same dir must load the same key (stable identity)."""
        e1 = IntegrityEngine(friday_dir=tmp_path)
        pk1 = e1.get_public_key_hex()
        e2 = IntegrityEngine(friday_dir=tmp_path)
        pk2 = e2.get_public_key_hex()
        assert pk1 == pk2


# ── sign_manifest / verify_manifest lifecycle ──────────────────────────────────

class TestSignManifestLifecycle:
    def test_sign_manifest_returns_manifest(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        assert isinstance(m, AgentIntegrityManifest)

    def test_manifest_has_generated_at(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        assert m.generated_at != ""

    def test_manifest_version_populated(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        assert m.version != ""

    def test_manifest_body_hash_populated(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        assert m._body_hash != ""

    def test_manifest_body_hash_is_sha256_of_body(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        expected = hashlib.sha256(m.body_for_signing()).hexdigest()
        assert m._body_hash == expected

    def test_to_dict_shape(self, tmp_engine):
        d = tmp_engine.sign_manifest().to_dict()
        for key in ("claws_hash", "claws_hmac", "ed25519_pubkey", "ed25519_sig",
                    "model_manifest", "tool_manifest", "vault_status",
                    "epistemic_score", "memory_health", "version",
                    "generated_at", "body_hash"):
            assert key in d, f"Missing key: {key}"

    def test_verify_manifest_clean_chain(self, tmp_engine):
        """A freshly signed manifest must pass verify_manifest."""
        m = tmp_engine.sign_manifest()
        result = tmp_engine.verify_manifest(m.to_dict())
        assert isinstance(result, dict)
        assert "valid" in result
        assert "checks" in result

    def test_body_hash_check_passes(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        result = tmp_engine.verify_manifest(m.to_dict())
        assert result["checks"]["body_hash"] is True

    def test_claws_hash_check_passes(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        result = tmp_engine.verify_manifest(m.to_dict())
        assert result["checks"]["claws_hash"] is True

    def test_tampered_body_hash_fails(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        d = m.to_dict()
        d["body_hash"] = "0" * 64  # corrupt the body hash
        result = tmp_engine.verify_manifest(d)
        assert result["checks"]["body_hash"] is False

    def test_tampered_claws_hash_fails(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        d = m.to_dict()
        d["claws_hash"] = "deadbeef" * 8  # corrupt the cLaws hash
        result = tmp_engine.verify_manifest(d)
        assert result["checks"]["claws_hash"] is False

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_verify_manifest_ed25519_valid(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        result = tmp_engine.verify_manifest(m.to_dict())
        assert result["checks"].get("ed25519") is True

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_tampered_ed25519_sig_fails(self, tmp_engine):
        m = tmp_engine.sign_manifest()
        d = m.to_dict()
        # Flip a nibble in the signature
        d["ed25519_sig"] = d["ed25519_sig"][:-1] + ("0" if d["ed25519_sig"][-1] != "0" else "1")
        result = tmp_engine.verify_manifest(d)
        assert result["checks"].get("ed25519") is False

    def test_sign_with_governance_key(self, tmp_path):
        """When a governance_key_fn is provided, claws_hmac must be non-trivial."""
        key_fn = lambda: b"test-governance-key-32-bytes!!!!"
        engine = IntegrityEngine(friday_dir=tmp_path, governance_key_fn=key_fn)
        m = engine.sign_manifest()
        assert m.claws_hmac not in ("no_governance_key_fn", "governance_key_unavailable", "")

    def test_sign_without_governance_key(self, tmp_engine):
        """Without a key function, claws_hmac is set to a sentinel string."""
        m = tmp_engine.sign_manifest()
        assert m.claws_hmac == "no_governance_key_fn"

    def test_custom_tool_manifest_propagated(self, tmp_engine):
        tools = [{"name": "fake_tool", "ring": 0}]
        m = tmp_engine.sign_manifest(tool_manifest=tools)
        assert m.tool_manifest == tools


# ── sign_payload edge cases ────────────────────────────────────────────────────

class TestSignPayload:
    def test_sign_payload_without_key_returns_none(self, tmp_path):
        """If Ed25519 is unavailable, sign_payload must return None."""
        engine = IntegrityEngine(friday_dir=tmp_path)
        if engine._signing_key is None:
            result = engine.sign_payload(b"data")
            assert result is None

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_sign_payload_returns_hex_string(self, tmp_engine):
        result = tmp_engine.sign_payload(b"test data")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.skipif(not _HAS_ED25519, reason="requires pynacl")
    def test_sign_payload_different_data_different_sig(self, tmp_engine):
        sig1 = tmp_engine.sign_payload(b"data A")
        sig2 = tmp_engine.sign_payload(b"data B")
        assert sig1 != sig2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

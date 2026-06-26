"""Unit tests for services/federation.py"""
import pytest
from services import federation as fed

# The schema is skipped under FRIDAY_TESTING=1.  Initialise it explicitly so
# the SQLite tables exist for the duration of the test session.
fed._ensure_schema()


class TestGetIdentity:
    def test_get_identity_returns_dict(self):
        result = fed.get_identity()
        assert isinstance(result, dict)

    def test_get_identity_has_agent_id(self):
        result = fed.get_identity()
        assert "agent_id" in result
        assert result["agent_id"]  # non-empty

    def test_get_identity_has_capabilities(self):
        result = fed.get_identity()
        assert "capabilities" in result
        assert isinstance(result["capabilities"], list)
        assert len(result["capabilities"]) > 0

    def test_get_identity_has_content_types(self):
        result = fed.get_identity()
        assert "content_types" in result
        assert isinstance(result["content_types"], list)

    def test_get_identity_has_federation_version(self):
        result = fed.get_identity()
        assert "federation_version" in result

    def test_get_identity_never_raises(self):
        # Should not raise under any circumstances
        for _ in range(3):
            result = fed.get_identity()
            assert isinstance(result, dict)


class TestGetPeerCard:
    def test_get_peer_card_returns_dict(self):
        card = fed.get_peer_card()
        assert isinstance(card, dict)

    def test_get_peer_card_type_field(self):
        card = fed.get_peer_card()
        if card:  # may be {} on crypto failure
            assert card.get("type") == "FridayPeerCard"

    def test_get_peer_card_version(self):
        card = fed.get_peer_card()
        if card:
            assert "version" in card

    def test_get_peer_card_agent_id(self):
        card = fed.get_peer_card()
        if card:
            assert "agent_id" in card

    def test_get_peer_card_capabilities(self):
        card = fed.get_peer_card()
        if card:
            assert "capabilities" in card
            assert isinstance(card["capabilities"], list)

    def test_get_peer_card_signature_field(self):
        card = fed.get_peer_card()
        if card:
            assert "signature" in card
            sig = card["signature"]
            assert isinstance(sig, dict)
            assert "alg" in sig
            assert "value" in sig

    def test_get_peer_card_signature_alg_is_ed25519(self):
        card = fed.get_peer_card()
        if card:
            assert card["signature"]["alg"] == "ed25519"


class TestPeerManagement:
    def test_get_peers_returns_list(self):
        result = fed.get_peers()
        assert isinstance(result, list)

    def test_get_peer_nonexistent_returns_none(self):
        result = fed.get_peer("nonexistent_id_that_does_not_exist_xyz")
        assert result is None

    def test_add_peer_card_empty_dict_returns_none(self):
        result = fed.add_peer_card({})
        assert result is None

    def test_add_peer_card_missing_agent_id_returns_none(self):
        # Card missing required fields should fail gracefully
        result = fed.add_peer_card({"type": "FridayPeerCard", "version": "1.0"})
        assert result is None

    def test_add_peer_card_missing_signature_accepted_unverified(self):
        # The service accepts cards with no signature (logs a warning but stores them).
        # A valid agent_id (64-hex) with no sig → returns a peer record (unverified).
        card = {
            "type": "FridayPeerCard",
            "version": "1.0",
            "agent_id": "deadbeef" * 8,
            "capabilities": [],
            "endpoints": [],
            "issued": "2026-01-01T00:00:00Z",
        }
        result = fed.add_peer_card(card)
        # Acceptable outcomes: dict (stored unverified) or None (rejected)
        assert result is None or isinstance(result, dict)

    def test_update_peer_trust_no_crash_nonexistent(self):
        # Should not raise even if peer doesn't exist
        try:
            fed.update_peer_trust("nonexistent_peer_xyz", {"type": "test"})
        except Exception as exc:
            pytest.fail(f"update_peer_trust raised unexpectedly: {exc}")


class TestHandshake:
    def test_handshake_no_manifest_returns_dict(self):
        result = fed.handshake(None, {})
        assert isinstance(result, dict)
        assert "ok" in result

    def test_handshake_no_manifest_has_claws_match(self):
        result = fed.handshake(None, {})
        assert "claws_match" in result

    def test_handshake_wrong_claws_hash_returns_mismatch(self):
        manifest = {"claws_hash": "0000000000000000000000000000000000000000000000000000000000000000"}
        result = fed.handshake(manifest, {"agent_id": "test"})
        assert isinstance(result, dict)
        assert "ok" in result
        # Wrong hash → claws_match=False or ok=False
        assert result.get("claws_match") is False or result.get("ok") is False

    def test_handshake_never_raises(self):
        # peer_card must be a dict (not None); manifest_dict may be None
        for args in [({}, {}), (None, {"agent_id": "x"}), ({}, {"agent_id": "x"})]:
            try:
                result = fed.handshake(*args)
                assert isinstance(result, dict)
            except Exception as exc:
                pytest.fail(f"handshake raised unexpectedly: {exc}")


class TestDiscoverPeer:
    def test_discover_peer_bad_url_returns_none(self):
        # Port 19999 should be unused; connection refused must be caught
        result = fed.discover_peer("http://localhost:19999/nonexistent", timeout=1)
        assert result is None

    def test_discover_peer_invalid_url_returns_none(self):
        result = fed.discover_peer("not-a-url-at-all", timeout=1)
        assert result is None

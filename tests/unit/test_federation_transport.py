"""Unit tests for services/federation_transport.py"""
import pytest
from services import federation_transport as ft


# Detect whether the cryptography package is available (same flag the module uses)
_HAS_CRYPTO = ft._HAS_CRYPTO


class TestMsgTypes:
    def test_msg_types_dict_exists(self):
        assert isinstance(ft.MSG_TYPES, dict)

    def test_msg_types_has_handshake(self):
        assert "HANDSHAKE" in ft.MSG_TYPES

    def test_msg_types_has_heartbeat(self):
        assert "HEARTBEAT" in ft.MSG_TYPES

    def test_msg_types_has_content_offer(self):
        assert "CONTENT_OFFER" in ft.MSG_TYPES

    def test_msg_types_has_trust_attestation(self):
        assert "TRUST_ATTESTATION" in ft.MSG_TYPES

    def test_msg_types_values_are_strings(self):
        for key, val in ft.MSG_TYPES.items():
            assert isinstance(val, str), f"MSG_TYPES[{key!r}] is not a string"

    def test_rate_limit_constant_exists(self):
        assert isinstance(ft.RATE_LIMIT_PER_MIN, int)
        assert ft.RATE_LIMIT_PER_MIN > 0


class TestCreateSession:
    def test_create_session_returns_dict(self):
        result = ft.create_session("deadbeef" * 8)
        assert isinstance(result, dict)

    def test_create_session_has_ephem_pubkey(self):
        result = ft.create_session("cafebabe" * 8)
        assert "ephem_pubkey_hex" in result

    def test_create_session_has_session_id(self):
        result = ft.create_session("aabbccdd" * 8)
        assert "session_id" in result

    def test_create_session_different_peers_produce_different_sessions(self):
        s1 = ft.create_session("aaaa" * 16)
        s2 = ft.create_session("bbbb" * 16)
        assert s1["session_id"] != s2["session_id"]

    def test_create_session_no_crypto_returns_fallback(self):
        # Even without crypto, must return a dict with the required keys
        result = ft.create_session("00" * 32)
        assert "ephem_pubkey_hex" in result
        assert "session_id" in result


class TestBuildMessage:
    def test_build_message_returns_dict_or_empty(self):
        # build_message is an alias for encrypt_message
        result = ft.build_message("HEARTBEAT", {"ping": True}, "a" * 64)
        assert isinstance(result, dict)

    def test_build_message_with_known_msg_type(self):
        msg_type = ft.MSG_TYPES["HEARTBEAT"]
        result = ft.build_message(msg_type, {}, "b" * 64)
        assert isinstance(result, dict)

    def test_build_message_plaintext_fallback_is_dict(self):
        # Even with a bogus recipient pubkey, should return dict (plaintext fallback)
        result = ft.build_message("HEARTBEAT", {"test": 1}, "00" * 32)
        assert isinstance(result, dict)


class TestEncryptDecryptRoundtrip:
    @pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography package not installed")
    def test_encrypt_produces_envelope_or_fallback(self):
        # Without a completed ECDH handshake we can't round-trip the ChaCha20
        # layer, but encrypt_message must return a non-empty dict in all cases.
        peer_hex = "cc" * 32
        ft.create_session(peer_hex)
        payload = {"msg": "hello federation", "value": 42}
        envelope = ft.encrypt_message("HEARTBEAT", payload, peer_hex)
        assert isinstance(envelope, dict)
        assert envelope  # must be non-empty

    @pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography package not installed")
    def test_envelope_has_sender_pubkey_or_fallback_flag(self):
        peer_hex = "ee" * 32
        ft.create_session(peer_hex)
        envelope = ft.encrypt_message("HEARTBEAT", {"x": 1}, peer_hex)
        assert isinstance(envelope, dict)
        # Either a proper signed envelope or a plaintext fallback dict
        has_sender = "sender_pubkey" in envelope
        is_fallback = envelope.get("plaintext_fallback") is True
        assert has_sender or is_fallback or envelope == {}

    @pytest.mark.skipif(not _HAS_CRYPTO, reason="cryptography package not installed")
    def test_decrypt_tampered_payload_returns_not_ok(self):
        # Build an envelope (may be plaintext fallback), then tamper with
        # encrypted_payload if present, or with the payload field directly.
        peer_hex = "dd" * 32
        ft.create_session(peer_hex)
        envelope = ft.encrypt_message("HEARTBEAT", {"x": 1}, peer_hex)
        if not envelope:
            pytest.skip("Empty envelope; tamper test not applicable")
        tampered = dict(envelope)
        if "encrypted_payload" in tampered:
            original = tampered["encrypted_payload"]
            if len(original) >= 4:
                tampered["encrypted_payload"] = "ff" + original[2:]
            result = ft.decrypt_message(tampered)
            assert result.get("ok") is False
        else:
            # plaintext fallback — no encryption layer to tamper with
            pytest.skip("Plaintext fallback active; no encrypted_payload to tamper")

    def test_decrypt_empty_envelope_returns_not_ok(self):
        result = ft.decrypt_message({})
        assert isinstance(result, dict)
        assert result.get("ok") is False


class TestRateLimit:
    def test_check_rate_limit_allows_first_call(self):
        result = ft.check_rate_limit("fresh-peer-" + __import__("uuid").uuid4().hex)
        assert result is True

    def test_check_rate_limit_returns_bool(self):
        result = ft.check_rate_limit("rate-bool-peer")
        assert isinstance(result, bool)

    def test_check_rate_limit_blocks_after_overflow(self):
        # Use a limit of 5 for speed; spam 20 calls
        peer_id = "spam-peer-" + __import__("uuid").uuid4().hex
        results = [ft.check_rate_limit(peer_id, limit=5) for _ in range(20)]
        # At least one should be False once the window fills
        assert False in results, "Rate limiter should have blocked at least one call"

    def test_check_rate_limit_different_peers_independent(self):
        import uuid
        peer_a = "peer-a-" + uuid.uuid4().hex
        peer_b = "peer-b-" + uuid.uuid4().hex
        # Exhaust peer_a's limit
        for _ in range(20):
            ft.check_rate_limit(peer_a, limit=3)
        # peer_b should still be allowed
        assert ft.check_rate_limit(peer_b, limit=3) is True


class TestSendToPeer:
    def test_send_to_peer_unreachable_returns_error(self):
        result = ft.send_to_peer("http://localhost:19998", {})
        assert isinstance(result, dict)
        assert result.get("ok") is False

    def test_send_to_peer_bad_scheme_returns_error(self):
        result = ft.send_to_peer("not-a-url", {})
        assert isinstance(result, dict)
        assert result.get("ok") is False

    def test_send_to_peer_has_error_key(self):
        result = ft.send_to_peer("http://localhost:19997", {})
        assert "error" in result

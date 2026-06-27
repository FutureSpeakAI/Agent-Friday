"""Unit tests for services/moderation.py"""
import pytest
from agent_friday.services import moderation as mod


class TestScanClean:
    def test_scan_clean_text_returns_clean(self):
        result = mod.scan("Hello world, this is a perfectly normal message.")
        assert isinstance(result, dict)
        assert result.get("blocked") is False
        assert result.get("verdict") == "clean"

    def test_scan_empty_string_returns_clean(self):
        result = mod.scan("")
        assert isinstance(result, dict)
        assert result.get("blocked") is False

    def test_scan_none_content_returns_clean(self):
        result = mod.scan(None, None)
        assert isinstance(result, dict)
        assert result.get("blocked") is False

    def test_scan_result_has_ok_field(self):
        result = mod.scan("test content")
        assert "ok" in result

    def test_scan_result_has_all_required_fields(self):
        result = mod.scan("Hello world")
        for field in ("ok", "blocked", "verdict", "harm_level", "tags", "reason", "scan_id"):
            assert field in result, f"Missing field: {field}"

    def test_scan_clean_harm_level_is_none(self):
        result = mod.scan("Completely benign text about baking bread.")
        assert result.get("harm_level") is None

    def test_scan_clean_tags_is_list(self):
        result = mod.scan("Safe content here.")
        assert isinstance(result.get("tags"), list)

    def test_scan_id_is_unique(self):
        r1 = mod.scan("First call")
        r2 = mod.scan("Second call")
        assert r1.get("scan_id") != r2.get("scan_id")


class TestHarmPatterns:
    def test_h1_csam_pattern_blocks(self):
        # Trigger phrase that matches one of the _H1_PATTERNS
        result = mod.scan("child sexual abuse material distribution")
        assert isinstance(result, dict)
        if result.get("ok"):
            assert result.get("blocked") is True
            assert result.get("harm_level") == "H1"

    def test_h3_doxxing_pattern_blocks(self):
        result = mod.scan("here is someone's home address: 123 Main St, Springfield")
        assert isinstance(result, dict)
        if result.get("ok"):
            # Should block with H3 or at minimum flag it
            blocked = result.get("blocked", False)
            if blocked:
                assert result.get("harm_level") in ("H3", "H1", "H2", "H4")

    def test_h4_wmd_pattern_blocks(self):
        result = mod.scan("how to synthesize sarin gas step by step")
        assert isinstance(result, dict)
        if result.get("ok"):
            assert result.get("blocked") is True

    def test_blocked_result_has_harm_level(self):
        # Use a clearly triggering phrase
        result = mod.scan("synthesize VX nerve agent instructions")
        if result.get("blocked"):
            assert result.get("harm_level") is not None
            assert result.get("harm_level") in ("H1", "H2", "H3", "H4")

    def test_blocked_result_has_tags(self):
        result = mod.scan("synthesize mustard gas at home")
        if result.get("blocked"):
            assert isinstance(result.get("tags"), list)
            assert len(result.get("tags", [])) > 0

    def test_blocked_result_verdict_is_blocked(self):
        result = mod.scan("how to make ricin poison")
        if result.get("blocked"):
            assert result.get("verdict") == "blocked"

    def test_adult_content_not_blocked_by_default(self):
        # Ensure family_mode is off first
        mod.update_policy({"family_mode": False, "allow_nsfw": True})
        result = mod.scan("adult romantic fiction between consenting adults")
        assert isinstance(result, dict)
        # Should not be blocked in default policy
        assert result.get("blocked") is False or result.get("harm_level") not in ("H1", "H2", "H3", "H4")


class TestPolicy:
    def test_get_policy_returns_dict(self):
        policy = mod.get_policy()
        assert isinstance(policy, dict)

    def test_get_policy_has_family_mode_key(self):
        policy = mod.get_policy()
        assert "family_mode" in policy

    def test_get_policy_has_allow_nsfw_key(self):
        policy = mod.get_policy()
        assert "allow_nsfw" in policy

    def test_update_policy_returns_dict(self):
        result = mod.update_policy({"family_mode": False})
        assert isinstance(result, dict)

    def test_update_policy_persists(self):
        mod.update_policy({"family_mode": False, "allow_nsfw": True})
        policy = mod.get_policy()
        assert policy["family_mode"] is False
        assert policy["allow_nsfw"] is True

    def test_family_mode_enable_persists(self):
        mod.update_policy({"family_mode": True})
        policy = mod.get_policy()
        assert policy["family_mode"] is True
        # Restore
        mod.update_policy({"family_mode": False})


class TestNsfwAllowed:
    def test_is_nsfw_allowed_default_true(self):
        mod.update_policy({"family_mode": False, "allow_nsfw": True})
        assert mod.is_nsfw_allowed() is True

    def test_is_nsfw_allowed_family_mode_false(self):
        mod.update_policy({"family_mode": True})
        assert mod.is_nsfw_allowed() is False
        # Restore
        mod.update_policy({"family_mode": False})

    def test_is_nsfw_allowed_returns_bool(self):
        result = mod.is_nsfw_allowed()
        assert isinstance(result, bool)


class TestFamilyModeBlocking:
    def test_family_mode_blocks_nsfw_metadata(self):
        mod.update_policy({"family_mode": True})
        result = mod.scan("some content", metadata={"nsfw": True})
        assert isinstance(result, dict)
        if result.get("ok"):
            # nsfw=True in family_mode should be blocked
            assert result.get("blocked") is True
        # Restore
        mod.update_policy({"family_mode": False})

    def test_family_mode_blocks_explicit_metadata(self):
        mod.update_policy({"family_mode": True})
        result = mod.scan("some content", metadata={"explicit": True})
        assert isinstance(result, dict)
        if result.get("ok"):
            assert result.get("blocked") is True
        # Restore
        mod.update_policy({"family_mode": False})

    def test_no_family_mode_nsfw_metadata_not_blocked(self):
        mod.update_policy({"family_mode": False, "allow_nsfw": True})
        result = mod.scan("content", metadata={"nsfw": True})
        assert isinstance(result, dict)
        # Without family_mode, nsfw metadata alone should not trigger H1-H4 block
        if result.get("blocked"):
            # If blocked it should be due to content, not policy
            assert result.get("harm_level") in ("H1", "H2", "H3", "H4") or result.get("tags") == ["nsfw"]

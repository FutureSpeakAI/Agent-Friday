"""Unit tests for services/content_policies.py"""
import pytest
from agent_friday.services import content_policies as cp

cp._ensure_schema()


# ─────────────────────────────────────────────────────────────────────────────
#  BUILT-IN PACKS
# ─────────────────────────────────────────────────────────────────────────────

class TestBuiltinPacks:
    def test_asimov_standard_exists(self):
        pack = cp.get_pack("asimov-standard")
        assert pack is not None

    def test_asimov_standard_always_on(self):
        pack = cp.get_pack("asimov-standard")
        assert pack["always_on"] is True

    def test_family_safe_exists(self):
        assert cp.get_pack("family-safe") is not None

    def test_creator_commons_exists(self):
        assert cp.get_pack("creator-commons") is not None

    def test_journalism_exists(self):
        assert cp.get_pack("journalism") is not None

    def test_all_builtins_have_rules(self):
        for p in cp.BUILTIN_PACKS:
            fetched = cp.get_pack(p["pack_id"])
            assert fetched is not None
            assert isinstance(fetched.get("rules"), list)
            assert len(fetched["rules"]) > 0

    def test_asimov_blocks_h1_h4(self):
        pack = cp.get_pack("asimov-standard")
        cats = {r["category"] for r in pack["rules"]}
        assert "CSAM" in cats
        assert "real_person_deepfake" in cats
        assert "doxxing" in cats
        assert "violence_incitement" in cats

    def test_asimov_standard_is_subscribed_by_default(self):
        subscribed_ids = {p["pack_id"] for p in cp.get_subscribed_packs()}
        assert "asimov-standard" in subscribed_ids

    def test_available_packs_contains_all_builtins(self):
        available_ids = {p["pack_id"] for p in cp.get_available_packs()}
        for p in cp.BUILTIN_PACKS:
            assert p["pack_id"] in available_ids


# ─────────────────────────────────────────────────────────────────────────────
#  SUBSCRIPTION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

class TestSubscription:
    def test_subscribe_family_safe(self):
        ok = cp.subscribe("family-safe")
        assert ok is True
        assert any(p["pack_id"] == "family-safe" for p in cp.get_subscribed_packs())

    def test_unsubscribe_family_safe(self):
        cp.subscribe("family-safe")
        ok = cp.unsubscribe("family-safe")
        assert ok is True

    def test_cannot_unsubscribe_asimov_standard(self):
        ok = cp.unsubscribe("asimov-standard")
        assert ok is False
        assert any(p["pack_id"] == "asimov-standard" for p in cp.get_subscribed_packs())

    def test_cannot_unsubscribe_always_on_pack(self):
        # asimov-standard is the only always-on built-in
        ok = cp.unsubscribe(cp.ALWAYS_ON_PACK)
        assert ok is False

    def test_subscribe_unknown_pack_fails(self):
        ok = cp.subscribe("pack-that-does-not-exist-xyz")
        assert ok is False

    def test_subscribe_idempotent(self):
        cp.subscribe("creator-commons")
        cp.subscribe("creator-commons")  # double subscribe — should not error
        subs = [p["pack_id"] for p in cp.get_subscribed_packs()]
        assert subs.count("creator-commons") == 1  # only once

    def test_available_packs_is_list(self):
        packs = cp.get_available_packs()
        assert isinstance(packs, list)
        assert len(packs) >= 4  # at least the 4 built-ins

    def test_subscribed_packs_is_list(self):
        packs = cp.get_subscribed_packs()
        assert isinstance(packs, list)
        assert len(packs) >= 1


# ─────────────────────────────────────────────────────────────────────────────
#  PACK CREATION
# ─────────────────────────────────────────────────────────────────────────────

class TestCreatePack:
    def test_creates_pack_with_valid_rules(self):
        pack = cp.create_pack(
            name="Test Pack",
            description="A test pack",
            rules=[
                {"category": "violence", "action": "WARN",
                 "severity_threshold": 0.5, "description": "Warn on violence"},
            ],
        )
        assert pack is not None
        assert pack["name"] == "Test Pack"
        assert pack["builtin"] is False

    def test_created_pack_is_retrievable(self):
        pack = cp.create_pack(
            name="Retrievable Pack",
            description="",
            rules=[{"category": "nsfw", "action": "TAG",
                    "severity_threshold": 0.0, "description": "Tag NSFW"}],
        )
        assert pack is not None
        fetched = cp.get_pack(pack["pack_id"])
        assert fetched is not None
        assert fetched["pack_id"] == pack["pack_id"]

    def test_created_pack_rules_preserved(self):
        rules = [
            {"category": "gore", "action": "BLOCK", "severity_threshold": 0.7,
             "description": "Block gore"},
            {"category": "spam", "action": "TAG", "severity_threshold": 0.0,
             "description": "Tag spam"},
        ]
        pack = cp.create_pack(name="Multi-rule Pack", description="", rules=rules)
        assert pack is not None
        assert len(pack["rules"]) == 2

    def test_invalid_action_filtered_out(self):
        pack = cp.create_pack(
            name="Bad Action Pack",
            description="",
            rules=[
                {"category": "nsfw", "action": "INVALID_ACTION",
                 "severity_threshold": 0.0, "description": "bad"},
                {"category": "violence", "action": "WARN",
                 "severity_threshold": 0.3, "description": "ok"},
            ],
        )
        assert pack is not None
        assert len(pack["rules"]) == 1
        assert pack["rules"][0]["category"] == "violence"

    def test_all_valid_actions_accepted(self):
        for action in ("BLOCK", "TAG", "WARN", "ALLOW"):
            pack = cp.create_pack(
                name=f"{action} Pack",
                description="",
                rules=[{"category": "test", "action": action,
                        "severity_threshold": 0.0, "description": "test"}],
            )
            assert pack is not None

    def test_returns_none_without_name(self):
        result = cp.create_pack(name="", description="", rules=[
            {"category": "x", "action": "BLOCK", "severity_threshold": 0.0,
             "description": "y"}
        ])
        assert result is None

    def test_returns_none_without_rules(self):
        result = cp.create_pack(name="No Rules Pack", description="", rules=[])
        assert result is None

    def test_returns_none_with_all_invalid_rules(self):
        result = cp.create_pack(
            name="All Bad Rules",
            description="",
            rules=[{"category": "x", "action": "BOGUS", "severity_threshold": 0.0,
                    "description": "y"}],
        )
        assert result is None

    def test_severity_threshold_clamped(self):
        pack = cp.create_pack(
            name="Threshold Clamp Pack",
            description="",
            rules=[{"category": "x", "action": "BLOCK",
                    "severity_threshold": 99.0, "description": "y"}],
        )
        assert pack is not None
        assert pack["rules"][0]["severity_threshold"] <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
#  CONTENT EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateContent:
    def _clean_packs(self):
        """Return only asimov-standard to isolate tests."""
        return [cp.get_pack("asimov-standard")]

    def test_clean_content_returns_clean(self):
        result = cp.evaluate_content(
            {"title": "A beautiful sunset", "description": "Nice photo"},
            subscribed_packs=self._clean_packs(),
        )
        assert result["blocked"] is False
        assert result["verdict"] == "clean"

    def test_returns_required_fields(self):
        result = cp.evaluate_content({"title": "test"}, subscribed_packs=[])
        for f in ("blocked", "verdict", "applied_packs", "tags", "warnings",
                  "blocking_rule", "reason"):
            assert f in result, f"missing field: {f}"

    def test_nsfw_category_tagged_by_creator_commons(self):
        pack = cp.get_pack("creator-commons")
        result = cp.evaluate_content(
            {"title": "adult content", "categories": ["nsfw"], "nsfw": True},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack],
        )
        assert "nsfw" in result["tags"]
        assert result["blocked"] is False

    def test_nsfw_blocked_by_family_safe(self):
        result = cp.evaluate_content(
            {"categories": ["nsfw"], "nsfw": True, "severity": 0.0},
            subscribed_packs=[cp.get_pack("asimov-standard"), cp.get_pack("family-safe")],
        )
        assert result["blocked"] is True
        assert result["verdict"] == "blocked"

    def test_marketplace_paid_blocked_by_family_safe(self):
        result = cp.evaluate_content(
            {"categories": ["marketplace_paid"], "price_mpsi": 10000, "severity": 0.0},
            subscribed_packs=[cp.get_pack("asimov-standard"), cp.get_pack("family-safe")],
        )
        assert result["blocked"] is True

    def test_price_mpsi_adds_marketplace_paid_category(self):
        pack = cp.get_pack("family-safe")
        result = cp.evaluate_content(
            {"title": "Premium item", "price_mpsi": 5000, "severity": 0.0},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack],
        )
        assert result["blocked"] is True

    def test_free_item_not_blocked_by_family_safe(self):
        pack = cp.get_pack("family-safe")
        result = cp.evaluate_content(
            {"title": "Free art", "price_mpsi": 0, "severity": 0.0},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack],
        )
        assert result["blocked"] is False

    def test_warn_below_threshold_not_triggered(self):
        pack = cp.get_pack("family-safe")
        result = cp.evaluate_content(
            {"categories": ["violence"], "severity": 0.1},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack],
        )
        assert result["blocked"] is False
        # No warning because severity 0.1 < threshold 0.3
        assert len(result["warnings"]) == 0

    def test_warn_at_threshold_triggered(self):
        pack = cp.get_pack("family-safe")
        result = cp.evaluate_content(
            {"categories": ["violence"], "severity": 0.5},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack],
        )
        assert len(result["warnings"]) >= 1
        assert result["verdict"] in ("warned", "tagged")

    def test_block_wins_over_tag(self):
        block_pack = cp.create_pack(
            name="Block Violence", description="",
            rules=[{"category": "violence", "action": "BLOCK",
                    "severity_threshold": 0.0, "description": "block"}]
        )
        tag_pack = cp.create_pack(
            name="Tag Violence", description="",
            rules=[{"category": "violence", "action": "TAG",
                    "severity_threshold": 0.0, "description": "tag"}]
        )
        assert block_pack and tag_pack
        result = cp.evaluate_content(
            {"categories": ["violence"], "severity": 0.5},
            subscribed_packs=[cp.get_pack("asimov-standard"), block_pack, tag_pack],
        )
        assert result["blocked"] is True

    def test_allow_does_not_block(self):
        allow_pack = cp.create_pack(
            name="Allow All Pack", description="",
            rules=[{"category": "nsfw", "action": "ALLOW",
                    "severity_threshold": 0.0, "description": "allow"}]
        )
        assert allow_pack
        result = cp.evaluate_content(
            {"categories": ["nsfw"], "nsfw": True, "severity": 0.0},
            subscribed_packs=[cp.get_pack("asimov-standard"), allow_pack],
        )
        assert result["blocked"] is False

    def test_tags_are_additive_across_packs(self):
        pack_a = cp.create_pack(
            name="Pack A Tags", description="",
            rules=[{"category": "nsfw", "action": "TAG",
                    "severity_threshold": 0.0, "description": "tag nsfw"}]
        )
        pack_b = cp.create_pack(
            name="Pack B Tags", description="",
            rules=[{"category": "violence", "action": "TAG",
                    "severity_threshold": 0.0, "description": "tag violence"}]
        )
        assert pack_a and pack_b
        result = cp.evaluate_content(
            {"categories": ["nsfw", "violence"], "severity": 0.5},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack_a, pack_b],
        )
        assert "nsfw" in result["tags"]
        assert "violence" in result["tags"]

    def test_empty_metadata_doesnt_crash(self):
        result = cp.evaluate_content({})
        assert isinstance(result, dict)
        assert "blocked" in result

    def test_h1_content_blocked_by_harm_floor(self):
        # The harm floor is checked via moderation.scan — for unit isolation,
        # just verify that explicit H1 category text triggers the floor.
        result = cp.evaluate_content(
            {"title": "csam", "description": "child sexual abuse material"},
            subscribed_packs=[cp.get_pack("asimov-standard")],
        )
        assert result["blocked"] is True
        assert result["applied_packs"] == ["asimov-standard"]

    def test_journalism_pack_allows_political_speech(self):
        pack = cp.get_pack("journalism")
        result = cp.evaluate_content(
            {"categories": ["political_speech"], "severity": 0.8},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack],
        )
        # political_speech → ALLOW in journalism pack
        assert result["blocked"] is False

    def test_journalism_pack_blocks_high_confidence_unverified(self):
        pack = cp.get_pack("journalism")
        result = cp.evaluate_content(
            {"categories": ["unverified_claim"], "severity": 0.9},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack],
        )
        assert result["blocked"] is True

    def test_applied_packs_populated(self):
        pack = cp.get_pack("creator-commons")
        result = cp.evaluate_content(
            {"categories": ["nsfw"], "nsfw": True},
            subscribed_packs=[cp.get_pack("asimov-standard"), pack],
        )
        assert "creator-commons" in result["applied_packs"]

    def test_subscribed_packs_default_used_when_none(self):
        # Call with no packs argument — should use DB subscribed packs
        result = cp.evaluate_content({"title": "hello world"})
        assert isinstance(result, dict)
        assert "blocked" in result


# ─────────────────────────────────────────────────────────────────────────────
#  ALWAYS_ON CONSTANT
# ─────────────────────────────────────────────────────────────────────────────

class TestAlwaysOnConstant:
    def test_always_on_is_asimov_standard(self):
        assert cp.ALWAYS_ON_PACK == "asimov-standard"

    def test_asimov_pack_cannot_be_unsubscribed_by_name(self):
        ok = cp.unsubscribe(cp.ALWAYS_ON_PACK)
        assert ok is False

    def test_asimov_pack_not_removed_from_subscribed_after_attempted_unsubscribe(self):
        cp.unsubscribe(cp.ALWAYS_ON_PACK)
        subs = {p["pack_id"] for p in cp.get_subscribed_packs()}
        assert cp.ALWAYS_ON_PACK in subs

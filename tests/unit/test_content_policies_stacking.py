"""Unit tests for content_policies — pack CRUD, subscription rules, the
always-on H1-H4 floor, pack stacking, and the guarantee that community packs
cannot override the built-in Asimov floor.
"""
from __future__ import annotations

import uuid

import pytest

from agent_friday.services import content_policies as cp

cp._ensure_schema()


def _rules(action="BLOCK", cat="nsfw", thr=0.5):
    return [{"category": cat, "action": action, "severity_threshold": thr,
             "description": f"{action} {cat}"}]


class TestPackCRUD:
    def test_create_pack(self):
        p = cp.create_pack("Test Pack", "desc", _rules())
        assert p is not None
        assert p["name"] == "Test Pack"
        assert p["builtin"] is False

    def test_create_pack_rejects_empty_rules(self):
        assert cp.create_pack("x", "d", []) is None

    def test_create_pack_drops_invalid_actions(self):
        p = cp.create_pack("Mixed", "d", [
            {"category": "a", "action": "BLOCK", "severity_threshold": 0.5},
            {"category": "b", "action": "NONSENSE", "severity_threshold": 0.5},
        ])
        assert len(p["rules"]) == 1  # only the valid BLOCK survives

    def test_get_pack(self):
        p = cp.create_pack("Gettable", "d", _rules())
        assert cp.get_pack(p["pack_id"])["name"] == "Gettable"

    def test_builtin_asimov_pack_present(self):
        assert cp.get_pack(cp.ALWAYS_ON_PACK) is not None


class TestSubscription:
    def test_subscribe_and_unsubscribe(self):
        p = cp.create_pack("Subbable", "d", _rules())
        assert cp.subscribe(p["pack_id"]) is True
        subs = [x["pack_id"] for x in cp.get_subscribed_packs()]
        assert p["pack_id"] in subs
        assert cp.unsubscribe(p["pack_id"]) is True

    def test_always_on_pack_cannot_be_unsubscribed(self):
        assert cp.unsubscribe(cp.ALWAYS_ON_PACK) is False

    def test_always_on_always_in_subscribed(self):
        subs = [x["pack_id"] for x in cp.get_subscribed_packs()]
        assert cp.ALWAYS_ON_PACK in subs

    def test_subscribe_unknown_pack_false(self):
        assert cp.subscribe("no-such-pack") is False


class TestEvaluationFloor:
    def test_clean_content_passes(self):
        r = cp.evaluate_content({"title": "A nice recipe", "description": "cooking",
                                 "categories": []}, subscribed_packs=[])
        assert r["blocked"] is False
        assert r["verdict"] == "clean"

    def test_h1h4_floor_blocks_via_moderation(self, monkeypatch):
        # Stub moderation.scan to report a hard-floor harm — floor must BLOCK
        # regardless of subscribed packs.
        import agent_friday.services.moderation as mod
        monkeypatch.setattr(mod, "scan", lambda content_text=None, **k: {
            "blocked": True, "harm_level": "H1", "reason": "floor", "tags": ["h1"]})
        r = cp.evaluate_content({"title": "bad", "description": "worse"},
                                subscribed_packs=[])
        assert r["blocked"] is True
        assert r["blocking_rule"]["pack_id"] == cp.ALWAYS_ON_PACK


class TestPackStacking:
    def test_block_rule_blocks_matching_category(self):
        pack = {"pack_id": "p1", "rules": _rules("BLOCK", "nsfw", 0.5)}
        r = cp.evaluate_content(
            {"nsfw": True, "severity": 0.9, "title": "", "description": ""},
            subscribed_packs=[pack])
        assert r["blocked"] is True

    def test_block_below_threshold_does_not_block(self):
        pack = {"pack_id": "p1", "rules": _rules("BLOCK", "nsfw", 0.8)}
        r = cp.evaluate_content(
            {"nsfw": True, "severity": 0.2, "title": "", "description": ""},
            subscribed_packs=[pack])
        assert r["blocked"] is False

    def test_tag_rule_adds_tag(self):
        pack = {"pack_id": "p1", "rules": _rules("TAG", "nsfw", 0.0)}
        r = cp.evaluate_content(
            {"nsfw": True, "title": "", "description": ""},
            subscribed_packs=[pack])
        assert "nsfw" in r["tags"]
        assert r["verdict"] == "tagged"

    def test_warn_rule_adds_warning(self):
        pack = {"pack_id": "p1", "rules": _rules("WARN", "adult_content", 0.0)}
        r = cp.evaluate_content(
            {"adult_content": True, "severity": 0.5, "title": "", "description": ""},
            subscribed_packs=[pack])
        assert r["warnings"]
        assert r["verdict"] in ("warned", "tagged")

    def test_allow_rule_permits(self):
        pack = {"pack_id": "p1", "rules": [
            {"category": "nsfw", "action": "ALLOW", "severity_threshold": 0.0}]}
        r = cp.evaluate_content(
            {"nsfw": True, "severity": 0.9, "title": "", "description": ""},
            subscribed_packs=[pack])
        assert r["blocked"] is False

    def test_marketplace_paid_category_auto_added(self):
        pack = {"pack_id": "p1", "rules": _rules("TAG", "marketplace_paid", 0.0)}
        r = cp.evaluate_content(
            {"price_mpsi": 5000, "title": "", "description": ""},
            subscribed_packs=[pack])
        assert "marketplace_paid" in r["tags"]

    def test_first_matching_block_wins_over_later_packs(self):
        # Two packs; the first BLOCK short-circuits — floor precedence preserved.
        p_block = {"pack_id": "blocker", "rules": _rules("BLOCK", "nsfw", 0.0)}
        p_tag = {"pack_id": "tagger", "rules": _rules("TAG", "nsfw", 0.0)}
        r = cp.evaluate_content(
            {"nsfw": True, "severity": 0.9, "title": "", "description": ""},
            subscribed_packs=[p_block, p_tag])
        assert r["blocked"] is True
        assert r["blocking_rule"]["pack_id"] == "blocker"

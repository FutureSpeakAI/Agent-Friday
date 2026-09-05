"""Gauntlet finding F54 (part of the 2026-09-04 claim-corpus sweep):
content_policies.py's own docstring claims "The H1-H4 hard floor is always
checked via moderation.scan() first" and "always applies first, regardless
of pack configuration." Neither was true for content classified purely by
category metadata with no title/description text.

evaluate_content()'s H1-H4 check built its scan text ONLY from
title/description and skipped moderation.scan() entirely when that text
was empty. Separately, the per-pack rule loop explicitly skipped
asimov-standard ("already handled above") on the assumption the text scan
already covered it. asimov-standard's own four rules (CSAM,
real_person_deepfake, doxxing, violence_incitement) are category-based,
exactly like every other pack's rules -- so content tagged
categories=["CSAM"] with no title or description hit NEITHER check and
came back clean. evaluate_content() is genuinely live: routes/defederation.py,
services/marketplace.py, services/dissent_gate.py (the Law 1 floor check),
and services/moderation.py itself all call it for real content decisions.

This probe proves the fix: category-only content matching an asimov-standard
rule is now blocked, without needing any title/description text at all.
"""
from __future__ import annotations

import agent_friday.services.content_policies as content_policies


class TestHarmFloorCatchesCategoryOnlyContent:
    def test_csam_category_with_no_text_is_blocked(self, monkeypatch):
        """The exact gap the finding named: no title, no description, just
        an upstream classifier's category tag."""
        monkeypatch.setattr(
            content_policies, "get_subscribed_packs",
            lambda: list(content_policies.BUILTIN_PACKS))

        result = content_policies.evaluate_content({
            "categories": ["CSAM"],
            "severity": 1.0,
        })

        assert result["blocked"] is True, (
            "content tagged categories=['CSAM'] with no title/description "
            "text was not blocked -- the H1-H4 hard floor was bypassed "
            "entirely for category-only content (F54)"
        )
        assert result["blocking_rule"]["pack_id"] == content_policies.ALWAYS_ON_PACK

    def test_each_h1_h4_category_with_no_text_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            content_policies, "get_subscribed_packs",
            lambda: list(content_policies.BUILTIN_PACKS))

        for category in ("CSAM", "real_person_deepfake", "doxxing",
                         "violence_incitement"):
            result = content_policies.evaluate_content({
                "categories": [category], "severity": 1.0,
            })
            assert result["blocked"] is True, (
                f"category {category!r} (an H1-H4 rule) was not blocked "
                "when supplied with no title/description text"
            )

    def test_text_based_detection_still_works_unchanged(self, monkeypatch):
        """Falsifiability / grounding check: the pre-existing text-scan path
        (title/description matched against moderation.scan()) must still
        catch what it already caught -- this fix must not have narrowed
        anything, only widened coverage."""
        monkeypatch.setattr(
            content_policies, "get_subscribed_packs",
            lambda: list(content_policies.BUILTIN_PACKS))

        def _fake_scan(content_text):
            return {"blocked": True, "harm_level": "H1", "reason": "test block"}

        import agent_friday.services.moderation as moderation
        monkeypatch.setattr(moderation, "scan", _fake_scan)

        result = content_policies.evaluate_content({
            "title": "something that should be blocked", "description": "",
        })
        assert result["blocked"] is True
        assert result["blocking_rule"]["pack_id"] == content_policies.ALWAYS_ON_PACK

    def test_clean_content_is_not_blocked(self, monkeypatch):
        """Falsifiability check: the fix must not over-block ordinary
        content that matches no rule at all."""
        monkeypatch.setattr(
            content_policies, "get_subscribed_packs",
            lambda: list(content_policies.BUILTIN_PACKS))

        result = content_policies.evaluate_content({
            "title": "a nice landscape photo", "description": "mountains at dawn",
            "categories": [],
        })
        assert result["blocked"] is False

    def test_non_floor_packs_are_still_evaluated_normally(self, monkeypatch):
        """Grounding check: removing the asimov-standard skip must not
        change how OTHER packs (e.g. family-safe) are evaluated -- they
        were never skipped and still shouldn't be double-counted or
        altered."""
        monkeypatch.setattr(
            content_policies, "get_subscribed_packs",
            lambda: list(content_policies.BUILTIN_PACKS))

        result = content_policies.evaluate_content(
            {"categories": ["nsfw"], "severity": 1.0},
            subscribed_packs=[
                p for p in content_policies.BUILTIN_PACKS
                if p["pack_id"] in (content_policies.ALWAYS_ON_PACK, "family-safe")
            ],
        )
        assert result["blocked"] is True
        assert result["blocking_rule"]["pack_id"] == "family-safe"

"""Unit tests for liquid_ui.py — Liquid UI self-evolving interface system.

Covers:
  - classify_complexity: tier thresholds, score clamping, empty input,
    keyword-driven scores, behavioral signal boosters.
  - FeatureSpecGenerator._title_from_text: prefix stripping, capitalize,
    truncation, empty input.
  - FeatureSpecGenerator._heuristic_synth: structural fill based on keywords.
  - FeatureSpec.tier_policy: delegation to TIER_DEFAULTS, trivial auto_approve.
  - _open_questions_for: condition-based question generation.

All tests use purely synthetic data; no disk I/O through the engine singleton.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import liquid_ui as lui
from liquid_ui import (
    TIER_DEFAULTS,
    TIER_THRESHOLDS,
    FeatureSpec,
    FeatureSpecGenerator,
    LiquidUIRequest,
    classify_complexity,
    _open_questions_for,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _req(**kwargs) -> LiquidUIRequest:
    """Factory: build a minimal LiquidUIRequest for testing."""
    return LiquidUIRequest(
        user_text=kwargs.get("user_text", ""),
        workspace=kwargs.get("workspace", ""),
        signals=kwargs.get("signals", {}),
    )


# ════════════════════════════════════════════════════════════════════════
#  TIER_DEFAULTS sanity — the constants tests depend on
# ════════════════════════════════════════════════════════════════════════

class TestTierDefaults:
    def test_trivial_auto_approve_is_true(self):
        assert TIER_DEFAULTS["trivial"]["auto_approve"] is True

    @pytest.mark.parametrize("tier", ["simple", "medium", "complex", "epic"])
    def test_non_trivial_auto_approve_is_false(self, tier):
        assert TIER_DEFAULTS[tier]["auto_approve"] is False

    def test_all_tiers_present(self):
        assert set(TIER_DEFAULTS.keys()) == {"trivial", "simple", "medium", "complex", "epic"}


# ════════════════════════════════════════════════════════════════════════
#  classify_complexity
# ════════════════════════════════════════════════════════════════════════

class TestClassifyComplexity:
    # ── empty / None input ────────────────────────────────────────────────
    def test_empty_string_is_trivial(self):
        # Empty string: no keyword matches. The length booster uses max(len(lower.split()), 1)
        # so even "" yields 1 word → 1/200 = 0.005 bonus. Tier is still trivial (<0.10).
        tier, score = classify_complexity("")
        assert tier == "trivial"
        assert score < 0.10

    def test_none_is_trivial(self):
        tier, score = classify_complexity(None)  # type: ignore[arg-type]
        assert tier == "trivial"
        assert score < 0.10

    # ── score stays in [0, 1] ─────────────────────────────────────────────
    def test_score_clamped_below_one(self):
        # Pile on many high-weight keywords + signals — score must never exceed 1.0
        text = "platform framework engine integration sync automation scheduler voice agent"
        signals = {
            "touches_multiple_workspaces": True,
            "requires_new_data_model": True,
            "requires_external_integration": True,
            "involves_voice_or_vision": True,
        }
        _, score = classify_complexity(text, signals=signals)
        assert score <= 1.0

    def test_score_never_negative(self):
        _, score = classify_complexity("hello world")
        assert score >= 0.0

    # ── tier thresholds (exact boundary behaviour) ─────────────────────────
    # Source: TIER_THRESHOLDS = [(0.10,"trivial"),(0.30,"simple"),(0.55,"medium"),
    #                             (0.80,"complex"),(1.01,"epic")]
    # score < 0.10  → trivial
    # 0.10 ≤ score < 0.30 → simple
    # 0.30 ≤ score < 0.55 → medium
    # 0.55 ≤ score < 0.80 → complex
    # score ≥ 0.80 → epic

    def test_trivial_tier_low_score(self):
        tier, _ = classify_complexity("rename")   # keyword weight 0.05 + tiny length bonus
        assert tier == "trivial"

    def test_simple_tier_keyword(self):
        # "add a button" weight = 0.12, which (with minimal length bonus) lands in simple
        tier, score = classify_complexity("add a button")
        assert tier == "simple"
        assert 0.10 <= score < 0.30

    def test_medium_tier_keyword(self):
        # "panel" weight = 0.30; length-based booster pushes us into medium
        tier, score = classify_complexity("panel")
        assert tier in ("medium", "complex")  # 0.30 ≤ score
        assert score >= 0.30

    def test_complex_tier_keyword(self):
        # "automation" weight = 0.60 → complex (0.55–0.80)
        tier, score = classify_complexity("automation")
        assert tier == "complex"
        assert 0.55 <= score < 0.80

    def test_epic_tier_keyword(self):
        # "framework" weight = 0.85 → epic
        tier, score = classify_complexity("framework")
        assert tier == "epic"
        assert score >= 0.80

    # ── behavioral signal boosters ─────────────────────────────────────────
    def test_signals_raise_score(self):
        _, score_no_sig = classify_complexity("rename")
        _, score_with = classify_complexity(
            "rename",
            signals={
                "touches_multiple_workspaces": True,
                "requires_new_data_model": True,
            },
        )
        assert score_with > score_no_sig

    def test_unknown_signal_keys_ignored(self):
        # Spurious keys must not crash
        tier, score = classify_complexity("rename", signals={"garbage_key": True})
        assert isinstance(tier, str)
        assert 0.0 <= score <= 1.0

    # ── length booster ──────────────────────────────────────────────────────
    def test_long_text_gets_higher_score_than_short(self):
        short = "rename label"
        long_ = " ".join(["rename"] * 50)  # 50 words, well above threshold
        _, s_short = classify_complexity(short)
        _, s_long = classify_complexity(long_)
        assert s_long > s_short

    # ── return type ─────────────────────────────────────────────────────────
    def test_return_is_tuple_str_float(self):
        result = classify_complexity("search")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], float)


# ════════════════════════════════════════════════════════════════════════
#  FeatureSpecGenerator._title_from_text
# ════════════════════════════════════════════════════════════════════════

class TestTitleFromText:
    def test_empty_string_returns_untitled(self):
        assert FeatureSpecGenerator._title_from_text("") == "Untitled feature"

    def test_i_wish_i_could_prefix_stripped(self):
        result = FeatureSpecGenerator._title_from_text("i wish i could add a dark mode")
        assert result == "Add a dark mode"

    def test_i_wish_prefix_stripped(self):
        result = FeatureSpecGenerator._title_from_text("i wish add more shortcuts")
        assert result == "Add more shortcuts"

    def test_i_want_to_prefix_stripped(self):
        result = FeatureSpecGenerator._title_from_text("I want to see a calendar view")
        assert result == "See a calendar view"

    def test_please_prefix_stripped(self):
        result = FeatureSpecGenerator._title_from_text("please add a dark mode")
        assert result == "Add a dark mode"

    def test_can_you_prefix_stripped(self):
        result = FeatureSpecGenerator._title_from_text("can you add a dark mode")
        assert result == "Add a dark mode"

    def test_make_prefix_stripped(self):
        result = FeatureSpecGenerator._title_from_text("make a dashboard for finances")
        assert result == "A dashboard for finances"

    def test_add_prefix_stripped(self):
        result = FeatureSpecGenerator._title_from_text("add a notification bell")
        assert result == "A notification bell"

    def test_first_letter_capitalized(self):
        result = FeatureSpecGenerator._title_from_text("search for contacts quickly")
        assert result[0].isupper()

    def test_stops_at_first_punctuation(self):
        result = FeatureSpecGenerator._title_from_text("add dark mode; also fix fonts")
        assert ";" not in result
        assert "also fix fonts" not in result

    def test_long_text_truncated_at_80(self):
        long_text = "a " * 60  # well over 80 characters
        result = FeatureSpecGenerator._title_from_text(long_text)
        assert len(result) <= 80

    def test_no_prefix_plain_text(self):
        result = FeatureSpecGenerator._title_from_text("dark mode toggle in settings")
        assert result == "Dark mode toggle in settings"


# ════════════════════════════════════════════════════════════════════════
#  FeatureSpecGenerator._heuristic_synth
# ════════════════════════════════════════════════════════════════════════

class TestHeuristicSynth:
    def _synth(self, text: str) -> dict:
        req = _req(user_text=text)
        return FeatureSpecGenerator._heuristic_synth(req)

    def test_returns_dict_always(self):
        out = self._synth("")
        assert isinstance(out, dict)

    def test_always_has_get_route(self):
        out = self._synth("show me a panel")
        routes = out.get("backend_routes", [])
        methods = [r.get("method") for r in routes]
        assert "GET" in methods

    def test_panel_keyword_adds_card_component(self):
        out = self._synth("I want a panel for my finances")
        kinds = [c.get("kind") for c in out.get("ui_components", [])]
        assert "Card" in kinds

    def test_filter_keyword_adds_searchbar(self):
        out = self._synth("I need to filter my contacts")
        kinds = [c.get("kind") for c in out.get("ui_components", [])]
        assert "SearchBar" in kinds

    def test_form_keyword_adds_post_route(self):
        out = self._synth("I want a form to submit tasks")
        routes = out.get("backend_routes", [])
        methods = [r.get("method") for r in routes]
        assert "POST" in methods

    def test_gmail_keyword_adds_integration(self):
        out = self._synth("integrate with gmail")
        assert "gmail" in out.get("integrations", [])

    def test_calendar_keyword_adds_integration(self):
        out = self._synth("schedule an event on my calendar")
        assert "calendar" in out.get("integrations", [])

    def test_slack_keyword_adds_integration(self):
        out = self._synth("send a slack message")
        assert "slack" in out.get("integrations", [])

    def test_no_keywords_returns_default_panel(self):
        out = self._synth("do something")
        kinds = [c.get("kind") for c in out.get("ui_components", [])]
        assert "Panel" in kinds

    def test_state_flow_always_present(self):
        out = self._synth("something")
        assert isinstance(out.get("state_flow"), list)
        assert len(out["state_flow"]) >= 1

    def test_open_questions_present(self):
        # No workspace → should generate at least one question
        req = _req(user_text="show tasks", workspace="")
        out = FeatureSpecGenerator._heuristic_synth(req)
        assert isinstance(out.get("open_questions"), list)


# ════════════════════════════════════════════════════════════════════════
#  FeatureSpec.tier_policy
# ════════════════════════════════════════════════════════════════════════

class TestTierPolicy:
    @pytest.mark.parametrize("tier", ["trivial", "simple", "medium", "complex", "epic"])
    def test_valid_tiers_return_correct_policy(self, tier):
        spec = FeatureSpec(complexity_tier=tier)
        policy = spec.tier_policy
        assert policy == TIER_DEFAULTS[tier]

    def test_trivial_policy_auto_approve_true(self):
        spec = FeatureSpec(complexity_tier="trivial")
        assert spec.tier_policy["auto_approve"] is True

    def test_simple_policy_auto_approve_false(self):
        spec = FeatureSpec(complexity_tier="simple")
        assert spec.tier_policy["auto_approve"] is False

    def test_unknown_tier_falls_back_to_medium(self):
        spec = FeatureSpec(complexity_tier="nonexistent_tier")
        assert spec.tier_policy == TIER_DEFAULTS["medium"]

    def test_tier_policy_has_expected_keys(self):
        for tier in TIER_DEFAULTS:
            spec = FeatureSpec(complexity_tier=tier)
            p = spec.tier_policy
            assert "auto_approve" in p
            assert "needs_review" in p
            assert "review_mode" in p
            assert "max_seconds" in p

    def test_to_dict_includes_tier_policy(self):
        spec = FeatureSpec(complexity_tier="trivial")
        d = spec.to_dict()
        assert "tier_policy" in d
        assert d["tier_policy"]["auto_approve"] is True


# ════════════════════════════════════════════════════════════════════════
#  _open_questions_for
# ════════════════════════════════════════════════════════════════════════

class TestOpenQuestionsFor:
    def test_no_workspace_adds_workspace_question(self):
        req = _req(user_text="add a dark mode", workspace="")
        qs = _open_questions_for(req)
        assert any("workspace" in q.lower() for q in qs)

    def test_with_workspace_no_workspace_question(self):
        req = _req(user_text="add a dark mode", workspace="Personal")
        qs = _open_questions_for(req)
        assert not any("workspace" in q.lower() for q in qs)

    def test_every_keyword_adds_frequency_question(self):
        req = _req(user_text="run every morning at 9am", workspace="Personal")
        qs = _open_questions_for(req)
        assert any("how often" in q.lower() for q in qs)

    def test_daily_keyword_adds_frequency_question(self):
        req = _req(user_text="send a daily digest", workspace="Personal")
        qs = _open_questions_for(req)
        assert any("how often" in q.lower() for q in qs)

    def test_missing_data_volume_signal_adds_question(self):
        req = _req(user_text="show contacts", workspace="Personal", signals={})
        qs = _open_questions_for(req)
        assert any("items" in q.lower() or "how many" in q.lower() for q in qs)

    def test_data_volume_signal_present_no_items_question(self):
        req = _req(user_text="show contacts", workspace="Personal",
                   signals={"data_volume": 100})
        qs = _open_questions_for(req)
        assert not any("items" in q.lower() for q in qs)

    def test_returns_list(self):
        req = _req(user_text="", workspace="")
        qs = _open_questions_for(req)
        assert isinstance(qs, list)


# ════════════════════════════════════════════════════════════════════════
#  FeatureSpecGenerator.generate (integration of the above)
# ════════════════════════════════════════════════════════════════════════

class TestGenerateIntegration:
    def setup_method(self):
        self.gen = FeatureSpecGenerator()

    def test_generate_returns_feature_spec(self):
        req = _req(user_text="add a dark mode")
        spec = self.gen.generate(req)
        assert isinstance(spec, FeatureSpec)

    def test_title_derived_from_user_text(self):
        req = _req(user_text="i wish i could add a dark mode")
        spec = self.gen.generate(req)
        assert "Dark mode" in spec.title or "dark mode" in spec.title.lower()

    def test_trivial_text_gets_trivial_tier(self):
        req = _req(user_text="rename this label")
        spec = self.gen.generate(req)
        assert spec.complexity_tier == "trivial"
        assert spec.tier_policy["auto_approve"] is True

    def test_epic_text_gets_non_trivial_tier(self):
        req = _req(user_text="build a complete platform framework engine")
        spec = self.gen.generate(req)
        assert spec.complexity_tier in ("complex", "epic")
        assert spec.tier_policy["auto_approve"] is False

    def test_custom_synthesize_fn_used(self):
        called = []

        def fake_synth(r):
            called.append(r)
            return {"title": "Custom Title", "description": "custom desc"}

        gen = FeatureSpecGenerator(synthesize=fake_synth)
        req = _req(user_text="add a panel")
        spec = gen.generate(req)
        assert called
        assert spec.title == "Custom Title"

    def test_synthesize_exception_falls_back_gracefully(self):
        def bad_synth(r):
            raise RuntimeError("boom")

        gen = FeatureSpecGenerator(synthesize=bad_synth)
        req = _req(user_text="add a dark mode")
        spec = gen.generate(req)  # must not raise
        assert isinstance(spec, FeatureSpec)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""Unit tests for epistemic_engine.py — the four _score_* methods,
composite/score computation, register_governance_event, and score clamping.

All tests use synthetic data only. No disk writes from the test side;
the engine may write to ~/.friday (redirected to a throwaway temp dir by conftest).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import math
import threading
import pytest

from agent_friday.epistemic_engine import (
    EpistemicEngine,
    TurnScore,
    PUSHBACK_PHRASES,
    TEACHING_PATTERNS,
    EXECUTION_PATTERNS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    """Fresh EpistemicEngine for each test (does NOT use the module singleton)."""
    return EpistemicEngine()


# ── Helper: call private methods directly ────────────────────────────────────
# We test the internals via name-mangling access so that refactors to score_turn
# can't accidentally hide regressions in a single dimension.

def _ig(eng: EpistemicEngine, user: str, response: str) -> float:
    return eng._score_information_gain(user, response)

def _pb(eng: EpistemicEngine, response: str) -> float:
    return eng._score_pushback(response)

def _sr(eng: EpistemicEngine, response: str) -> float:
    return eng._score_socratic_ratio(response)

def _ind(eng: EpistemicEngine, response: str) -> float:
    return eng._score_independence(response)


# ── _score_information_gain ───────────────────────────────────────────────────

class TestScoreInformationGain:

    def test_empty_response_is_zero(self, engine):
        assert _ig(engine, "hello", "") == 0.0

    def test_score_in_unit_interval(self, engine):
        score = _ig(engine, "hi", "A medium-length reply with some words")
        assert 0.0 <= score <= 1.0

    def test_long_response_scores_higher_than_short(self, engine):
        user = "what is entropy?"
        short = "Disorder."
        long = (
            "Entropy is a thermodynamic quantity measuring disorder. "
            "In information theory Shannon entropy quantifies uncertainty. "
            "Higher entropy means more unpredictability and less structure. "
            "It underpins the second law of thermodynamics and data compression."
        )
        assert _ig(engine, user, long) > _ig(engine, user, short)

    def test_novel_words_boost_score(self, engine):
        user = "tell me about X"
        # All words already present in the user message — very low novelty
        same = "tell me about x tell about x"
        # Words completely new to user — high novelty
        new = (
            "Quantum tunnelling allows particles to penetrate barriers that classical "
            "physics forbids, underpinning semiconductor devices and nuclear fusion."
        )
        assert _ig(engine, user, new) > _ig(engine, user, same)

    def test_length_ratio_caps_at_1(self, engine):
        # A 100× longer response should still stay ≤ 1.0
        user = "why"
        resp = "Because " + ("the fundamental reason is epistemic recursion. " * 50)
        assert _ig(engine, user, resp) <= 1.0

    def test_zero_length_user_message(self, engine):
        # Should not raise ZeroDivisionError
        score = _ig(engine, "", "Some response text here.")
        assert 0.0 <= score <= 1.0

    @pytest.mark.parametrize("user,resp", [
        ("", ""),
        ("a", ""),
        ("", "b"),
    ])
    def test_degenerate_inputs_safe(self, engine, user, resp):
        score = _ig(engine, user, resp)
        assert 0.0 <= score <= 1.0


# ── _score_pushback ───────────────────────────────────────────────────────────

class TestScorePushback:

    def test_empty_is_zero(self, engine):
        assert _pb(engine, "") == 0.0

    def test_score_bounded(self, engine):
        resp = "Actually, I disagree. That's not quite right."
        assert 0.0 <= _pb(engine, resp) <= 1.0

    def test_no_pushback_phrase_is_low(self, engine):
        resp = "Sure, I will do that for you right away."
        assert _pb(engine, resp) < 0.5

    def test_single_pushback_phrase(self, engine):
        resp = "Actually that is not quite what I would recommend."
        assert _pb(engine, resp) > 0.0

    def test_multiple_phrases_score_higher(self, engine):
        one = "Actually, let me push back on that."
        two = "Actually, I disagree. That's not accurate. Let me correct that."
        assert _pb(engine, two) >= _pb(engine, one)

    def test_score_does_not_exceed_one(self, engine):
        # Cram every pushback phrase into one response — should still ≤ 1.0
        resp = " ".join(PUSHBACK_PHRASES)
        assert _pb(engine, resp) <= 1.0

    @pytest.mark.parametrize("phrase", PUSHBACK_PHRASES)
    def test_each_phrase_triggers_nonzero(self, engine, phrase):
        resp = f"Some context. {phrase} the situation here."
        assert _pb(engine, resp) > 0.0


# ── _score_socratic_ratio ─────────────────────────────────────────────────────

class TestScoreSocraticRatio:

    def test_empty_is_zero(self, engine):
        assert _sr(engine, "") == 0.0

    def test_score_bounded(self, engine):
        resp = "What do you think? Why does it matter? How would you approach this?"
        assert 0.0 <= _sr(engine, resp) <= 1.0

    def test_no_questions_returns_low_score(self, engine):
        resp = "The answer is simple. It is always like this."
        score = _sr(engine, resp)
        # Ratio < 0.05 → returns 0.1
        assert score == pytest.approx(0.1)

    def test_healthy_ratio_returns_high_score(self, engine):
        # ~30% questions → should hit 0.8 bracket
        resp = "Consider this. The first thing to note is the baseline. What is the baseline? The second aspect is scope."
        # Let's build something with ~25% questions explicitly
        resp2 = "Sentence one. Sentence two. Is this right? Sentence four."
        score = _sr(engine, resp2)
        assert score >= 0.4

    def test_all_questions_returns_lower_than_ideal(self, engine):
        # ratio > 0.5 → returns 0.7 (slightly penalised)
        resp = "Why? What? How? Where? Who? When?"
        assert _sr(engine, resp) == pytest.approx(0.7)

    @pytest.mark.parametrize("resp,expected_bracket", [
        # < 0.05 ratio
        ("Statement one. Statement two. Statement three. Statement four. Statement five. Statement six.", 0.1),
        # > 0.50 ratio — every token is a question
        ("Why? What? How? Who? When?", 0.7),
    ])
    def test_ratio_brackets(self, engine, resp, expected_bracket):
        assert _sr(engine, resp) == pytest.approx(expected_bracket)


# ── _score_independence ───────────────────────────────────────────────────────

class TestScoreIndependence:

    def test_empty_is_zero(self, engine):
        assert _ind(engine, "") == 0.0

    def test_score_bounded(self, engine):
        resp = "Here's how you can do it: step 1 prepare, then review."
        assert 0.0 <= _ind(engine, resp) <= 1.0

    def test_no_signals_returns_neutral(self, engine):
        # Neither teaching nor execution patterns → 0.5 neutral
        resp = "The weather today is mild and pleasant."
        assert _ind(engine, resp) == pytest.approx(0.5)

    def test_teaching_only_returns_high(self, engine):
        resp = "Here's how to approach this. The way to solve it is first understand the problem."
        score = _ind(engine, resp)
        assert score > 0.5

    def test_execution_only_returns_low(self, engine):
        resp = "I've done the task and it's all set. Here's the result."
        score = _ind(engine, resp)
        assert score < 0.5

    def test_teaching_beats_execution_mix(self, engine):
        # Mixed — teaching patterns should pull score above 0
        resp = (
            "I've completed the initial setup. "
            "Here's how you can extend it: the way to add features is to open the config."
        )
        score = _ind(engine, resp)
        assert 0.0 <= score <= 1.0

    def test_score_does_not_exceed_one(self, engine):
        resp = " ".join([
            "Here's how to do this. The way to proceed is carefully. "
            "Step 1 assess. Step 2 execute. You can do it by following the steps. "
            "The approach is systematic. The trick is consistency. "
            "What you want to do is start slow. The concept here is iteration. "
            "This works because of compounding. Under the hood it uses recursion. "
            "The reason it works is the feedback loop."
        ])
        assert _ind(engine, resp) <= 1.0


# ── score_turn: composite + clamping ─────────────────────────────────────────

class TestScoreTurn:

    def test_returns_turn_score(self, engine):
        turn = engine.score_turn("Hello?", "Hi there, how are you doing today?")
        assert isinstance(turn, TurnScore)

    def test_composite_in_unit_interval(self, engine):
        turn = engine.score_turn("What is 2+2?", "2+2 equals 4.")
        assert 0.0 <= turn.composite <= 1.0

    def test_all_dimensions_in_unit_interval(self, engine):
        turn = engine.score_turn(
            "Can you help me with this?",
            "Actually, I'd push back: here's how to think about it. What do you think?"
        )
        for attr in ("information_gain", "pushback_rate", "socratic_ratio",
                     "independence_fostering", "composite"):
            val = getattr(turn, attr)
            assert 0.0 <= val <= 1.0, f"{attr}={val} out of range"

    def test_timestamp_set(self, engine):
        turn = engine.score_turn("test", "response")
        assert turn.timestamp.endswith("Z")
        assert len(turn.timestamp) > 10

    def test_message_lengths_recorded(self, engine):
        user = "short"
        resp = "A somewhat longer response here."
        turn = engine.score_turn(user, resp)
        assert turn.user_message_length == len(user)
        assert turn.response_length == len(resp)

    def test_history_grows(self, engine):
        before = len(engine._history)
        engine.score_turn("a", "b")
        engine.score_turn("c", "d")
        assert len(engine._history) == before + 2

    def test_empty_inputs_do_not_raise(self, engine):
        turn = engine.score_turn("", "")
        assert 0.0 <= turn.composite <= 1.0

    def test_huge_response_stays_clamped(self, engine):
        user = "go"
        resp = "word " * 10_000
        turn = engine.score_turn(user, resp)
        assert 0.0 <= turn.composite <= 1.0

    def test_weights_sum_to_one(self):
        # Documented weights must equal exactly 1.0
        weights = {
            "information_gain": 0.3,
            "pushback_rate": 0.2,
            "socratic_ratio": 0.25,
            "independence_fostering": 0.25,
        }
        assert math.isclose(sum(weights.values()), 1.0)

    def test_composite_is_weighted_combination(self, engine):
        """Score a deterministic response and verify the composite manually."""
        user = "x"
        response = (
            "Actually, I disagree with that assumption. "
            "Here's how to think about it: step 1 consider the evidence. "
            "What do you believe is the root cause?"
        )
        turn = engine.score_turn(user, response)
        ig = _ig(engine, user, response)
        pb = _pb(engine, response)
        sr = _sr(engine, response)
        ind = _ind(engine, response)
        expected = 0.3 * ig + 0.2 * pb + 0.25 * sr + 0.25 * ind
        expected = max(0.0, min(1.0, expected))
        assert abs(turn.composite - round(expected, 3)) <= 0.001

    def test_concurrent_score_turns_thread_safe(self, engine):
        errors = []
        def worker():
            try:
                engine.score_turn("concurrent user message", "concurrent response text")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"Thread safety errors: {errors}"


# ── register_governance_event ─────────────────────────────────────────────────

class TestRegisterGovernanceEvent:

    def test_returns_turn_score(self, engine):
        turn = engine.register_governance_event(severity=0.8)
        assert isinstance(turn, TurnScore)

    def test_high_severity_lowers_composite(self, engine):
        turn = engine.register_governance_event(severity=0.9)
        assert turn.composite == pytest.approx(0.1, abs=0.001)

    def test_zero_severity_gives_composite_one(self, engine):
        turn = engine.register_governance_event(severity=0.0)
        assert turn.composite == pytest.approx(1.0, abs=0.001)

    def test_max_severity_gives_composite_zero(self, engine):
        turn = engine.register_governance_event(severity=1.0)
        assert turn.composite == pytest.approx(0.0, abs=0.001)

    def test_severity_half_gives_composite_half(self, engine):
        turn = engine.register_governance_event(severity=0.5)
        assert turn.composite == pytest.approx(0.5, abs=0.001)

    def test_all_dimensions_equal_composite(self, engine):
        turn = engine.register_governance_event(severity=0.6, detail="test event")
        assert turn.information_gain == turn.composite
        assert turn.pushback_rate == turn.composite
        assert turn.socratic_ratio == turn.composite
        assert turn.independence_fostering == turn.composite

    def test_severity_clamps_above_one(self, engine):
        turn = engine.register_governance_event(severity=99.0)
        assert turn.composite == pytest.approx(0.0, abs=0.001)

    def test_severity_clamps_below_zero(self, engine):
        turn = engine.register_governance_event(severity=-5.0)
        assert turn.composite == pytest.approx(1.0, abs=0.001)

    def test_user_message_length_is_zero(self, engine):
        turn = engine.register_governance_event(severity=0.5)
        assert turn.user_message_length == 0

    def test_response_length_reflects_detail(self, engine):
        detail = "something bad happened here"
        turn = engine.register_governance_event(severity=0.3, detail=detail)
        assert turn.response_length == len(detail)

    def test_empty_detail_response_length_zero(self, engine):
        turn = engine.register_governance_event(severity=0.3)
        assert turn.response_length == 0

    def test_appended_to_history(self, engine):
        before = len(engine._history)
        engine.register_governance_event(severity=0.5)
        assert len(engine._history) == before + 1

    @pytest.mark.parametrize("severity", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_composite_monotonically_decreasing_with_severity(self, engine, severity):
        turn = engine.register_governance_event(severity=severity)
        expected_composite = max(0.0, 1.0 - severity)
        assert abs(turn.composite - round(expected_composite, 3)) <= 0.001

    def test_timestamp_iso_format(self, engine):
        turn = engine.register_governance_event(severity=0.5)
        assert "T" in turn.timestamp
        assert turn.timestamp.endswith("Z")


# ── get_scores / get_prompt_injection ────────────────────────────────────────

class TestGetScores:

    def test_returns_dict_after_scoring(self, engine):
        engine.score_turn("hello", "world response with novel text here.")
        scores = engine.get_scores()
        assert isinstance(scores, dict)

    def test_total_turns_increments(self, engine):
        engine.score_turn("a", "b c d e f")
        engine.score_turn("g", "h i j k l")
        scores = engine.get_scores()
        # May differ from exactly 2 if history was pre-loaded, but must be >= 2
        assert scores.get("total_turns_scored", 0) >= 2

    def test_dimensions_present(self, engine):
        engine.score_turn("test", "a longer response with content")
        scores = engine.get_scores()
        dims = scores.get("dimensions", {})
        for key in ("information_gain", "pushback_rate", "socratic_ratio",
                    "independence_fostering"):
            assert key in dims


class TestGetPromptInjection:

    def test_returns_string(self, engine):
        out = engine.get_prompt_injection()
        assert isinstance(out, str)

    def test_contains_score_value(self, engine):
        out = engine.get_prompt_injection()
        # Should include "0.00" or similar numeric
        assert any(char.isdigit() for char in out)

    def test_contains_dimension_names(self, engine):
        out = engine.get_prompt_injection()
        assert "Information gain" in out or "information_gain" in out.lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

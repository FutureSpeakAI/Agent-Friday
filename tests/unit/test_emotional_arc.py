"""Unit tests for emotional_arc.py — local sentiment + cross-session arc.

Strategy mirrors test_conversation_memory.py:
  * score_sentiment() is PURE — tested thoroughly with no deps.
  * EmotionalArc is tested against a tmp state file (no real ~/.friday touched),
    so the EMA accumulation, persistence round-trip, and tone-guidance gating are
    all exercised deterministically.

DO NOT import server.py — this module is intentionally standalone.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_friday.emotional_arc import (  # noqa: E402
    score_sentiment, EmotionalArc, get_emotional_arc,
    POS_THRESHOLD, NEG_THRESHOLD,
)


# ─────────────────────────────────────────────────────────────────────────────
#  PART 1 — score_sentiment (pure, always runs)
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreSentiment:
    def test_empty_is_neutral(self):
        assert score_sentiment("") == (0.0, "neutral")
        assert score_sentiment(None) == (0.0, "neutral")

    def test_no_emotional_words_is_neutral(self):
        score, label = score_sentiment("what time is the meeting tomorrow")
        assert score == 0.0
        assert label == "neutral"

    def test_clear_positive(self):
        score, label = score_sentiment("thanks, that's perfect — works great!")
        assert score > 0.3
        assert label == "positive"

    def test_clear_negative(self):
        score, label = score_sentiment("ugh this is still broken and so frustrating")
        assert score < -0.3
        assert label == "negative"

    def test_score_is_bounded(self):
        for txt in ["amazing wonderful perfect love love love",
                    "terrible awful broken broken hate hate frustrated"]:
            score, _ = score_sentiment(txt)
            assert -1.0 <= score <= 1.0

    def test_phrase_outweighs_single_word(self):
        # "doesn't work" (phrase, weight 2) should pull negative even with a
        # mild positive token present.
        score, label = score_sentiment("it doesn't work, not good")
        assert label == "negative"

    def test_word_boundary_no_false_positive(self):
        # "thanksgiving" must not match "thanks".
        score, label = score_sentiment("we are hosting thanksgiving dinner")
        assert label == "neutral"


# ─────────────────────────────────────────────────────────────────────────────
#  PART 2 — EmotionalArc accumulation + persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestEmotionalArc:
    def _arc(self, tmp_path):
        return EmotionalArc(state_file=tmp_path / "arc.json")

    def test_fresh_arc_is_neutral(self, tmp_path):
        arc = self._arc(tmp_path)
        st = arc.state()
        assert st["mood"] == "neutral"
        assert st["count"] == 0
        assert st["ema"] == 0.0

    def test_record_returns_label(self, tmp_path):
        arc = self._arc(tmp_path)
        out = arc.record("thanks so much, that's perfect")
        assert out["label"] == "positive"

    def test_negative_streak_moves_mood_negative(self, tmp_path):
        arc = self._arc(tmp_path)
        for _ in range(5):
            arc.record("this is broken and frustrating, still doesn't work")
        st = arc.state()
        assert st["ema"] <= NEG_THRESHOLD
        assert st["mood"] == "negative"

    def test_positive_streak_moves_mood_positive(self, tmp_path):
        arc = self._arc(tmp_path)
        for _ in range(5):
            arc.record("this is amazing, thank you, works perfectly!")
        st = arc.state()
        assert st["ema"] >= POS_THRESHOLD
        assert st["mood"] == "positive"

    def test_neutral_turns_do_not_wash_out_mood(self, tmp_path):
        arc = self._arc(tmp_path)
        for _ in range(5):
            arc.record("this is broken and frustrating")
        neg_ema = arc.state()["ema"]
        # A few purely-factual turns should NOT drag the EMA back toward zero.
        for _ in range(5):
            arc.record("what time is it")
        assert arc.state()["ema"] == neg_ema

    def test_persistence_round_trip(self, tmp_path):
        f = tmp_path / "arc.json"
        a1 = EmotionalArc(state_file=f)
        for _ in range(4):
            a1.record("frustrating, broken, doesn't work")
        ema1 = a1.state()["ema"]
        # A brand-new instance pointed at the same file recovers the state.
        a2 = EmotionalArc(state_file=f)
        assert a2.state()["ema"] == ema1
        assert a2.state()["count"] == 4

    def test_corrupt_state_file_resets_gracefully(self, tmp_path):
        f = tmp_path / "arc.json"
        f.write_text("{not valid json", encoding="utf-8")
        arc = EmotionalArc(state_file=f)
        # No raise; treated as a fresh neutral arc.
        assert arc.state()["mood"] == "neutral"
        assert arc.record("thanks!")["label"] == "positive"

    def test_daily_rollup_records_counts(self, tmp_path):
        arc = self._arc(tmp_path)
        arc.record("amazing thank you", timestamp="2026-06-09T10:00:00")
        arc.record("broken frustrating", timestamp="2026-06-09T11:00:00")
        st = arc._load()
        day = st["daily"]["2026-06-09"]
        assert day["count"] == 2
        assert day["pos"] == 1
        assert day["neg"] == 1


# ─────────────────────────────────────────────────────────────────────────────
#  PART 3 — tone_guidance gating
# ─────────────────────────────────────────────────────────────────────────────

class TestToneGuidance:
    def test_thin_history_yields_no_guidance(self, tmp_path):
        arc = EmotionalArc(state_file=tmp_path / "arc.json")
        arc.record("thanks")  # count < 3
        assert arc.tone_guidance() == ""

    def test_negative_mood_yields_patient_guidance(self, tmp_path):
        arc = EmotionalArc(state_file=tmp_path / "arc.json")
        for _ in range(5):
            arc.record("broken, frustrating, doesn't work, still failing")
        g = arc.tone_guidance()
        assert g
        assert "patient" in g.lower()

    def test_positive_mood_yields_match_energy_guidance(self, tmp_path):
        arc = EmotionalArc(state_file=tmp_path / "arc.json")
        for _ in range(5):
            arc.record("amazing, thank you, perfect, love it!")
        g = arc.tone_guidance()
        assert g
        assert "energy" in g.lower() or "upbeat" in g.lower()


# ─────────────────────────────────────────────────────────────────────────────
#  PART 4 — singleton
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleton:
    def test_singleton_returns_same_instance(self, tmp_path):
        a = get_emotional_arc(state_file=tmp_path / "s.json")
        b = get_emotional_arc()
        assert a is b

"""Unit tests for memory_dreaming — consolidation pass, fact mining, topic
extraction, noise counting, date validation, and markdown/DB persistence.

A lightweight in-memory stub stands in for the ChromaDB ConversationMemory so
these tests stay fast and hermetic.
"""
from __future__ import annotations

import pytest

from agent_friday.services import memory_dreaming as md


class FakeMemory:
    """Minimal ConversationMemory stub: .recent(n) returns newest-first rows."""
    def __init__(self, rows):
        self._rows = rows

    def recent(self, n=100):
        return list(self._rows[:n])


def _turn(text, day="2026-06-30", role="user", kws=None):
    return {"text": text, "date": day, "timestamp": f"{day}T12:00:00",
            "role": role, "topic_keywords": kws or []}


class TestDayValidation:
    @pytest.mark.parametrize("bad", ["../evil", "2026/06/30", "june", "2026-6-3", ""])
    def test_invalid_days_rejected(self, bad):
        # Empty falls back to yesterday (valid); the rest are rejected.
        if bad == "":
            assert md.dream(day=bad, memory=FakeMemory([]))["ok"] is True
        else:
            assert md.dream(day=bad)["ok"] is False

    def test_valid_iso_day_accepted(self):
        assert md.dream(day="2026-06-30", memory=FakeMemory([]))["ok"] is True


class TestConsolidation:
    def test_empty_memory_produces_empty_envelope(self):
        r = md.dream(day="2026-06-30", memory=FakeMemory([]))
        assert r["ok"] is True
        assert r["turns_reviewed"] == 0
        assert r["consolidated"] == []

    def test_reviews_only_matching_day(self):
        mem = FakeMemory([
            _turn("I prefer dark mode always.", day="2026-06-30"),
            _turn("unrelated older turn", day="2026-06-29"),
        ])
        r = md.dream(day="2026-06-30", memory=mem)
        assert r["turns_reviewed"] == 1

    def test_mines_preference_fact(self):
        mem = FakeMemory([_turn("I prefer concise answers over long ones.")])
        r = md.dream(day="2026-06-30", memory=mem)
        cats = [f["category"] for f in r["consolidated"]]
        assert "preference" in cats

    def test_mines_bio_fact(self):
        mem = FakeMemory([_turn("My name is Stephen and my company is FutureSpeak.")])
        r = md.dream(day="2026-06-30", memory=mem)
        assert any(f["category"] == "bio" for f in r["consolidated"])

    def test_counts_noise_turns(self):
        mem = FakeMemory([
            _turn("thanks"), _turn("ok"), _turn("I prefer verbose logging."),
        ])
        r = md.dream(day="2026-06-30", memory=mem)
        assert r["pruned"] >= 2

    def test_extracts_topics_from_keywords(self):
        mem = FakeMemory([
            _turn("text about kubernetes", kws=["kubernetes", "deploy"]),
            _turn("more kubernetes talk", kws=["kubernetes"]),
        ])
        r = md.dream(day="2026-06-30", memory=mem)
        topics = {t["topic"] for t in r["topics"]}
        assert "kubernetes" in topics

    def test_only_user_turns_mined_for_facts(self):
        mem = FakeMemory([
            _turn("I prefer X", role="assistant"),  # assistant → skipped
            _turn("I prefer Y", role="user"),
        ])
        r = md.dream(day="2026-06-30", memory=mem)
        texts = [f["text"] for f in r["consolidated"]]
        assert not any("prefer X" in t for t in texts)


class TestPersistence:
    def test_writes_markdown_file(self):
        # Turn date must match the dream day so there's something to consolidate.
        mem = FakeMemory([_turn("I prefer tabs over spaces.", day="2026-06-28")])
        md.dream(day="2026-06-28", memory=mem)
        p = md.DREAMS_DIR / "2026-06-28.md"
        assert p.exists()
        assert "Dream" in p.read_text(encoding="utf-8")

    def test_rerun_replaces_dream_row(self):
        mem = FakeMemory([_turn("I prefer one thing.", day="2026-06-27")])
        md.dream(day="2026-06-27", memory=mem)
        md.dream(day="2026-06-27", memory=mem)  # re-run same day
        rows = [d for d in md.recent_dreams(50) if d["day"] == "2026-06-27"]
        assert len(rows) == 1  # one row per day, replaced on re-run

    def test_recent_dreams_and_state(self):
        mem = FakeMemory([_turn("I prefer clarity.", day="2026-06-26")])
        md.dream(day="2026-06-26", memory=mem)
        assert any(d["day"] == "2026-06-26" for d in md.recent_dreams(50))
        st = md.state()
        assert st["available"] is True
        assert st["total_dreams"] >= 1


class TestMemoryUnavailable:
    def test_none_memory_degrades_gracefully(self):
        r = md.dream(day="2026-06-30", memory=None)
        assert r["ok"] is True
        assert r["turns_reviewed"] == 0

    def test_raising_memory_degrades(self):
        class Boom:
            def recent(self, n=100):
                raise RuntimeError("chromadb down")
        r = md.dream(day="2026-06-30", memory=Boom())
        assert r["ok"] is True
        assert r["turns_reviewed"] == 0


class TestDisabled:
    def test_skips_when_disabled(self, monkeypatch):
        monkeypatch.setattr(md, "_enabled", lambda: False)
        assert md.dream(day="2026-06-30")["skipped"] is True

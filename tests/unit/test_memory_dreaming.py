"""Unit tests for services/memory_dreaming.py — overnight consolidation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import memory_dreaming as md  # noqa: E402
from agent_friday.services import user_model as um  # noqa: E402
from agent_friday.services import core_memory as cm  # noqa: E402


class FakeMemory:
    """Minimal stand-in for ConversationMemory.recent()."""

    def __init__(self, rows):
        self._rows = rows

    def recent(self, n=2000):
        return list(self._rows)[:n]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(md, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(md, "DREAMS_DIR", tmp_path / "dreams")
    # isolate user_model too so note_fact writes to a throwaway db
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    yield


def _rows(day):
    return [
        {"text": "I prefer dark mode and terse answers.", "role": "user",
         "date": day, "timestamp": f"{day}T10:00:00", "topic_keywords": ["dark", "mode"]},
        {"text": "We decided to ship the voice fix on Friday.", "role": "user",
         "date": day, "timestamp": f"{day}T10:05:00", "topic_keywords": ["voice", "ship"]},
        {"text": "thanks", "role": "user", "date": day,
         "timestamp": f"{day}T10:06:00", "topic_keywords": []},
        {"text": "Sure — done.", "role": "friday", "date": day,
         "timestamp": f"{day}T10:07:00", "topic_keywords": []},
        {"text": "old stuff", "role": "user", "date": "2020-01-01",
         "timestamp": "2020-01-01T00:00:00", "topic_keywords": ["old"]},
    ]


def test_dream_consolidates_facts():
    day = "2026-06-30"
    res = md.dream(day=day, memory=FakeMemory(_rows(day)))
    assert res["ok"] is True
    assert res["day"] == day
    assert res["turns_reviewed"] == 4  # the 2020 row is filtered by date
    texts = [c["text"] for c in res["consolidated"]]
    assert any("dark mode" in t for t in texts)
    assert any("ship the voice fix" in t for t in texts)


def test_dream_flags_noise():
    day = "2026-06-30"
    res = md.dream(day=day, memory=FakeMemory(_rows(day)))
    assert res["pruned"] >= 1  # "thanks" is low-value


def test_dream_extracts_topics():
    day = "2026-06-30"
    res = md.dream(day=day, memory=FakeMemory(_rows(day)))
    topics = {t["topic"] for t in res["topics"]}
    assert "voice" in topics or "dark" in topics


def test_dream_writes_no_unreviewed_fact_and_queues_it_for_the_owner(monkeypatch):
    """A regex over yesterday's turns is not a review: nothing it finds lands in
    the user model, the knowledge graph or the memory card. Each candidate
    waits in the card's review queue for the owner's Keep. (Red on the old
    code: dream() called user_model.note_fact and ingest_fact directly.)"""
    ingested = []
    from agent_friday.services.knowledge_graph import integration as kgi
    monkeypatch.setattr(kgi, "ingest_fact", lambda *a, **k: ingested.append(a))
    day = "2026-06-30"
    md.dream(day=day, memory=FakeMemory(_rows(day)))
    assert um.profile()["facts"] == []
    assert ingested == []
    assert cm.entries("user") == []
    queued = [p["text"] for p in cm.pending()]
    assert any("dark mode" in t for t in queued)
    assert all(p["source"] == f"dream:{day}" for p in cm.pending())


def test_dream_writes_markdown():
    day = "2026-06-30"
    md.dream(day=day, memory=FakeMemory(_rows(day)))
    assert (md.DREAMS_DIR / f"{day}.md").exists()


def test_dream_empty_is_wellformed():
    res = md.dream(day="2099-01-01", memory=FakeMemory([]))
    assert res["ok"] is True
    assert res["turns_reviewed"] == 0
    assert res["consolidated"] == []


def test_dream_no_memory_graceful():
    res = md.dream(day="2026-06-30", memory=None)
    # no ChromaDB in unit context → empty but well-formed
    assert res["ok"] is True
    assert "turns_reviewed" in res


def test_recent_dreams_and_state():
    day = "2026-06-30"
    md.dream(day=day, memory=FakeMemory(_rows(day)))
    rd = md.recent_dreams()
    assert rd and rd[0]["day"] == day
    st = md.state()
    assert st["available"] is True
    assert st["total_dreams"] >= 1


def test_dream_reruns_replace_same_day():
    day = "2026-06-30"
    md.dream(day=day, memory=FakeMemory(_rows(day)))
    md.dream(day=day, memory=FakeMemory(_rows(day)))
    assert md.state()["total_dreams"] == 1  # deduped by day


def test_rerun_same_day_queues_each_candidate_once():
    # Regression: re-running the SAME day must not file the same candidate twice.
    day = "2026-06-30"
    md.dream(day=day, memory=FakeMemory(_rows(day)))
    before = sorted(p["text"] for p in cm.pending())
    md.dream(day=day, memory=FakeMemory(_rows(day)))
    md.dream(day=day, memory=FakeMemory(_rows(day)))
    assert sorted(p["text"] for p in cm.pending()) == before and before


def test_pull_turns_flags_capped(monkeypatch):
    # When the recent() window saturates and its oldest row is still on the day,
    # dream() must flag capped rather than silently under-consolidate.
    monkeypatch.setattr(md, "_PULL_WINDOW", 3)
    day = "2026-06-30"
    rows = [{"text": f"I prefer thing {i}", "role": "user", "date": day,
             "timestamp": f"{day}T10:0{i}:00", "topic_keywords": []} for i in range(3)]
    res = md.dream(day=day, memory=FakeMemory(rows))
    assert res["capped"] is True

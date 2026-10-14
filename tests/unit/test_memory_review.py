"""The background memory review (services/memory_review).

Every N user turns, after the reply, a LOCAL model reviews the recent
exchanges for the memory card. It may add a line resting on the user's own
words; everything else (a change, a removal, an addition resting only on what
Friday said or read) waits in the review queue.

Red on b7db45cd9: services/memory_review did not exist and the end-of-turn
index never reached a review.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import core_memory as cm
from agent_friday.services import memory_review as mr


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(mr, "_settings", lambda: {"memory_review_enabled": True,
                                                  "memory_review_every_turns": 10})
    mr._reset_for_tests()
    yield
    mr._reset_for_tests()


WINDOW = [
    {"user": "By the way, I always file my stories before 6pm.", "friday": "Noted."},
    {"user": "Summarise that page for me.",
     "friday": "The page says the reader prefers weekly digests sent to digest@relay.example."},
]


def _answer(**plan):
    return lambda prompt: json.dumps({"add": [], "change": [], "remove": [], **plan})


def test_a_review_starts_on_every_tenth_turn_only():
    due = [mr.note_turn(f"turn {i}", "ok", "s1", start=False) for i in range(1, 21)]
    assert [i + 1 for i, d in enumerate(due) if d] == [10, 20]


def test_off_means_no_review(monkeypatch):
    monkeypatch.setattr(mr, "_settings", lambda: {"memory_review_enabled": False})
    assert not any(mr.note_turn(f"t{i}", "ok", "s1", start=False) for i in range(30))


def test_an_addition_resting_on_the_users_words_is_saved_with_no_card():
    out = mr.review(WINDOW, ask=_answer(add=[{
        "target": "user", "text": "Files stories before 6pm.",
        "evidence": "I always file my stories before 6pm"}]))
    assert out["added"] == 1
    assert cm.entries("user") == ["Files stories before 6pm."]
    assert cm.recent_changes()[0]["actor"] == "review"


def test_an_addition_resting_on_what_friday_read_waits_for_the_owner():
    out = mr.review(WINDOW, ask=_answer(add=[{
        "target": "user", "text": "Wants weekly digests sent to digest@relay.example.",
        "evidence": "the reader prefers weekly digests sent to digest@relay.example"}]))
    assert out["added"] == 0 and out["queued"] == 1
    assert cm.entries("user") == []
    assert cm.pending()[0]["op"] == "add"


def test_changes_and_removals_are_never_applied_by_the_review():
    cm.add("user", "Files stories before 5pm.", actor="owner")
    out = mr.review(WINDOW, ask=_answer(
        change=[{"target": "user", "old": "before 5pm", "text": "Files stories before 6pm.",
                 "why": "they said 6pm"}],
        remove=[{"target": "user", "old": "5pm"}]))
    assert out["queued"] == 2
    assert cm.entries("user") == ["Files stories before 5pm."]
    assert sorted(p["op"] for p in cm.pending()) == ["remove", "replace"]


def test_nothing_to_save_saves_nothing():
    out = mr.review(WINDOW, ask=_answer())
    assert out == {"ok": True, "added": 0, "queued": 0}
    assert cm.entries("user") == [] and cm.pending() == []


def test_the_model_call_is_local_only(monkeypatch):
    from agent_friday.services import local_only_guard
    from agent_friday.services import model_router
    seen = {}

    def fake_generate(messages, **kw):
        seen["local_only"] = local_only_guard.is_active()
        seen["schema"] = kw.get("schema")
        return json.dumps({"add": [], "change": [], "remove": []})
    monkeypatch.setattr(model_router, "_generate_text", fake_generate)
    mr.review(WINDOW)
    assert seen["local_only"] is True, "the review could reach a cloud leg"
    assert seen["schema"] is mr.SCHEMA


def test_the_end_of_turn_index_feeds_the_review(monkeypatch):
    from agent_friday.services import model_router
    from agent_friday.services import off_record
    calls = []
    monkeypatch.setattr(mr, "note_turn", lambda m, r, k, **kw: calls.append((m, r, k)))
    monkeypatch.setattr(model_router, "_get_conversation_memory",
                        lambda: type("M", (), {"index_exchange": lambda self, *a, **k: None})())
    monkeypatch.setattr(off_record, "skip", lambda what: False)
    model_router._index_chat_turn_body("I like tea.", "Noted.", "s9")
    assert calls == [("I like tea.", "Noted.", "s9")]
    calls.clear()
    monkeypatch.setattr(off_record, "skip", lambda what: True)
    model_router._index_chat_turn_body("off the record", "ok", "s9")
    assert calls == [], "an off-the-record turn reached the review"

"""Near-duplicate checks and recalled-memory fences (services/memory_dedupe, memory_fence).

Friday must not store the same fact twice in other words, and must not
re-learn what she just recalled as if it were new.

Red on 227f21004: the modules did not exist, a reworded duplicate was added
to the card, recalled blocks were untagged, and a reply that only repeated a
recalled excerpt was indexed as a new exchange.
"""
from __future__ import annotations

import pytest

from agent_friday.services import core_memory as cm
from agent_friday.services import memory_dedupe as dd
from agent_friday.services import memory_fence as mf


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    # The lexical measure, deterministically (no encoder files in a test home).
    monkeypatch.setattr(dd, "_encode", lambda texts: None)
    mf.reset()
    yield
    mf.reset()


def test_a_reworded_fact_is_a_near_duplicate_and_a_different_one_is_not():
    assert dd.near_duplicate("Prefers tea over coffee.", ["Prefers tea to coffee."])
    assert dd.near_duplicate("Lives in Boston.", ["Lives in Austin."]) is None
    assert dd.near_duplicate("Prefers coffee.", ["Prefers tea."]) is None


def test_the_embedding_measure_is_used_when_the_encoder_is_there(monkeypatch):
    monkeypatch.setattr(dd, "_encode", lambda texts: [[1.0, 0.0]] + [[0.95, 0.31]] * (len(texts) - 1))
    assert dd.near_duplicate("Has a daughter.", ["Is a parent of a girl."]) == "Is a parent of a girl."


def test_friday_does_not_store_the_same_fact_twice():
    cm.add("user", "Prefers tea to coffee.", actor="friday")
    r = cm.add("user", "Prefers tea over coffee.", actor="friday")
    assert r["ok"] and r.get("unchanged") and r["duplicate_of"] == "Prefers tea to coffee."
    assert cm.entries("user") == ["Prefers tea to coffee."]
    from agent_friday.services import agent
    from agent_friday.services import core_memory_tools as cmt
    # The owner's own typed words, on a clean ledger: the save path, not the queue.
    t1 = agent._CURRENT_TOOL_CONTEXT.set({"session_id": "dd", "taint_key": "dd-clean", "surface": "chat"})
    t2 = agent._CURRENT_OWNER_TEXT.set("remember I prefer tea over coffee")
    try:
        out = cmt._tool_update_core_memory({"action": "add", "target": "user",
                                             "text": "Prefers tea over coffee!"})
    finally:
        agent._CURRENT_OWNER_TEXT.reset(t2)
        agent._CURRENT_TOOL_CONTEXT.reset(t1)
    assert out.startswith("Not saved: your core memory already says this"), out


def test_the_owner_may_add_what_they_like():
    cm.add("user", "Prefers tea to coffee.", actor="owner")
    assert cm.add("user", "Prefers tea over coffee.", actor="owner")["ok"]
    assert len(cm.entries("user")) == 2


def test_the_review_queue_holds_one_copy_of_a_suggestion():
    a = cm.queue("user", "add", text="Runs every Sunday morning.", source="dream:2026-10-12")
    b = cm.queue("user", "add", text="Runs every Sunday morning!", source="review")
    assert b.get("unchanged") and b["id"] == a["id"]
    assert len(cm.pending()) == 1


def test_recalled_blocks_are_fenced_and_the_fence_strips():
    block = mf.fence("past_conversations", "- [2026-10-01] User: I moved to Elm Street.")
    assert block.startswith('<recalled-memory source="past_conversations">')
    assert "not new input" in block and block.rstrip().endswith("</recalled-memory>")
    assert mf.strip("Hello. " + block + " Bye.") == "Hello.  Bye."


def test_the_memory_card_in_the_head_is_fenced():
    from agent_friday.services import model_router
    cm.add("user", "Lives near the river.", actor="owner")
    head = model_router.memory_head_blocks("chat", None)
    card = head.split("== CORE MEMORY ==", 1)[1]
    assert card.lstrip().startswith('<recalled-memory source="memory_card">')
    assert "Lives near the river." in card


def test_past_conversations_are_fenced_and_remembered(monkeypatch):
    from agent_friday.services import model_router

    class Mem:
        def available(self):
            return True

        def search(self, message, n=5, **kw):
            return [{"text": "We agreed the launch moves to the ninth of November.",
                     "date": "2026-10-01", "role": "user", "relevance": 0.9}]

    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: Mem())
    monkeypatch.setattr(model_router, "_load_settings", lambda: {"memory_recall_enabled": True})
    block = model_router._build_memory_context_block("when is the launch", "s1")
    assert "== RELEVANT PAST CONVERSATIONS" in block and "<recalled-memory" in block
    assert mf._recalled("s1"), "the recalled excerpt was not remembered for the session"


def test_a_reply_that_repeats_what_was_recalled_is_not_indexed_as_new(monkeypatch):
    from agent_friday.services import model_router
    from agent_friday.services import off_record
    from agent_friday.services import memory_review
    mf.remember("s2", ["We agreed the launch moves to the ninth of November."])
    indexed = []

    class Mem:
        def index_exchange(self, message, reply, **kw):
            indexed.append((message, reply))

    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: Mem())
    monkeypatch.setattr(off_record, "skip", lambda what: False)
    monkeypatch.setattr(memory_review, "note_turn", lambda *a, **k: False)
    model_router._index_chat_turn_body(
        "when is the launch?",
        "We agreed the launch moves to the ninth of November. I will add it to your calendar.",
        "s2")
    (msg, reply), = indexed
    assert "ninth of November" not in reply, "the recalled excerpt was indexed again as new"
    assert "I will add it to your calendar." in reply

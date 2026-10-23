"""Forgetting a person reaches what was derived from the owner's conversations.

North star NS-15.7-2 ("forget traverses derived indexes, graph entities,
summaries, caches and proposals"), NS-15.7-3 (a deletion receipt listing
removed, not removable and retained-for-integrity items) and NS-36.2-9
(deletion traversal).

Through the real entry point (services/forget_person.forget, which POST
/api/people/forget calls): after forgetting a person, the turns that name them
are not recalled by ConversationMemory.search or found by conversation_fts.search
(not even after a rebuild from the transcripts), the day summary that named
them is gone and queued, and the summary written again from the cleaned index
does not mention them; card lines and pending suggestions naming them are
gone; graph retraction was requested for their turns; the receipt lists every
store with removed / not removable / retained. An unrelated person's data, the
transcript itself, and an ordinary word that collides with a forgotten name
are untouched.

Red on the base: forget() never touched the vector memory, conversation
search, the summaries or the memory card, and its receipt had no per-store
rows ("stores"), so the first recall assertion fails.

Synthetic data. The real ConversationMemory over an in-memory collection (CI
installs no Chroma), as tests/unit/test_correction_reaches_derived.py does.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from agent_friday.services import memory_derived as md


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


class FakeCollection:
    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[str(i)] = [d, dict(m)]

    def query(self, query_texts=None, query_embeddings=None, n_results=5, where=None, include=None):
        q = _words((query_texts or [""])[0])
        scored = sorted(((1.0 - len(q & _words(d)) / max(1, len(q)), i, d, m)
                         for i, (d, m) in self.rows.items()), key=lambda s: s[0])
        top = scored[:max(1, int(n_results))]
        return {"ids": [[s[1] for s in top]], "documents": [[s[2] for s in top]],
                "metadatas": [[dict(s[3]) for s in top]], "distances": [[s[0] for s in top]]}

    def get(self, ids=None, include=None, where=None, limit=None):
        keys = [str(i) for i in ids if str(i) in self.rows] if ids is not None else list(self.rows)
        if isinstance(where, dict) and isinstance(where.get("session_id"), str):
            keys = [k for k in keys if self.rows[k][1].get("session_id") == where["session_id"]]
        return {"ids": keys, "documents": [self.rows[k][0] for k in keys],
                "metadatas": [dict(self.rows[k][1]) for k in keys]}

    def update(self, ids, metadatas=None, documents=None):
        for i, m in zip(ids, metadatas or []):
            if str(i) in self.rows:
                self.rows[str(i)][1] = dict(m)

    def delete(self, ids=None):
        for i in ids or []:
            self.rows.pop(str(i), None)


DAY1, DAY2 = "2026-09-10", "2026-09-11"
CID = "conv-forget01"


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv
    from agent_friday.services import forget_person as fp
    from agent_friday.services import model_router
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(fp, "_friday_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(model_router, "SESSION_SUMMARY_DIR", tmp_path / "memory" / "session_summaries")
    graph = []
    monkeypatch.setattr(md, "queue_graph_retraction", lambda texts: graph.append(list(texts)) or {})
    mem = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    mem._collection = FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    return tmp_path, mem, graph


def _ts(day, hour):
    return datetime.strptime("%s %02d:00" % (day, hour), "%Y-%m-%d %H:%M").timestamp()


def _seed(tmp, mem, people, turns, card=(), pending=()):
    """people: {key: (name, aliases)}; turns: [(id, role, day, hour, text)]."""
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv
    graph = {"people": {k: {"name": n, "aliases": list(a), "entity_type": "human"}
                        for k, (n, a) in people.items()}}
    (tmp / "people_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    cv.create(title="Boat", cid=CID)
    with open(tmp / "conversations" / CID / "messages.jsonl", "w", encoding="utf-8") as fh:
        for mid, role, day, hour, text in turns:
            fh.write(json.dumps({"id": mid, "role": role, "text": text, "ts": _ts(day, hour)}) + "\n")
            mem.index(text, role, session_id=day, timestamp="%sT%02d:00:00" % (day, hour), msg_id=mid)
    fts.build(force=True)
    cm = tmp / "core_memory"
    cm.mkdir(parents=True, exist_ok=True)
    (cm / "ME.md").write_text("".join("- %s\n" % line for line in card), encoding="utf-8")
    (cm / "pending.jsonl").write_text("".join(json.dumps(
        {"id": "pend-%d" % i, "ts": 1.0, "target": "user", "op": "add", "text": t, "old": "",
         "source": "dream", "reason": ""}) + "\n" for i, t in enumerate(pending)), encoding="utf-8")


def _summary(day, text):
    from agent_friday.services import model_router
    model_router._save_session_summary(day, text)
    return model_router._session_summary_path(day, "md")


DANA_TURN = "Dana Okafor said the boat survey is booked for the first week of October."
DANA_REPLY = "Noted: Dana will send the surveyor's report once the survey is done."
FEE_TURN = "The mooring fee went up to 520 pounds this year, payable in March."
PRIYA_TURN = "Priya Shah sent the insurance papers for the boat this morning."


def test_forgetting_a_person_reaches_every_derived_store(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import core_memory
    from agent_friday.services import forget_person as fp
    from agent_friday.services import model_router
    _seed(tmp, mem,
          {"dana_okafor": ("Dana Okafor", ["Dana"]), "priya_shah": ("Priya Shah", [])},
          [("m-dana", "user", DAY1, 10, DANA_TURN), ("m-reply", "friday", DAY1, 10, DANA_REPLY),
           ("m-fee", "user", DAY1, 11, FEE_TURN), ("m-priya", "user", DAY2, 9, PRIYA_TURN)],
          card=["Dana Okafor is the owner's boat surveyor.", "Priya Shah handles the boat insurance.",
                "The owner will sail to Harwich in June."],
          pending=["Dana prefers phone calls in the morning.", "Priya Shah prefers email."])
    day1 = _summary(DAY1, "Dana Okafor booked the boat survey; the mooring fee went up.")
    day2 = _summary(DAY2, "Priya Shah sent the insurance papers.")
    (tmp / "actions").mkdir()
    (tmp / "actions" / "receipts.jsonl").write_text(json.dumps(
        {"id": "act-1", "tool": "send_email", "summary": "Emailed Dana Okafor the survey dates"}) + "\n",
        encoding="utf-8")
    # Seeded as recallable, so the checks below are not vacuous.
    assert any("Dana" in r["text"] for r in mem.search("Dana Okafor boat survey", n=10))
    assert fts.search("Dana")

    receipt = fp.forget("Dana Okafor")

    # (1) The recall indexes: never recalled, never searched, not even after a rebuild.
    assert not any("Dana" in r["text"] for r in mem.search("Dana Okafor boat survey", n=10)), \
        "a forgotten person's turn is still recalled by conversation memory"
    assert fts.search("Dana") == [], "a forgotten person's turn is still found by conversation search"
    fts.build(force=True)
    assert fts.search("Dana") == [], "a rebuild from the transcript brought the turn back into search"
    assert any(r["text"] == PRIYA_TURN for r in mem.search("Priya insurance papers", n=10))
    assert fts.search("Priya") and fts.search("mooring")
    # The transcript itself follows forget-person's policy: kept, in the owner's words.
    assert DANA_TURN in (tmp / "conversations" / CID / "messages.jsonl").read_text(encoding="utf-8")

    # (2) Day summaries: the one naming them is gone and queued; the other is untouched.
    assert not day1.exists(), "the day summary still names the forgotten person"
    assert day2.exists()
    assert DAY1 in json.loads((tmp / "memory" / "session_summaries" / "redo.json").read_text())

    # (3) Memory card lines and pending suggestions naming them are gone; the rest stay.
    lines = core_memory.entries("user")
    assert not any("Dana" in line for line in lines)
    assert "Priya Shah handles the boat insurance." in lines
    assert "The owner will sail to Harwich in June." in lines
    waiting = [r["text"] for r in core_memory.pending()]
    assert waiting == ["Priya Shah prefers email."]
    journal = (tmp / "core_memory" / "changes.jsonl")
    assert "Dana" not in (journal.read_text(encoding="utf-8") if journal.exists() else "")

    # (4) Graph retraction was requested for their turns, and not for the others.
    asked = [t for batch in graph for t in batch]
    assert DANA_TURN in asked and DANA_REPLY in asked
    assert PRIYA_TURN not in asked and FEE_TURN not in asked

    # The receipt: every store, each with removed / not removable / retained.
    rows = {r["store"]: r for r in receipt["stores"]}
    for store in ("people_graph", "kg_entities", "conversation_recall", "conversation_graph",
                  "day_summaries", "memory_card", "memory_card_pending", "action_receipts",
                  "memory_proposals", "library", "wiki_notes"):
        assert store in rows, "the receipt does not list %s" % store
    for r in receipt["stores"]:
        assert {"removed", "kind", "not_removable", "retained"} <= set(r)
    assert rows["conversation_recall"]["removed"] == 2
    assert rows["day_summaries"]["removed"] == 1
    assert rows["memory_card"]["removed"] == 1 and rows["memory_card_pending"]["removed"] == 1
    assert rows["conversation_graph"]["removed"] == 2
    kept = rows["conversation_recall"]["retained"]
    assert kept and kept[0]["count"] == 2 and kept[0]["reason"]
    assert rows["action_receipts"]["retained"][0]["count"] == 1
    assert rows["action_receipts"]["removed"] == 0
    # Counts and kinds only: no content beyond what the receipt showed before.
    dumped = json.dumps(receipt["stores"])
    assert "survey" not in dumped and "surveyor" not in dumped and "Priya" not in dumped

    # The summary written again reads the cleaned index: it does not bring them back.
    monkeypatch.setattr(model_router, "_get_friday_system_prompt", lambda **k: "")
    monkeypatch.setattr(model_router, "_predict_route_provider", lambda **k: "local")
    monkeypatch.setattr(model_router, "_generate_text",
                        lambda messages, **k: messages[0]["content"].split("===")[2].strip())
    assert md.redo_summaries() == 1
    again = model_router._load_session_summary(DAY1)
    assert "mooring fee" in again, "the day was not summarised again"
    assert "Dana" not in again, "the summary written again names the forgotten person"


def test_a_common_word_that_is_also_the_name_is_untouched(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import core_memory
    from agent_friday.services import forget_person as fp
    will_turn = "Will Hartley checked the rigging on the boat on Saturday."
    plain = "I will phone the marina tomorrow about the winter mooring."
    _seed(tmp, mem, {"will_hartley": ("Will Hartley", ["Will"])},
          [("m-will", "user", DAY1, 10, will_turn), ("m-plain", "user", DAY1, 11, plain)],
          card=["The owner will sail to Harwich in June.", "Will is the owner's rigger."],
          pending=["The owner will repaint the hull in spring."])

    receipt = fp.forget("Will Hartley")

    assert not any(r["text"] == will_turn for r in mem.search("rigging boat Saturday", n=10))
    assert any(r["text"] == plain for r in mem.search("phone marina winter mooring", n=10)), \
        "an ordinary 'will' was removed from recall"
    assert fts.search("marina"), "an ordinary 'will' was removed from conversation search"
    assert not fts.search("rigging")
    assert core_memory.entries("user") == ["The owner will sail to Harwich in June."]
    assert [r["text"] for r in core_memory.pending()] == ["The owner will repaint the hull in spring."]
    rows = {r["store"]: r for r in receipt["stores"]}
    assert rows["conversation_recall"]["removed"] == 1
    assert [t for batch in graph for t in batch] == [will_turn]

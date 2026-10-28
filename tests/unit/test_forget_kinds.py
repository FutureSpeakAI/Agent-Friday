"""Forget a fact, a conversation or a source, and see where something is first.

North star NS-15.7-1: "Forget fact/person/conversation/source and 'show
everywhere' requests supported." Forget-person (M9) and the Library's own
forget existed; these are the other three forgets and the read-only look
(services/forget_kinds, routes/memory_forget).

Through the real entry points, on synthetic data:

* forget_fact removes the statement from the memory card, its review queue,
  the older user model, proposals, the dreaming log and the graph's fact node;
  only the turns the owner confirmed (show_everywhere's candidates: their own
  turns, no negation the statement lacks, not long unrelated ones) leave
  recall and search (not after a rebuild either) while the transcript keeps
  them; with none confirmed no turn is touched and the receipt says so; the
  day summary made from them is queued again; tombstones stop every derived
  path making it again, and the owner can still say it; unrelated lines,
  facts and turns are untouched.
* forget_conversation takes every turn of one conversation out of recall and
  search (a short one too, by its id), keeps the transcript byte for byte,
  queues its day and its graph turns, and leaves another conversation alone,
  the same words said there included.
* stop_using_source handles a meeting (its archive documents too, which the
  archive then refuses, failing closed), a site (never a public suffix), a
  sender and a Library document, records a stopped meeting or site without
  its id or domain, reports a tombstone it could not save, and says "not
  supported yet" for what it cannot do.
* show_everywhere finds the statement in every store and writes nothing.
* every receipt has forget-person's row shape and carries no content.
* the desktop controls are in both UI files, identical where they are shared.

Red on the base: there is no services/forget_kinds (every test here fails at
its import), forget_derived has no purge_turns, and neither UI file has the
controls.

CI installs no Chroma: conversation memory runs over an in-memory collection,
as tests/unit/test_forget_reaches_everything.py does.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

DAY1, DAY2 = "2026-09-10", "2026-09-11"
CID, CID2 = "conv-kinds01", "conv-kinds02"

TEA = "The user prefers green tea at breakfast."
TEA_TURN = "I drink green tea at breakfast every day, with honey."
COFFEE_TURN = "Green tea is fine, but I want coffee at lunch."
FEE_TURN = "The mooring fee went up to 520 pounds this year, payable in March."
PRIYA_TURN = "Priya Shah sent the insurance papers for the boat this morning."


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


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday import core
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv
    from agent_friday.services import core_memory
    from agent_friday.services import egress_gate
    from agent_friday.services import forget_person as fp
    from agent_friday.services import forget_registry as fr
    from agent_friday.services import memory_derived as md
    from agent_friday.services import memory_dreaming
    from agent_friday.services import memory_proposals
    from agent_friday.services import memory_review
    from agent_friday.services import model_router
    from agent_friday.services import tombstones
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph import integration
    from agent_friday.services.knowledge_graph import store as kg_store
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(fp, "_friday_dir", lambda: tmp_path, raising=False)
    for mod in (user_model, memory_dreaming, memory_proposals):
        monkeypatch.setattr(mod, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(user_model, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(memory_dreaming, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(memory_dreaming, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(memory_proposals, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(core, "CONTEXT_LOG_DIR", tmp_path / "vault" / "context-log")
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "vault" / "egress-log.jsonl")
    monkeypatch.setattr(fr, "_owner_names", lambda: set())
    # The chat mirror (core.CHAT_HISTORY, chat_history.json) of this test only.
    monkeypatch.setattr(core, "CHAT_HISTORY_FILE", tmp_path / "chat_history.json")
    monkeypatch.setattr(core, "CHAT_HISTORY", [])
    memory_review._reset_for_tests()
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(fts, "BACKGROUND_BUILD", False)
    monkeypatch.setattr(fts, "_BUILT", {})
    monkeypatch.setattr(model_router, "SESSION_SUMMARY_DIR", tmp_path / "memory" / "session_summaries")
    monkeypatch.setattr(kg_store, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(integration, "kg_settings", lambda: {"enabled": True})
    graph = []
    monkeypatch.setattr(md, "queue_graph_retraction", lambda texts: graph.append(list(texts)) or {})
    # The one-time move of old facts onto the card is not what is tested here.
    (tmp_path / "core_memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "core_memory" / ".migrated_user_model_facts").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(core_memory._MIGRATION_CHECKED, str(tmp_path / "core_memory"), True)
    mem = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    mem._collection = FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    tombstones._reset_for_tests()
    cv._reset_for_tests()
    yield tmp_path, mem, graph
    tombstones._reset_for_tests()
    cv._reset_for_tests()


def _ts(day, hour):
    return datetime.strptime("%s %02d:00" % (day, hour), "%Y-%m-%d %H:%M").timestamp()


def _conv(tmp, mem, cid, title, turns):
    """turns: [(id, role, day, hour, text)], written to the transcript and both indexes."""
    from agent_friday.services import conversations as cv
    cv.create(title=title, cid=cid)
    with open(tmp / "conversations" / cid / "messages.jsonl", "w", encoding="utf-8") as fh:
        for mid, role, day, hour, text in turns:
            fh.write(json.dumps({"id": mid, "role": role, "text": text, "ts": _ts(day, hour)}) + "\n")
            mem.index(text, role, session_id=day, timestamp="%sT%02d:00:00" % (day, hour), msg_id=mid)


def _card(tmp, lines=(), pending=()):
    cm = tmp / "core_memory"
    (cm / "ME.md").write_text("".join("- %s\n" % line for line in lines), encoding="utf-8")
    (cm / "pending.jsonl").write_text("".join(json.dumps(
        {"id": "pend-%d" % i, "ts": 1.0, "target": "user", "op": "add", "text": t, "old": "",
         "source": "dream", "reason": ""}) + "\n" for i, t in enumerate(pending)), encoding="utf-8")


def _proposals(rows):
    """rows: [(fact_id, text, provenance or None)]"""
    from agent_friday.services import memory_proposals as mp
    conn = mp._connect()
    conn.executemany("INSERT INTO proposed_facts(fact_id, ts, day, category, text, evidence, confidence, model, "
                     "provenance) VALUES(?,?,?,?,?,?,?,?,?)",
                     [(f, 1.0, DAY1, "preference", t, "", 0.45, "m", p) for f, t, p in rows])
    conn.commit()
    conn.close()


def _proposal_ids():
    from agent_friday.services import memory_proposals as mp
    conn = mp._connect()
    try:
        return sorted(r[0] for r in conn.execute("SELECT fact_id FROM proposed_facts"))
    finally:
        conn.close()


def _kg(tmp, ents):
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore(base_dir=tmp / "kg")
    store.save("entities", [dict(e, provenance=dict(e.get("provenance") or {}, sensitivity=1)) for e in ents])
    return store


def _fact_node(text, **prov):
    from agent_friday.services import forget_kinds as fk
    return {"id": fk._fact_node_id(text), "title": " ".join(text.split()[:8]), "type": "preference",
            "description": text, "provenance": dict(prov)}


def _summary(day, text):
    from agent_friday.services import model_router
    model_router._save_session_summary(day, text)
    return model_router._session_summary_path(day, "md")


def _rows(receipt):
    return {r["store"]: r for r in receipt["stores"]}


def _shape_ok(receipt):
    for r in receipt["stores"]:
        assert {"store", "removed", "kind", "not_removable", "retained"} <= set(r), r
        assert isinstance(r["removed"], int)


def _seed_tea(tmp, mem):
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import memory_dreaming as dr
    from agent_friday.services import user_model
    _conv(tmp, mem, CID, "Mornings", [("m-tea", "user", DAY1, 8, TEA_TURN), ("m-coffee", "user", DAY1, 12, COFFEE_TURN),
                                     ("m-fee", "user", DAY2, 9, FEE_TURN)])
    fts.build(force=True)
    _card(tmp, ["The user prefers green tea at breakfast.", "The user sails to Harwich in June."],
          ["the user prefers green tea, at breakfast", "The user likes jazz on Sundays."])
    user_model.note_fact("preference", TEA, source="dream:%s" % DAY1)
    user_model.note_fact("workflow", "The user pays the mooring fee in March.", source="dream:%s" % DAY1)
    _proposals([("p-tea", "The user prefers GREEN tea at breakfast!", None),
                ("p-jazz", "The user likes jazz on Sundays.", None)])
    facts = [{"category": "preference", "text": TEA, "confidence": 0.7},
             {"category": "workflow", "text": "We decided to pay the mooring fee in March.", "confidence": 0.6}]
    summary = dr._summarize(DAY1, 3, [{"topic": "tea", "mentions": 2}], facts, 0)
    dr._persist(DAY1, 3, [{"topic": "tea", "mentions": 2}], facts, 0, summary)
    dr._write_markdown(DAY1, 3, [{"topic": "tea", "mentions": 2}], facts, 0, summary)
    _kg(tmp, [_fact_node(TEA, conversations=["proposed:%s" % DAY1]),
              _fact_node("The user pays the mooring fee in March.", conversations=["proposed:%s" % DAY1]),
              {"id": "e-atlas", "title": "Project Atlas", "type": "project", "description": "A project."}])


# ── forget a fact ────────────────────────────────────────────────────────────
def test_forgetting_a_fact_reaches_every_store_and_keeps_the_transcript(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import core_memory
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import memory_dreaming as dr
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    _seed_tea(tmp, mem)
    day1 = _summary(DAY1, "Green tea at breakfast was mentioned; coffee at lunch.")
    day2 = _summary(DAY2, "The mooring fee went up.")
    transcript = (tmp / "conversations" / CID / "messages.jsonl").read_bytes()
    # Seeded as recallable, so the checks below are not vacuous.
    assert any(r["text"] == TEA_TURN for r in mem.search("green tea breakfast", n=10))
    assert fts.search("breakfast")

    receipt = fk.forget_fact("  the user prefers green tea at breakfast  ", turn_ids=["m-tea"])

    assert receipt["ok"]
    _shape_ok(receipt)
    rows = _rows(receipt)
    # The memory card, its queue: the statement in any case or punctuation goes; the rest stay.
    assert core_memory.entries("user") == ["The user sails to Harwich in June."]
    assert [r["text"] for r in core_memory.pending()] == ["The user likes jazz on Sundays."]
    assert rows["memory_card"]["removed"] == 1 and rows["memory_card_pending"]["removed"] == 1
    # The older user model, the proposals, the dreaming log, the graph's fact node.
    assert [f["text"] for f in user_model._recent_facts(50)] == ["The user pays the mooring fee in March."]
    assert _proposal_ids() == ["p-jazz"]
    dream = dr.recent_dreams(5)[0]
    assert [f["text"] for f in dream["consolidated"]] == ["We decided to pay the mooring fee in March."]
    assert "green tea" not in (tmp / "dreams" / ("%s.md" % DAY1)).read_text(encoding="utf-8").lower()
    titles = {e["id"] for e in KnowledgeGraphStore(base_dir=tmp / "kg").load("entities")}
    assert fk._fact_node_id(TEA) not in titles
    assert fk._fact_node_id("The user pays the mooring fee in March.") in titles and "e-atlas" in titles
    assert rows["user_model"]["removed"] == 1 and rows["memory_proposals"]["removed"] == 1
    assert rows["dreams"]["removed"] >= 1 and rows["kg_facts"]["removed"] == 1
    # The turn that stated it: out of recall and search, also after a rebuild; the other turns stay.
    assert not any(r["text"] == TEA_TURN for r in mem.search("green tea breakfast", n=10))
    assert fts.search("breakfast") == []
    fts.build(force=True)
    assert fts.search("breakfast") == [], "a rebuild from the transcript brought the turn back"
    assert fts.search("coffee") and fts.search("mooring")
    assert any(r["text"] == COFFEE_TURN for r in mem.search("coffee lunch", n=10))
    assert fts.is_forgotten("m-tea") and not fts.is_forgotten("m-coffee")
    # ... and the transcript keeps it, byte for byte, and the receipt says so.
    assert (tmp / "conversations" / CID / "messages.jsonl").read_bytes() == transcript
    assert rows["conversation_recall"]["removed"] == 1
    kept = rows["conversation_recall"]["retained"]
    assert kept and kept[0]["count"] == 1 and "History" in kept[0]["reason"]
    # The day summary made from it is gone and queued; the other day is untouched.
    assert not day1.exists() and day2.exists()
    assert DAY1 in json.loads((tmp / "memory" / "session_summaries" / "redo.json").read_text())
    assert rows["day_summaries"]["removed"] == 1
    # The graph retraction was requested for that turn only.
    asked = [t for batch in graph for t in batch]
    assert TEA_TURN in asked and COFFEE_TURN not in asked and FEE_TURN not in asked
    # Counts and kinds only.
    dumped = json.dumps(receipt).lower()
    assert "green" not in dumped and "breakfast" not in dumped and "honey" not in dumped


def test_a_forgotten_fact_is_not_made_again_but_the_owner_can_still_say_it(home):
    tmp, mem, graph = home
    from agent_friday.services import core_memory
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph import integration
    _seed_tea(tmp, mem)

    r = fk.forget_fact(TEA, turn_ids=["m-tea"])

    assert _rows(r)["tombstones"]["removed"] == 3 and _rows(r)["tombstones"]["not_removable"] == []
    # Nightly dreaming or the review pass suggesting it for the card again:
    assert core_memory.queue("user", "add", text=TEA, source="dream:%s" % DAY2).get("tombstoned")
    assert core_memory.add("user", "the user prefers green tea at breakfast", actor="friday").get("tombstoned")
    # A dream storing it as a fact, a proposal run proposing it (other case), the graph igniting it:
    assert user_model.note_fact("preference", TEA, source="dream:%s" % DAY2).get("tombstoned")
    assert mp._refused_before("THE USER PREFERS GREEN TEA AT BREAKFAST")
    assert integration.ingest_fact(TEA, source_kind="conversation", source_key="dream:x", sensitivity=1) is None
    # The forgotten turn re-indexed by its id (a rebuild) stays out.
    assert mem.index(TEA_TURN, "user", session_id=DAY1, msg_id="m-tea") is None
    # Unrelated statements are not held back.
    assert user_model.note_fact("preference", "The user prefers rooibos at night.", source="dream:x")["ok"]
    # The tombstone file holds no text.
    raw = (tmp / "tombstones.json").read_text(encoding="utf-8").lower()
    assert "tea" not in raw and "breakfast" not in raw
    # The owner saying it again wins.
    assert core_memory.add("user", TEA, actor="owner")["ok"]
    assert TEA in core_memory.entries("user")


def test_a_short_statement_leaves_turns_alone_and_says_why(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import core_memory
    from agent_friday.services import forget_kinds as fk
    _conv(tmp, mem, CID, "Music", [("m-jazz", "user", DAY1, 9, "I love jazz, play some jazz please.")])
    fts.build(force=True)
    _card(tmp, ["Likes jazz.", "The user sails to Harwich in June."])

    where = {s["store"]: s for s in fk.show_everywhere("Likes jazz.")["stores"]}
    assert where["conversation_turns"]["candidates"] == []
    assert "fewer than 3 meaningful words" in where["conversation_turns"]["note"]

    r = fk.forget_fact("Likes jazz.")

    assert core_memory.entries("user") == ["The user sails to Harwich in June."]
    row = _rows(r)["conversation_recall"]
    assert row["removed"] == 0 and "none were chosen" in row["retained"][0]["reason"]
    assert fts.search("jazz") and not fts.is_forgotten("m-jazz")
    assert graph == []


def test_forget_fact_by_a_saved_fact_id(home):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import user_model
    user_model.note_fact("preference", TEA, source="manual")
    user_model.note_fact("preference", "The user prefers rooibos at night.", source="manual")
    fid = next(f["fact_id"] for f in user_model._recent_facts(10) if f["text"] == TEA)

    r = fk.forget_fact(fact_id=fid)

    assert r["ok"] and _rows(r)["user_model"]["removed"] == 1
    assert [f["text"] for f in user_model._recent_facts(10)] == ["The user prefers rooibos at night."]
    assert fk.forget_fact("")["ok"] is False


# ── forget a conversation ────────────────────────────────────────────────────
def test_forgetting_a_conversation_stops_using_every_turn_and_keeps_the_transcript(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import forget_kinds as fk
    _conv(tmp, mem, CID, "Boat", [("c1", "user", DAY1, 10, TEA_TURN), ("c2", "user", DAY1, 10, "ok"),
                                  ("c3", "friday", DAY1, 11, FEE_TURN)])
    _conv(tmp, mem, CID2, "Insurance", [("d1", "user", DAY2, 9, "ok"), ("d2", "user", DAY2, 9, PRIYA_TURN)])
    fts.build(force=True)
    day1 = _summary(DAY1, "Tea and the mooring fee.")
    day2 = _summary(DAY2, "Insurance papers arrived.")
    before = (tmp / "conversations" / CID / "messages.jsonl").read_bytes()
    assert fts.search("mooring") and "c2" in mem._collection.rows

    r = fk.forget_conversation(CID)

    assert r["ok"]
    _shape_ok(r)
    rows = _rows(r)
    # Every turn of it, the short one too (by its id), leaves recall and search; not after a rebuild either.
    assert not {"c1", "c2", "c3"} & set(mem._collection.rows)
    assert fts.search("mooring") == [] and fts.search("breakfast") == []
    fts.build(force=True)
    assert fts.search("mooring") == []
    assert all(fts.is_forgotten(m) for m in ("c1", "c2", "c3"))
    assert rows["conversation_recall"]["removed"] == 3
    # The other conversation, its own short "ok" included, is untouched.
    assert {"d1", "d2"} <= set(mem._collection.rows)
    assert fts.search("insurance") and not fts.is_forgotten("d1")
    # The transcript stays, and the receipt says why and how to remove it.
    assert (tmp / "conversations" / CID / "messages.jsonl").read_bytes() == before
    kept = rows["conversation_recall"]["retained"][0]
    assert kept["count"] == 3 and kept["what"] == "messages in this conversation" and "clear" in kept["reason"]
    assert rows["learned_from_it"]["not_removable"], "lines learned from it are not said to be unreachable"
    # Its day is summarised again; the graph retracts its turns only.
    assert not day1.exists() and day2.exists() and rows["day_summaries"]["removed"] == 1
    asked = [t for batch in graph for t in batch]
    assert TEA_TURN in asked and FEE_TURN in asked and PRIYA_TURN not in asked
    dumped = json.dumps(r).lower()
    assert "mooring" not in dumped and "boat" not in dumped
    # Running it again is safe, and an unknown conversation is refused.
    graph.clear()
    again = _rows(fk.forget_conversation(CID))
    assert again["conversation_recall"]["removed"] == 0 and graph == []
    assert fk.forget_conversation("conv-nope")["ok"] is False


def test_forgetting_a_conversation_leaves_the_same_words_in_another_one_alone(home):
    """The same 20+ character message said in a second conversation is that
    conversation's: it stays in recall and search and is not added to the
    forgotten list. A legacy vector row (random id, stored before turns
    carried their message id) goes only when it is dated on this
    conversation's own day."""
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import forget_kinds as fk
    _conv(tmp, mem, CID, "Boat", [("c1", "user", DAY1, 10, TEA_TURN), ("c3", "user", DAY1, 11, FEE_TURN)])
    _conv(tmp, mem, CID2, "Boat again", [("d3", "user", DAY2, 9, FEE_TURN), ("d4", "user", DAY2, 9, PRIYA_TURN)])
    legacy_here = mem.index(TEA_TURN, "user", session_id=DAY1, timestamp="%sT10:00:00" % DAY1)
    legacy_there = mem.index(FEE_TURN, "user", session_id=DAY2, timestamp="%sT09:00:00" % DAY2)
    assert len(legacy_here) == 32 and len(legacy_there) == 32
    fts.build(force=True)
    day2 = _summary(DAY2, "Insurance papers arrived.")

    r = fk.forget_conversation(CID)

    rows = _rows(r)
    assert not {"c1", "c3", legacy_here} & set(mem._collection.rows)
    # The other conversation's identical message: still recalled and searched, never forgotten.
    assert {"d3", "d4", legacy_there} <= set(mem._collection.rows)
    assert not fts.is_forgotten("d3") and not fts.is_forgotten(legacy_there)
    assert fts.search("mooring"), "the other conversation's message left search"
    # The receipt counts this conversation's two turns, not the other's; the other day is not redone.
    assert rows["conversation_recall"]["removed"] == 2
    assert day2.exists()


# ── forget a fact: candidate turns, confirmed by the owner ──────────────────
LONG_TURN = ("Yesterday we walked along the harbour and talked for hours about the boat, the mooring fee, the "
             "insurance papers, the engine service, the sails that need replacing, the winter storage costs, and "
             "then we had green tea at breakfast before driving home through the hills in the rain.")
NEG_TURN = "I do not drink green tea at breakfast any more."


def test_turn_candidates_are_the_owner_s_close_turns_only(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import forget_kinds as fk
    _conv(tmp, mem, CID, "Mornings", [("m-tea", "user", DAY1, 8, TEA_TURN), ("m-neg", "user", DAY1, 9, NEG_TURN),
                                     ("m-long", "user", DAY1, 10, LONG_TURN),
                                     ("m-fri", "friday", DAY1, 11, "Noted: green tea at breakfast.")])
    fts.build(force=True)

    turns = {s["store"]: s for s in fk.show_everywhere(TEA)["stores"]}["conversation_turns"]

    # A negation the statement lacks, a long unrelated turn and Friday's own turn are not candidates.
    assert [c["message_id"] for c in turns["candidates"]] == ["m-tea"]
    assert turns["count"] == 1 and turns["candidates"][0]["close"] is True
    # Common verbs and negations are not content words; a negated statement may match a negated turn.
    assert fk.TurnMatcher("The user does not drink green tea at breakfast.").words == {"drink", "green", "tea",
                                                                                      "breakfast"}
    assert fk.TurnMatcher("The user does not drink green tea at breakfast.")(NEG_TURN)
    assert not fk.TurnMatcher(TEA)(NEG_TURN) and not fk.TurnMatcher(TEA)(LONG_TURN)


def test_forget_fact_removes_only_the_turns_the_owner_confirmed(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import forget_kinds as fk
    other = "Green tea at breakfast again today, lovely."
    _conv(tmp, mem, CID, "Mornings", [("m-a", "user", DAY1, 8, TEA_TURN), ("m-b", "user", DAY1, 9, other),
                                     ("m-fri", "friday", DAY1, 10, "Green tea at breakfast it is, then.")])
    fts.build(force=True)
    _card(tmp, [TEA])

    # Without confirmed ids no turn is touched, and the receipt says so.
    r = fk.forget_fact(TEA)
    row = _rows(r)["conversation_recall"]
    assert row["removed"] == 0 and "none were chosen" in row["retained"][0]["reason"]
    assert "No conversation messages were chosen" in r["note"]
    assert not any(fts.is_forgotten(m) for m in ("m-a", "m-b", "m-fri")) and graph == []
    assert {"m-a", "m-b"} <= set(mem._collection.rows)

    # Only the confirmed one goes; an id that is Friday's turn or no turn at all is ignored.
    r = fk.forget_fact(TEA, turn_ids=["m-a", "m-fri", "no-such-turn"])
    assert _rows(r)["conversation_recall"]["removed"] == 1
    assert fts.is_forgotten("m-a") and not fts.is_forgotten("m-b") and not fts.is_forgotten("m-fri")
    assert "m-a" not in mem._collection.rows and {"m-b", "m-fri"} <= set(mem._collection.rows)
    assert [t for batch in graph for t in batch] == [TEA_TURN]
    assert "messages you chose" in r["note"]


# ── stop using a source ──────────────────────────────────────────────────────
MEET_FACT = "The user agreed to ship the Atlas beta on the 14th."
OTHER_MEET_FACT = "The user owns the Mercury budget review."


def test_stopping_a_meeting_removes_what_came_from_it_and_nothing_new_comes_in(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import core_memory
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import meeting_archive as ma
    from agent_friday.services import memory_proposals as mp
    archived = []
    monkeypatch.setattr(ma, "remove_meeting", lambda mid, store=None: archived.append(mid) or 2)
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph import integration
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    _proposals([("q1", "The user will demo Atlas to the board.", json.dumps({"meeting_id": "m-1"})),
                ("q2", "Mercury moves to Q3.", json.dumps({"meeting_id": "m-2"})),
                ("q3", "The user likes jazz on Sundays.", None)])
    user_model.note_fact("project", MEET_FACT, source="meeting:m-1")
    user_model.note_fact("project", OTHER_MEET_FACT, source="meeting:m-2")
    _card(tmp, [MEET_FACT, OTHER_MEET_FACT])
    _kg(tmp, [_fact_node(MEET_FACT, conversations=["meeting:m-1"]),
              _fact_node("Atlas launch slips a week.", conversations=["meeting:m-1"]),
              _fact_node(OTHER_MEET_FACT, conversations=["meeting:m-2"])])

    r = fk.stop_using_source("meeting:m-1")

    assert r["ok"] and r["source_kind"] == "meeting"
    _shape_ok(r)
    rows = _rows(r)
    assert _proposal_ids() == ["q2", "q3"]
    assert [f["text"] for f in user_model._recent_facts(10)] == [OTHER_MEET_FACT]
    assert core_memory.entries("user") == [OTHER_MEET_FACT]
    ids = {e["id"] for e in KnowledgeGraphStore(base_dir=tmp / "kg").load("entities")}
    assert ids == {fk._fact_node_id(OTHER_MEET_FACT)}
    assert rows["memory_proposals"]["removed"] == 1 and rows["user_model"]["removed"] == 1
    assert rows["memory_card"]["removed"] == 1 and rows["kg_facts"]["removed"] == 2
    assert rows["meeting_record"]["retained"] and "Meetings" in rows["meeting_record"]["retained"][0]["reason"]
    # Its searchable Library copies (the meetings archive) are removed too, and the note says so.
    assert archived == ["m-1"] and rows["meeting_archive"]["removed"] == 2
    assert "searchable copies" in r["note"] and "Meetings" in r["note"]
    assert "tombstones" not in rows, "a tombstone failure was reported when every one was saved"
    # Nothing new from it: no proposal, no graph fact; the statements it gave are held back.
    assert mp.propose_from_meeting([{"text": "Atlas beta moves.", "category": "project", "span": {"i": 0}}],
                                   "m-1", DAY1, "gemma") == 0
    assert integration.ingest_fact("Atlas has a new owner from today.", source_kind="conversation",
                                   source_key="meeting:m-1", sensitivity=1) is None
    assert user_model.note_fact("project", MEET_FACT, source="dream:x").get("tombstoned")
    assert fk.source_stopped("meeting:m-1") and not fk.source_stopped("meeting:m-2")
    # The record of the stopped source carries neither the id nor any text.
    raw = (tmp / fk.STOPPED_FILE).read_text(encoding="utf-8")
    assert "m-1" not in raw and "meeting" not in raw and "Atlas" not in raw
    assert "Atlas" not in json.dumps(r)


def test_stopping_a_site_removes_facts_learned_only_from_it(home):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services.knowledge_graph import integration
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore

    def research(eid, *urls):
        return {"id": eid, "title": eid, "type": "fact", "description": eid,
                "provenance": {"learned": "research", "sources": [{"url": u} for u in urls]}}
    _kg(tmp, [research("fact_aaaaaaaaaaaa", "https://www.example.com/a", "https://news.example.com/b"),
              research("fact_bbbbbbbbbbbb", "https://other.org/x"),
              research("fact_cccccccccccc", "https://example.com/c", "https://other.org/y"),
              research("fact_dddddddddddd", "https://notexample.com/z")])

    r = fk.stop_using_source("https://www.example.com/some/page")

    assert r["source_kind"] == "web"
    assert "research" in r["note"] and "may still open the site" in r["note"]
    _shape_ok(r)
    ids = {e["id"] for e in KnowledgeGraphStore(base_dir=tmp / "kg").load("entities")}
    assert ids == {"fact_bbbbbbbbbbbb", "fact_cccccccccccc", "fact_dddddddddddd"}
    rows = _rows(r)
    assert rows["kg_facts"]["removed"] == 1
    # No chat reply read it, so no turn was touched (owner ruling 10 covers chat; see below).
    assert rows["conversation_recall"]["removed"] == 0 and rows["conversation_recall"]["not_removable"] == []
    # A later research finding from that site is not taken in; another site's is.
    assert integration.ingest_fact("Example Corp was founded in 1999 in Leeds.", source_kind="research",
                                   source_key="setup-research:j:c", sources=[{"url": "https://example.com/about"}],
                                   sensitivity=1) is None
    assert integration.ingest_fact("Other Org was founded in 2001 in York.", source_kind="research",
                                   source_key="setup-research:j:d", sources=[{"url": "https://other.org/about"}],
                                   sensitivity=1)
    assert fk.site_stopped("https://blog.example.com/x") and not fk.site_stopped("https://notexample.com/")
    assert "example" not in (tmp / fk.STOPPED_FILE).read_text(encoding="utf-8")


def test_a_sender_a_library_document_and_an_unknown_source(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import message_triage as mt
    from agent_friday.services.library import forget as lf
    from agent_friday.services.library import principal as pr
    seen = []
    monkeypatch.setattr(mt, "forget_sender", lambda e, lane_id=None: seen.append(e) or {"ok": True, "removed": True})
    sender = fk.stop_using_source("Dana@Example.com")
    r = _rows(sender)
    assert seen == ["dana@example.com"] and r["sender_signals"]["removed"] == 1
    assert "triage" in sender["note"] and "no longer recalled" not in sender["note"]
    assert "not supported yet" in r["mail_content"]["not_removable"][0]["reason"]

    calls = []
    monkeypatch.setattr(pr, "current", lambda: "owner")
    monkeypatch.setattr(lf, "forget_document", lambda p, d: calls.append((p, d)) or
                        {"ok": True, "forgotten": "Secret plan.pdf", "messages_rewritten": 2, "incomplete": []})
    out = fk.stop_using_source("library:7")
    assert calls == [("owner", 7)]
    rows = _rows(out)
    assert rows["library"]["removed"] == 1 and rows["saved_chats"]["removed"] == 2
    assert rows["library"]["retained"][0]["what"] == "the file on disk"
    assert "Secret plan" not in json.dumps(out), "the receipt carried the document's title"

    unknown = fk.stop_using_source("my diary")
    assert unknown["source_kind"] == "unknown"
    assert "not supported yet" in unknown["stores"][0]["not_removable"][0]["reason"]
    assert not (tmp / fk.STOPPED_FILE).exists()


# ── show everywhere ──────────────────────────────────────────────────────────
def _snapshot(root: Path):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and not p.name.endswith(("-wal", "-shm", "-journal"))}


def test_show_everywhere_finds_it_in_every_store_and_writes_nothing(home):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    _seed_tea(tmp, mem)
    rows_before = dict(mem._collection.rows)
    before = _snapshot(tmp)

    out = fk.show_everywhere("The user prefers green tea at breakfast")

    assert _snapshot(tmp) == before, "show everywhere wrote to a store"
    assert mem._collection.rows == rows_before
    assert out["ok"] and out["read_only"] and out["query_kind"] == "statement"
    where = {r["store"]: r for r in out["stores"]}
    assert where["memory_card"]["count"] == 1
    assert where["memory_card"]["locations"] == [{"file": "About you", "line": 1}]
    assert where["memory_card_pending"]["count"] == 1
    assert where["user_model"]["count"] == 1 and where["memory_proposals"]["count"] == 1
    assert where["dreams"]["count"] == 1 and where["kg_facts"]["count"] == 1
    turns = where["conversation_turns"]
    assert turns["count"] == 1 and turns["locations"][0]["conversation_id"] == CID
    assert turns["locations"][0]["title"] == "Mornings" and turns["locations"][0]["messages"] == 1
    # The candidate itself, for the owner to tick: their own message, with a short snippet.
    assert [(c["message_id"], c["conversation_id"], c["close"]) for c in turns["candidates"]] == [("m-tea", CID, True)]
    assert turns["candidates"][0]["snippet"] == TEA_TURN
    assert where["tombstones"]["count"] == 0
    # After the forget (with the candidate ticked) it is found nowhere, and recorded as deleted.
    fk.forget_fact(TEA, turn_ids=["m-tea"])
    after = {r["store"]: r for r in fk.show_everywhere(TEA)["stores"]}
    assert all(r["count"] == 0 for k, r in after.items() if k != "tombstones")
    assert after["tombstones"]["count"] == 1


def test_show_everywhere_for_a_source_is_read_only(home):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import user_model
    _proposals([("q1", "The user will demo Atlas to the board.", json.dumps({"meeting_id": "m-1"}))])
    user_model.note_fact("project", MEET_FACT, source="meeting:m-1")
    _kg(tmp, [_fact_node(MEET_FACT, conversations=["meeting:m-1"])])
    before = _snapshot(tmp)

    out = fk.show_everywhere("meeting:m-1")

    assert _snapshot(tmp) == before
    assert out["query_kind"] == "source" and out["source_kind"] == "meeting"
    where = {r["store"]: r["count"] for r in out["stores"]}
    assert where == {"memory_proposals": 1, "user_model": 1, "kg_facts": 1, "stopped_sources": 0}


def test_sources_are_parsed_strictly():
    from agent_friday.services import forget_kinds as fk
    assert fk.parse_source("library:12") == ("library", "12")
    assert fk.parse_source("library:../x")[0] == ""
    assert fk.parse_source("meeting:mtg_2026-10-01") == ("meeting", "mtg_2026-10-01")
    assert fk.parse_source("meeting:a b")[0] == ""
    assert fk.parse_source("https://WWW.Example.com/a?b=1") == ("web", "example.com")
    assert fk.parse_source("web:news.example.co.uk") == ("web", "news.example.co.uk")
    assert fk.parse_source("sender:Dana@x.com") == ("sender", "dana@x.com")
    assert fk.parse_source("the user prefers tea")[0] == ""
    # A Library id is ASCII digits only (str.isdigit also takes other scripts' digits and superscripts).
    assert fk.parse_source("library:١٢")[0] == "" and fk.parse_source("library:²")[0] == ""
    # A public suffix on its own is no one site; a site under it is.
    for s in ("co.uk", "web:co.uk", "github.io", "https://github.io/x", "site:com.au", "web:herokuapp.com"):
        assert fk.parse_source(s)[0] == "", s
    assert fk.parse_source("bbc.co.uk") == ("web", "bbc.co.uk")
    assert fk.parse_source("https://dana.github.io/blog") == ("web", "dana.github.io")


def test_a_public_suffix_is_not_stopped(home):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    r = fk.stop_using_source("web:co.uk")
    assert r["source_kind"] == "unknown" and not (tmp / fk.STOPPED_FILE).exists()
    assert not fk.site_stopped("https://www.bbc.co.uk/news")


def test_no_new_key_is_made_beside_existing_stopped_sources(home):
    """A new tombstone key beside stopped-source entries made under a lost one
    would orphan them all (every stopped meeting or site starts again)."""
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import tombstones
    (tmp / fk.STOPPED_FILE).write_text(json.dumps({"version": 1, "entries": [{"h": "0" * 64, "day": DAY1}]}),
                                       encoding="utf-8")
    assert not tombstones._key_path().exists()

    out = fk.record_stopped("web:example.com")

    assert out["ok"] is False and not tombstones._key_path().exists()
    assert json.loads((tmp / fk.STOPPED_FILE).read_text(encoding="utf-8"))["entries"] == [{"h": "0" * 64,
                                                                                          "day": DAY1}]


MID = "0123456789ab"
OTHER_MID = "ba9876543210"


def test_the_meetings_archive_refuses_a_stopped_meeting_failing_closed(home, monkeypatch):
    tmp, mem, graph = home
    from types import SimpleNamespace
    from agent_friday.services import file_grants as fg
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import meeting_archive as ma
    monkeypatch.setattr(ma, "owns", lambda p: True)
    monkeypatch.setattr(ma, "visible", lambda: True)
    monkeypatch.setattr(ma, "meeting_exists", lambda mid: True)
    monkeypatch.setattr(ma, "choice", lambda: "on")
    monkeypatch.setattr(ma, "unavailable", lambda store=None: "")
    monkeypatch.setattr(fg, "check_grant", lambda p: SimpleNamespace(state="granted"))

    def doc(mid):
        return str(tmp / "meetings" / mid / "library" / "notes.md")
    assert ma.covers(doc(MID)) and ma.covers(doc(OTHER_MID))

    r = fk.stop_using_source("meeting:" + MID)

    assert _rows(r)["stopped_sources"]["removed"] == 1
    # Not shown, not read in again; another meeting is unaffected.
    assert not ma.covers(doc(MID)) and ma.covers(doc(OTHER_MID))
    assert ma.index_meeting({"id": MID, "state": "done"}, store=object()) == {"indexed": 0, "skipped": "stopped"}
    # A record that cannot be read hides every archive document (fail closed), while the
    # proposal and graph checks stay open (a source never stopped is not lost).
    (tmp / fk.STOPPED_FILE).write_text("{not json", encoding="utf-8")
    assert not ma.covers(doc(OTHER_MID)) and ma.stopped(OTHER_MID)
    assert fk.source_stopped("meeting:" + OTHER_MID) is False


def test_a_tombstone_that_cannot_be_saved_is_reported_when_a_meeting_is_stopped(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import meeting_archive as ma
    from agent_friday.services import tombstones
    from agent_friday.services import user_model
    monkeypatch.setattr(ma, "remove_meeting", lambda mid, store=None: 0)
    user_model.note_fact("project", MEET_FACT, source="meeting:m-1")
    real_add = tombstones.add
    monkeypatch.setattr(tombstones, "add", lambda kind, ident: ({"ok": False, "error": "disk full"}
                                                                if kind == "card_line" else real_add(kind, ident)))

    r = fk.stop_using_source("meeting:m-1")

    nr = _rows(r)["tombstones"]["not_removable"]
    assert nr and "card_line" in nr[0]["what"] and "user_fact" not in nr[0]["what"]
    # The meeting's fact is recorded as deleted, so a later pass does not save it again.
    monkeypatch.setattr(tombstones, "add", real_add)
    assert tombstones.is_tombstoned(("user_fact",), MEET_FACT)


# ── owner ruling 9: forgetting a conversation can delete its transcript ─────
def test_forgetting_a_conversation_can_delete_its_transcript_too(home):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import memory_review
    _conv(tmp, mem, CID, "Boat", [("c1", "user", DAY1, 10, TEA_TURN), ("c2", "user", DAY1, 10, "ok"),
                                  ("c3", "friday", DAY1, 11, FEE_TURN)])
    _conv(tmp, mem, CID2, "Insurance", [("d1", "user", DAY2, 9, PRIYA_TURN)])
    fts.build(force=True)
    # A file kept beside it (an artifact) and a signed receipt that mentions it.
    art = tmp / "artifacts" / CID / "a1"
    art.mkdir(parents=True)
    (art / "v1.json").write_text("{}", encoding="utf-8")
    (tmp / "actions").mkdir(parents=True, exist_ok=True)
    receipts = tmp / "actions" / "receipts.jsonl"
    receipts.write_text(json.dumps({"tool": "draft_email", "conversation_id": CID, "sig": "x"}) + "\n",
                        encoding="utf-8")
    memory_review.note_turn(TEA_TURN, "Noted.", DAY1, start=False)

    r = fk.forget_conversation(CID, delete_transcript=True)

    assert r["ok"] and r["transcript_deleted"] is True
    _shape_ok(r)
    rows = _rows(r)
    # The transcript, its record and its files are gone; every index lets its messages go.
    assert cv.load(CID) is None and not (tmp / "conversations" / CID).exists()
    assert not (tmp / "artifacts" / CID).exists()
    assert not {"c1", "c2", "c3"} & set(mem._collection.rows)
    assert fts.search("mooring") == [] and fts.search("breakfast") == []
    fts.build(force=True)
    assert fts.search("mooring") == []
    assert all(TEA_TURN != ex["user"] for buf in memory_review._BUFFERS.values() for ex in buf)
    # The receipt lists the transcript as removed (counts only) instead of kept.
    assert rows["transcript"]["removed"] == 3 and rows["transcript"]["not_removable"] == []
    assert rows["conversation_recall"]["retained"] == []
    assert "deleted from History" in r["note"] and "cannot be undone" in r["note"]
    # Signed receipts that mention it are kept, and say so.
    kept = rows["action_receipts"]["retained"]
    assert kept and kept[0]["count"] == 1 and "verifiable" in kept[0]["reason"]
    assert CID in receipts.read_text(encoding="utf-8")
    # The other conversation is untouched.
    assert cv.load(CID2) and "d1" in mem._collection.rows and fts.search("insurance")
    dumped = json.dumps(r).lower()
    assert "mooring" not in dumped and "boat" not in dumped
    # It is gone, so a second run is refused.
    assert fk.forget_conversation(CID, delete_transcript=True)["ok"] is False


def test_forgetting_a_conversation_keeps_the_transcript_unless_asked(home):
    tmp, mem, graph = home
    from agent_friday.services import conversations as cv
    from agent_friday.services import forget_kinds as fk
    _conv(tmp, mem, CID, "Boat", [("c1", "user", DAY1, 10, TEA_TURN)])
    # Only a real True deletes: a truthy string from a careless caller does not.
    r = fk.forget_conversation(CID, delete_transcript="yes")
    assert r["transcript_deleted"] is False and "transcript" not in _rows(r)
    assert cv.load(CID) and (tmp / "conversations" / CID / "messages.jsonl").exists()
    assert "stays in History" in r["note"]


def test_deleting_main_empties_it_and_never_imports_the_old_history_again(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import conversations as cv
    from agent_friday.services import forget_kinds as fk
    monkeypatch.setattr(cv, "FRIDAY_DIR", tmp)
    (tmp / "chat_history.json").write_text(json.dumps([{"role": "user", "text": FEE_TURN}]), encoding="utf-8")
    cv.ensure_main()
    assert [m["text"] for m in cv.messages(cv.MAIN_ID)] == [FEE_TURN]

    r = fk.forget_conversation(cv.MAIN_ID, delete_transcript=True)

    assert r["transcript_deleted"] and _rows(r)["transcript"]["removed"] == 1
    assert "Main is emptied" in _rows(r)["transcript"]["kind"]
    assert cv.load(cv.MAIN_ID) is not None and cv.messages(cv.MAIN_ID) == []
    cv.ensure_main()
    assert cv.messages(cv.MAIN_ID) == [], "the old chat_history.json was imported into Main again"


def test_a_transcript_that_cannot_be_deleted_is_reported(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import conversations as cv
    from agent_friday.services import forget_kinds as fk
    _conv(tmp, mem, CID, "Boat", [("c1", "user", DAY1, 10, TEA_TURN)])

    def boom(cid):
        raise OSError("locked")
    monkeypatch.setattr(cv, "delete", boom)
    r = fk.forget_conversation(CID, delete_transcript=True)
    assert r["ok"] and r["transcript_deleted"] is False
    assert _rows(r)["transcript"]["not_removable"] and "could not be deleted" in r["note"]
    # Recall was still stopped, and the transcript is still listed as kept.
    assert "c1" not in mem._collection.rows and _rows(r)["conversation_recall"]["retained"]


@pytest.mark.parametrize("variant", ["CONV-MAIN", "Conv-Main", "conv-main.", "conv-main ", "conv-main. ."])
def test_another_spelling_of_main_is_never_deleted(home, monkeypatch, variant):
    """Windows opens "CONV-MAIN" or "conv-main." as Main's folder: only the
    record's exact id is accepted, and a folder that is Main's is never
    removed under another name."""
    tmp, mem, graph = home
    from agent_friday.services import conversations as cv
    from agent_friday.services import forget_kinds as fk
    _conv(tmp, mem, cv.MAIN_ID, "Main", [("mm1", "user", DAY1, 10, TEA_TURN)])
    main_dir = tmp / "conversations" / cv.MAIN_ID
    before = (main_dir / "messages.jsonl").read_bytes()

    assert fk.forget_conversation(variant, delete_transcript=True)["ok"] is False
    assert cv.delete(variant)["ok"] is False
    # Even when the variant's record claims the variant as its id (the folder is still Main's).
    real_load = cv.load
    monkeypatch.setattr(cv, "load", lambda c: dict(real_load(cv.MAIN_ID) or {}, id=c))
    assert cv.delete(variant)["ok"] is False
    monkeypatch.setattr(cv, "load", real_load)
    assert main_dir.is_dir() and (main_dir / "messages.jsonl").read_bytes() == before
    assert "mm1" in mem._collection.rows and not cv.message_deleted("mm1")


def test_a_delete_that_stops_part_way_can_be_run_again(home, monkeypatch):
    """The indexes go first and the record last, so a file held open leaves
    the conversation loadable, the receipt counts what was left, and running
    it again finishes."""
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv
    from agent_friday.services import forget_kinds as fk
    _conv(tmp, mem, CID, "Boat", [("c1", "user", DAY1, 10, TEA_TURN), ("c2", "friday", DAY1, 11, FEE_TURN)])
    fts.build(force=True)
    held = tmp / "conversations" / CID / "held.bin"
    held.write_bytes(b"\0" * 8)
    real_unlink = cv._unlink
    monkeypatch.setattr(cv, "_unlink", lambda p: False if p.name == "held.bin" else real_unlink(p))

    r = fk.forget_conversation(CID, delete_transcript=True)

    assert r["ok"] and r["transcript_deleted"] is False and "Running it again retries" in r["note"]
    nr = _rows(r)["transcript"]["not_removable"]
    assert nr and nr[0]["count"] == 1 and "running it again retries" in nr[0]["reason"]
    # The indexes went first; the record is kept so it can be found again.
    assert not {"c1", "c2"} & set(mem._collection.rows) and fts.search("mooring") == []
    assert cv.load(CID) is not None and held.exists()
    assert not (tmp / "conversations" / CID / "messages.jsonl").exists()

    monkeypatch.setattr(cv, "_unlink", real_unlink)
    r2 = fk.forget_conversation(CID, delete_transcript=True)

    assert r2["transcript_deleted"] is True and _rows(r2)["transcript"]["not_removable"] == []
    assert cv.load(CID) is None and not (tmp / "conversations" / CID).exists()


def test_deleting_main_clears_the_old_chat_history_rows_without_ids(home, monkeypatch):
    """chat_history.json rows from before message ids were imported into Main
    under new ids: deleting Main lets them go by their text (also those older
    than the 30 days the mirror holds in memory); a row with an id that is
    not Main's stays."""
    tmp, mem, graph = home
    from agent_friday import core
    from agent_friday.services import conversations as cv
    from agent_friday.services import forget_kinds as fk
    monkeypatch.setattr(cv, "FRIDAY_DIR", tmp)
    hist = tmp / "chat_history.json"
    now = datetime.now().isoformat()
    legacy = [{"role": "user", "text": FEE_TURN, "timestamp": now},
              {"role": "friday", "text": TEA_TURN, "timestamp": "2020-01-01T09:00:00"}]
    hist.write_text(json.dumps(legacy), encoding="utf-8")
    cv.ensure_main()
    assert sorted(m["text"] for m in cv.messages(cv.MAIN_ID)) == sorted([FEE_TURN, TEA_TURN])
    # Later the mirror took a turn of another conversation (with its id).
    keep = {"id": "other-1", "role": "user", "text": PRIYA_TURN, "timestamp": now}
    hist.write_text(json.dumps(legacy + [keep]), encoding="utf-8")
    monkeypatch.setattr(core, "CHAT_HISTORY_FILE", hist)
    # The mirror in memory holds the last 30 days only (the 2020 row is on disk alone).
    monkeypatch.setattr(core, "CHAT_HISTORY", [dict(legacy[0]), dict(keep)])

    r = fk.forget_conversation(cv.MAIN_ID, delete_transcript=True)

    assert r["transcript_deleted"]
    rows = json.loads(hist.read_text(encoding="utf-8"))
    assert [x.get("id") for x in rows] == ["other-1"], rows
    assert [x.get("id") for x in core.CHAT_HISTORY] == ["other-1"]
    # A single message deleted in another conversation never takes an id-less row by its text.
    from agent_friday.services import conversation_fts as fts
    core.CHAT_HISTORY.append({"role": "user", "text": PRIYA_TURN, "timestamp": now})
    _conv(tmp, mem, CID2, "Insurance", [("d1", "user", DAY2, 9, PRIYA_TURN)])
    assert fts.delete_message(CID2, "d1")["ok"]
    assert any(x.get("text") == PRIYA_TURN and not x.get("id") for x in core.CHAT_HISTORY)


def test_an_index_that_fails_does_not_stop_the_others(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import conversations as cv
    from agent_friday.services import memory_review
    _conv(tmp, mem, CID, "Boat", [("c1", "user", DAY1, 10, TEA_TURN)])
    memory_review.note_turn(TEA_TURN, "Noted.", DAY1, start=False)

    def locked():
        raise OSError("database is locked")
    monkeypatch.setattr(fts, "_connect", locked)

    out = cv.delete(CID)

    assert out["ok"] and out["index_errors"] == 1
    # The vector memory and the review buffers were still cleaned, and the transcript is gone.
    assert "c1" not in mem._collection.rows
    assert all(TEA_TURN != ex["user"] for buf in memory_review._BUFFERS.values() for ex in buf)
    assert cv.load(CID) is None


def test_a_deleted_conversation_takes_no_late_reply(home, monkeypatch):
    """A reply that finishes after the delete neither writes the conversation
    back nor is indexed; Main refuses what was stamped before its delete."""
    tmp, mem, graph = home
    from agent_friday.services import conversations as cv
    from agent_friday.services import model_router
    _no_arc(monkeypatch)
    _conv(tmp, mem, CID, "Boat", [("c1", "user", DAY1, 10, TEA_TURN)])
    assert cv.delete(CID)["ok"]
    assert cv.was_deleted(CID) and cv.message_deleted("c1")

    cv.append(CID, {"id": "late-u", "role": "user", "text": "And the fee?"})
    cv.append(CID, {"id": "late-f", "role": "friday", "text": FEE_TURN})
    model_router._index_chat_turn_body("And the fee?", FEE_TURN, DAY1, "late-u", "late-f")

    assert cv.load(CID) is None and not (tmp / "conversations" / CID).exists()
    assert not {"late-u", "late-f"} & set(mem._collection.rows)
    # Main: emptied, not removed; a message stamped before the delete is refused, a new one is kept.
    _conv(tmp, mem, cv.MAIN_ID, "Main", [("mm1", "user", DAY1, 10, TEA_TURN)])
    assert cv.delete(cv.MAIN_ID)["ok"]
    cv.append(cv.MAIN_ID, {"id": "old-1", "role": "user", "text": "late", "ts": _ts(DAY1, 11)})
    cv.append(cv.MAIN_ID, {"id": "new-1", "role": "user", "text": "a new message"})
    assert [m["id"] for m in cv.messages(cv.MAIN_ID)] == ["new-1"]
    # Made again on purpose, the id is usable again.
    cv.create(title="Again", cid=CID)
    cv.append(CID, {"id": "again-1", "role": "user", "text": "hello"})
    assert [m["id"] for m in cv.messages(CID)] == ["again-1"]


# ── owner ruling 10: the sites read in chat, a full stop, and using one again ─
SITE = "example.com"
OTHER = "example.org"
WEB_REPLY = "The Example harbour office says high tide on Saturday is at 06:40 and again at 19:05."
OTHER_REPLY = "The Other harbour office lists the mooring waiting list as eleven boats this season."


def _web_results():
    """Fake web tool results, built here: one search, one page, one refused page, one news hit."""
    search = ("Search results for 'tides' (backend: test, 2 results). URLs below are real and fetchable — "
              "pass one verbatim to browse_web:\n\n1. Tides\n   High tide times.\n   https://www.%s/tides?d=1"
              "\n\n2. Harbour\n   Moorings.\n   https://news.%s/harbour" % (SITE, OTHER))
    page = "[https://blog.%s/post/7]\nThe page text." % SITE
    refused = "[CREW DENY] This conversation with a working agent has no tool authority."
    news = json.dumps({"query": "q", "hits": [{"title": "t", "url": "https://www.bbc.co.uk/news/1"}]})
    return [{"name": "search_web", "input": {"query": "tides"}, "result": search},
            {"name": "browse_web", "input": {"url": "https://blog.%s/post/7" % SITE}, "result": page},
            {"name": "browse_web", "input": {"url": "https://refused.example.net/"}, "result": refused},
            {"name": "search_news", "input": {"query": "q"}, "result": news},
            {"name": "read_file", "input": {"path": "x"}, "result": "https://not-a-web-read.example.net/"}]


def test_registered_domains_and_a_turn_s_web_sources(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import off_record
    from agent_friday.services import web_provenance as wp
    wp._reset_for_tests()
    assert wp.registered_domain("https://WWW.Example.com/a/b?c=1") == "example.com"
    assert wp.registered_domain("https://news.bbc.co.uk/x") == "bbc.co.uk"
    assert wp.registered_domain("https://dana.github.io/blog") == "dana.github.io"
    for none in ("https://127.0.0.1/x", "http://localhost/", "co.uk", "", "not a url at all"):
        assert wp.registered_domain(none) == "", none
    # Domains only, from the reads that happened; a gate's refusal and a local file are not web reads.
    assert wp.from_trace(_web_results()) == [SITE, OTHER, "bbc.co.uk"]
    # A tool noted a page for this conversation; the turn takes trace and notes once.
    wp.note(["https://shop.example.net/item?id=9"], CID)
    got = wp.turn_sources(_web_results(), CID)
    assert got == sorted([SITE, OTHER, "bbc.co.uk", "example.net"])
    assert wp.turn_sources(None, CID) == []
    assert all("/" not in d and "?" not in d for d in got)
    # Off the record nothing is noted or recorded.
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    assert wp.note(["https://www.%s/" % SITE], CID) == [] and wp.turn_sources(_web_results(), CID) == []


def _no_arc(monkeypatch):
    from agent_friday.services import model_router

    class _Arc:
        def record(self, *a, **k):
            return None
    monkeypatch.setattr(model_router, "_get_emotional_arc", lambda: _Arc())


def test_a_reply_that_read_the_web_carries_its_sites_into_what_is_learned(home, monkeypatch):
    """(a) The reply's index row, the graph's facts from it, a card suggestion
    from its window and a proposal whose evidence it holds all carry the
    domains, and only the domains."""
    tmp, mem, graph = home
    from agent_friday import conversation_memory as cm_mod
    from agent_friday.services import core_memory
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import memory_review
    from agent_friday.services import model_router
    from agent_friday.services import web_provenance as wp
    from agent_friday.services.knowledge_graph import indexer
    _no_arc(monkeypatch)
    wp._reset_for_tests()
    web = wp.turn_sources(_web_results()[:2], CID)            # the search and the page: example.com, example.org
    assert web == [SITE, OTHER]

    model_router._index_chat_turn_body("When is high tide on Saturday?", WEB_REPLY, DAY1, "u-1", "f-1",
                                       web_sources=web)

    rows = mem._collection.rows
    assert rows["f-1"][1]["web_sources"] == "%s,%s" % (SITE, OTHER)
    assert "web_sources" not in rows["u-1"][1], "the owner's own question was marked as read from the web"
    assert next(r for r in mem.recent(10) if r["text"] == WEB_REPLY)["web_sources"] == [SITE, OTHER]
    # The graph's chunk from that reply carries the domains (they merge into every entity it makes).
    monkeypatch.setattr(cm_mod, "ConversationMemory", lambda *a, **k: mem)
    chunk = next(c for c in indexer._conversation_chunks() if c["text"].startswith("The Example harbour"))
    assert chunk["provenance"]["web_sources"] == [SITE, OTHER]
    # A card suggestion from a window that read the web carries them.
    buf = list(memory_review._BUFFERS[DAY1])
    assert buf[-1]["web_sources"] == [SITE, OTHER]
    memory_review.review(buf, ask=lambda p: json.dumps({"add": [
        {"target": "user", "text": "The user keeps a boat in the Example harbour.", "evidence": "high tide"}],
        "change": [], "remove": []}))
    pend = [r for r in core_memory.pending() if "Example harbour" in r["text"]]
    assert pend and pend[0]["web_sources"] == [SITE, OTHER]
    # A proposal whose evidence is in that reply carries them.
    monkeypatch.setattr(mp, "seat", lambda: {"assigned": True, "model": "m", "provider": "ollama", "reason": ""})
    monkeypatch.setattr(mp, "_ask_seat", lambda prompt, model, provider, **k: json.dumps([
        {"category": "preference", "text": "High tide at the Example harbour is early on Saturdays.",
         "evidence": "high tide on Saturday is at 06:40"},
        {"category": "preference", "text": "The user asks about the tide before sailing.",
         "evidence": "When is high tide"}]))
    out = mp.propose(datetime.now().strftime("%Y-%m-%d"), memory=mem)     # the turn was indexed today
    assert out["stored"] == 2
    conn = mp._connect()
    try:
        prov = dict(conn.execute("SELECT text, provenance FROM proposed_facts").fetchall())
    finally:
        conn.close()
    assert json.loads(prov["High tide at the Example harbour is early on Saturdays."]) == {"web_sources": [SITE, OTHER]}
    assert prov["The user asks about the tide before sailing."] is None


def _seed_site(tmp, mem):
    """A chat that read example.com and another that read example.org, with what was learned from each."""
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import memory_review
    cv_dir = tmp / "conversations"
    from agent_friday.services import conversations as cv
    cv.create(title="Tides", cid=CID)
    lines = [{"id": "u-1", "role": "user", "text": "When is high tide on Saturday?", "ts": _ts(DAY1, 9)},
             {"id": "f-1", "role": "friday", "text": WEB_REPLY, "ts": _ts(DAY1, 9),
              "meta": {"kind": "turn", "web_sources": [SITE]}},
             {"id": "u-2", "role": "user", "text": "And the waiting list?", "ts": _ts(DAY1, 10)},
             {"id": "f-2", "role": "friday", "text": OTHER_REPLY, "ts": _ts(DAY1, 10),
              "meta": {"kind": "turn", "web_sources": [OTHER]}}]
    with open(cv_dir / CID / "messages.jsonl", "w", encoding="utf-8") as fh:
        for m in lines:
            fh.write(json.dumps(m) + "\n")
            mem.index(m["text"], m["role"], session_id=DAY1, timestamp="%sT09:00:00" % DAY1, msg_id=m["id"],
                      web_sources=(m.get("meta") or {}).get("web_sources"))
    # A vector row that read the site with no transcript message (its conversation was cleared).
    mem.index("An older reply that quoted the Example harbour page about winter berths.", "friday",
              session_id=DAY2, timestamp="%sT09:00:00" % DAY2, msg_id="f-old", web_sources=[SITE])
    fts.build(force=True)
    _card(tmp, ["The user sails to Harwich in June."])
    with open(tmp / "core_memory" / "pending.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "pend-web", "ts": 1.0, "target": "user", "op": "add", "old": "", "source": "review",
                             "text": "The user keeps a boat in the Example harbour.", "reason": "",
                             "web_sources": [SITE]}) + "\n")
        fh.write(json.dumps({"id": "pend-own", "ts": 1.0, "target": "user", "op": "add", "old": "", "source": "review",
                             "text": "The user likes jazz on Sundays.", "reason": ""}) + "\n")
    _proposals([("p-web", "High tide at the Example harbour is early on Saturdays.",
                 json.dumps({"web_sources": [SITE]})),
                ("p-other", "The Other harbour has a long waiting list.", json.dumps({"web_sources": [OTHER]})),
                ("p-own", "The user likes jazz on Sundays.", None)])
    _kg(tmp, [{"id": "ent_aaaaaaaaaaaa", "title": "Example Harbour", "type": "place", "description": "d",
               "provenance": {"conversations": ["x1"], "web_sources": [SITE]}},
              {"id": "ent_bbbbbbbbbbbb", "title": "Other Harbour", "type": "place", "description": "d",
               "provenance": {"conversations": ["x2"], "web_sources": [OTHER]}},
              {"id": "ent_cccccccccccc", "title": "Harwich", "type": "place", "description": "d",
               "provenance": {"conversations": ["x1"], "web_sources": [SITE], "wiki_pages": ["places.md"]}}])
    memory_review.note_turn("When is high tide on Saturday?", WEB_REPLY, DAY1, start=False, web_sources=[SITE])
    memory_review.note_turn("And the waiting list?", OTHER_REPLY, DAY1, start=False, web_sources=[OTHER])


def test_stopping_a_site_fully_stops_what_was_read_there_in_chat(home, monkeypatch):
    """(b) Its replies leave recall and search (durably), its graph facts,
    card suggestions and proposals go (refused first), and from then on
    nothing read there is remembered or proposed."""
    tmp, mem, graph = home
    from agent_friday import conversation_memory as cm_mod
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import core_memory
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import memory_review
    from agent_friday.services import model_router
    from agent_friday.services import web_provenance as wp
    from agent_friday.services.knowledge_graph import indexer
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    _no_arc(monkeypatch)
    _seed_site(tmp, mem)
    transcript = (tmp / "conversations" / CID / "messages.jsonl").read_bytes()
    assert fts.search("example") and "f-1" in mem._collection.rows

    where = {s["store"]: s for s in fk.show_everywhere("web:" + SITE)["stores"]}
    assert where["conversation_turns"]["count"] == 2 and where["kg_facts"]["count"] == 1
    assert where["memory_card_pending"]["count"] == 1 and where["memory_proposals"]["count"] == 1

    r = fk.stop_using_source("https://www.%s/tides" % SITE)

    assert r["ok"] and r["source_kind"] == "web"
    _shape_ok(r)
    rows = _rows(r)
    # The replies that read it: out of recall and search, also after a rebuild; the transcript keeps them.
    assert not {"f-1", "f-old"} & set(mem._collection.rows)
    assert {"u-1", "u-2", "f-2"} <= set(mem._collection.rows)
    assert fts.is_forgotten("f-1") and not fts.is_forgotten("f-2") and not fts.is_forgotten("u-1")
    fts.build(force=True)
    assert fts.search("example") == [] and fts.search("waiting")
    assert (tmp / "conversations" / CID / "messages.jsonl").read_bytes() == transcript
    assert rows["conversation_recall"]["removed"] == 2
    assert rows["conversation_recall"]["retained"][0]["count"] == 1
    assert WEB_REPLY in [t for batch in graph for t in batch]
    # Its graph facts go; one also held by the owner's own notes stays, and the receipt says so.
    ids = {e["id"] for e in KnowledgeGraphStore(base_dir=tmp / "kg").load("entities")}
    assert ids == {"ent_bbbbbbbbbbbb", "ent_cccccccccccc"}
    assert rows["kg_facts"]["removed"] == 1 and rows["kg_facts"]["not_removable"][0]["count"] == 1
    # Card suggestions and proposals read there go, and are not suggested again.
    assert [p["id"] for p in core_memory.pending()] == ["pend-own"]
    assert _proposal_ids() == ["p-other", "p-own"]
    assert rows["memory_card_pending"]["removed"] == 1 and rows["memory_proposals"]["removed"] == 1
    assert core_memory.queue("user", "add", text="The user keeps a boat in the Example harbour.",
                             source="review").get("tombstoned")
    assert mp._refused_before("High tide at the Example harbour is early on Saturdays.")
    # The review buffers forget the exchange that read it.
    assert all(WEB_REPLY != ex["friday"] for buf in memory_review._BUFFERS.values() for ex in buf)
    # The receipt says Friday may still open it but remembers nothing from it; no domain or content in it.
    assert "may still open the site" in r["note"] and "nothing read there is remembered" in r["note"]
    dumped = json.dumps(r).lower()
    assert "example" not in dumped and "tide" not in dumped

    # From now on: a reply that reads it is not remembered, searched, reviewed, learned or proposed.
    model_router._index_chat_turn_body("And on Sunday?", "Sunday high tide is at 07:15 per the page.", DAY2,
                                       "u-3", "f-3", web_sources=["blog." + SITE])
    assert "f-3" not in mem._collection.rows and fts.is_forgotten("f-3")
    assert "u-3" in mem._collection.rows, "the owner's own question was dropped"
    assert list(memory_review._BUFFERS[DAY2])[-1]["friday"] == wp.STOPPED_STANDIN
    monkeypatch.setattr(cm_mod, "ConversationMemory", lambda *a, **k: mem)
    mem.index("A reply that read the site before it was stopped, long enough to be indexed.", "friday",
              session_id=DAY2, msg_id="f-late", web_sources=[SITE])
    assert not any("before it was stopped" in c["text"] for c in indexer._conversation_chunks())
    assert mp.web_sources_of("high tide", [{"text": "high tide is early", "web_sources": [SITE]}]) == ([SITE], True)
    assert memory_review.review([{"user": "u", "friday": WEB_REPLY, "owner": True, "web_sources": [SITE]}],
                                ask=lambda p: pytest.fail("a window from a stopped site was reviewed")) == {
        "ok": True, "added": 0, "queued": 0}
    # Another site is learned from as before.
    model_router._index_chat_turn_body("Others?", OTHER_REPLY + " Again.", DAY2, "u-4", "f-4",
                                       web_sources=[OTHER])
    assert mem._collection.rows["f-4"][1]["web_sources"] == OTHER


def test_stopping_part_of_a_site_says_chat_reads_are_kept_by_the_whole_site(home):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    _seed_site(tmp, mem)
    r = fk.stop_using_source("web:news." + SITE)
    rows = _rows(r)
    assert rows["conversation_recall"]["removed"] == 0 and "f-1" in mem._collection.rows
    assert "stop the whole site" in rows["chat_reads"]["not_removable"][0]["reason"]


def test_stopped_sources_are_listed_with_sealed_labels_and_can_be_used_again(home, monkeypatch):
    """(c) The owner sees what they stopped (the name sealed at rest), and Use
    again lets a source be used from now on without bringing anything back."""
    tmp, mem, graph = home
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import credential_store as cs
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import meeting_archive as ma
    monkeypatch.setattr(ma, "remove_meeting", lambda mid, store=None: 0)
    _no_arc(monkeypatch)
    _seed_site(tmp, mem)
    fk.stop_using_source("web:" + SITE)
    fk.stop_using_source("meeting:m-7")

    raw = (tmp / fk.STOPPED_FILE).read_text(encoding="utf-8")
    assert "example" not in raw and "m-7" not in raw and "meeting" not in raw
    entries = json.loads(raw)["entries"]
    assert all(e["label"].startswith("sealed:") for e in entries)
    listed = fk.list_stopped()
    assert listed["ok"]
    by_label = {s["label"]: s for s in listed["sources"]}
    assert set(by_label) == {SITE, "meeting m-7"}
    assert by_label[SITE]["kind"] == "site" and by_label["meeting m-7"]["kind"] == "meeting"
    # A label that cannot be opened (another machine's keystore) shows as the kind and the day.
    real = cs.unprotect
    monkeypatch.setattr(cs, "unprotect", lambda blob: (_ for _ in ()).throw(RuntimeError("no key")))
    day = entries[0]["day"]
    assert {s["label"] for s in fk.list_stopped()["sources"]} == {"a stopped site from " + day,
                                                                 "a stopped meeting from " + day}
    monkeypatch.setattr(cs, "unprotect", real)

    out = fk.unstop(by_label[SITE]["id"])

    assert out["ok"] and "does not come back" in out["note"]
    assert not fk.site_stopped(SITE) and fk.source_stopped("meeting:m-7")
    assert {s["label"] for s in fk.list_stopped()["sources"]} == {"meeting m-7"}
    # Nothing removed comes back; the site is read and learned from again from now on.
    assert fts.is_forgotten("f-1") and "f-1" not in mem._collection.rows
    from agent_friday.services import model_router
    model_router._index_chat_turn_body("Tide?", WEB_REPLY + " Updated.", DAY2, "u-9", "f-9", web_sources=[SITE])
    assert mem._collection.rows["f-9"][1]["web_sources"] == SITE
    assert fk.unstop(by_label[SITE]["id"])["ok"] is False and fk.unstop("../x")["ok"] is False


def test_a_label_that_cannot_be_sealed_is_never_written_in_the_clear(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import credential_store as cs
    from agent_friday.services import forget_kinds as fk

    def refuse(data):
        raise RuntimeError("no way to encrypt on this host")
    monkeypatch.setattr(cs, "protect", refuse)
    assert fk.record_stopped("web:" + SITE)["ok"]
    raw = (tmp / fk.STOPPED_FILE).read_text(encoding="utf-8")
    assert "example" not in raw and "label" not in raw
    src = fk.list_stopped()["sources"][0]
    assert src["label"].startswith("a stopped site from ") and src["named"] is False
    assert fk.site_stopped(SITE)
    # It can still be used again.
    assert fk.unstop(src["id"])["ok"] and not fk.site_stopped(SITE)


def test_an_entry_written_before_ids_and_labels_can_be_listed_and_used_again(home):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    assert fk.record_stopped("web:" + SITE)["ok"]
    data = json.loads((tmp / fk.STOPPED_FILE).read_text(encoding="utf-8"))
    data["entries"] = [{"h": e["h"], "day": e["day"]} for e in data["entries"]]      # the old shape
    (tmp / fk.STOPPED_FILE).write_text(json.dumps(data), encoding="utf-8")
    src = fk.list_stopped()["sources"][0]
    assert src["label"].startswith("a stopped source from ") and fk.site_stopped(SITE)
    assert fk.unstop(src["id"])["ok"] and not fk.site_stopped(SITE)


def test_the_learning_paths_fail_closed_when_the_stopped_record_cannot_be_read(home, monkeypatch):
    """A record that cannot be read: the plain check stays open, but nothing
    read on the web is indexed, reviewed, proposed or taken into the graph;
    a turn that read no web page is learned from as usual."""
    tmp, mem, graph = home
    from agent_friday import conversation_memory as cm_mod
    from agent_friday.services import conversation_fts as fts
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import memory_review
    from agent_friday.services import model_router
    from agent_friday.services import web_provenance as wp
    from agent_friday.services.knowledge_graph import indexer
    _no_arc(monkeypatch)
    (tmp / fk.STOPPED_FILE).write_text("{not json", encoding="utf-8")

    assert wp.stopped([OTHER]) == [] and fk.site_stopped(OTHER) is False
    assert wp.stopped([OTHER], fail_closed=True) == [OTHER] and wp.stopped([], fail_closed=True) == []

    model_router._index_chat_turn_body("Waiting list?", OTHER_REPLY, DAY2, "u-5", "f-5", web_sources=[OTHER])
    assert "f-5" not in mem._collection.rows and fts.is_forgotten("f-5") and "u-5" in mem._collection.rows
    assert list(memory_review._BUFFERS[DAY2])[-1]["friday"] == wp.STOPPED_STANDIN
    model_router._index_chat_turn_body("Tea?", "You like green tea at breakfast, with honey.", DAY2, "u-6", "f-6")
    assert "f-6" in mem._collection.rows
    assert memory_review.review([{"user": "u", "friday": OTHER_REPLY, "owner": True, "web_sources": [OTHER]}],
                                ask=lambda p: pytest.fail("a web window was reviewed")) == {
        "ok": True, "added": 0, "queued": 0}
    assert mp.web_sources_of("waiting list", [{"text": OTHER_REPLY, "web_sources": [OTHER]}]) == ([OTHER], True)
    assert mp.web_sources_of("green tea", [{"text": "You like green tea.", "web_sources": []}]) == ([], False)
    monkeypatch.setattr(cm_mod, "ConversationMemory", lambda *a, **k: mem)
    mem.index("A reply that read the other harbour page about the waiting list this season.", "friday",
              session_id=DAY2, msg_id="f-web", web_sources=[OTHER])
    assert not any("other harbour page" in c["text"] for c in indexer._conversation_chunks())


def test_the_stopped_record_is_read_once_until_it_changes(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    assert fk.record_stopped("web:" + SITE)["ok"]
    real = fk._read_stopped
    reads = []
    monkeypatch.setattr(fk, "_read_stopped", lambda: reads.append(1) or real())

    for _ in range(20):
        assert fk.site_stopped("https://www.%s/a" % SITE) and not fk.site_stopped(OTHER)
    assert len(reads) <= 1, "the record was read on every check"

    # A change to the file (another writer) is seen; so is Friday's own unstop.
    data = json.loads((tmp / fk.STOPPED_FILE).read_text(encoding="utf-8"))
    (tmp / fk.STOPPED_FILE).write_text(json.dumps({"version": 1, "entries": []}), encoding="utf-8")
    assert not fk.site_stopped(SITE)
    (tmp / fk.STOPPED_FILE).write_text(json.dumps(data), encoding="utf-8")
    assert fk.site_stopped(SITE)
    sid = fk.list_stopped()["sources"][0]["id"]
    assert fk.unstop(sid)["ok"] and not fk.site_stopped(SITE)


def test_every_search_result_s_site_counts_as_read(home):
    """The choice web_provenance records: every result search_web returned
    counts, cited or not (a snippet can be learned from), so one stopped
    result holds back the whole reply."""
    tmp, mem, graph = home
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import web_provenance as wp
    wp._reset_for_tests()
    search = _web_results()[0]
    assert wp.turn_sources([search], CID) == sorted([SITE, OTHER])
    assert fk.record_stopped("web:" + OTHER)["ok"]
    assert wp.stopped(wp.turn_sources([search], CID), fail_closed=True) == [OTHER]
    assert "EVERY result" in (wp.__doc__ or "")


# ── the desktop controls ─────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
SHARED_START = "// Forget a fact, a conversation or a source, and look first"
SHARED_END = "// End conversation search panel"


def _ui(name):
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["index.html", "ui_parts/app.html"])
def test_the_forget_controls_are_in_both_ui_files(name):
    html = _ui(name)
    for needle in ("function ForgetThisControls(", "function fridayKgDeleteEntity(", "'Show everywhere'",
                   "'Forget this'", "'/api/memory/forget-fact'", "/api/memory/show-everywhere?q=",
                   "'/api/memory/forget-conversation'", "'/api/knowledge-graph/entity/delete'",
                   "'/api/knowledge-graph/entity/restore'", "'/api/user-model/fact/delete'",
                   "'Forget this conversation…'", "data-user-facts",
                   # Owner ruling 9: the transcript, a separate box with its own confirm.
                   "\"Also delete the transcript (can't be undone)\"", "data-forget-transcript-confirm",
                   "delete_transcript: true, confirm_transcript: true",
                   # Owner ruling 10: the stopped sources, each with Use again.
                   "function StoppedSourcesPanel(", "'/api/memory/stopped-sources'", "'Use again'",
                   "/unstop'"):
        assert needle in html, "%s: %s missing" % (name, needle)
    card = html.split("function MemoryCardPanel(")[1].split("// End memory card panel")[0]
    assert "E(ForgetThisControls" in card and "delFact(" in card
    assert "E(StoppedSourcesPanel" in card, "the stopped sources are not in the memory panel"
    # Deleting the transcript is reached only through its own confirm step.
    search = html.split("function ConversationSearchPanel(")[1].split("// End conversation search panel")[0]
    assert search.count("forgetConv(h, true)") == 1
    assert search.index("ask === 't' + h.message_id") < search.index("forgetConv(h, true)")
    # A Delete on a graph entity in both graph views: the Knowledge workspace bar and the graph's own panel.
    assert html.count("fridayKgDeleteEntity(sel,") == 1 and html.count("fridayKgDeleteEntity(selected") == 1
    assert html.count("data-kg-delete") >= 2
    assert "node_removed" in html


def test_the_shared_forget_components_are_identical_in_both_files():
    blocks = []
    for name in ("index.html", "ui_parts/app.html"):
        html = _ui(name)
        start = html.index(SHARED_START)
        blocks.append(html[start:html.index(SHARED_END, start)])
    assert blocks[0] == blocks[1], "the forget controls drifted between index.html and ui_parts/app.html"

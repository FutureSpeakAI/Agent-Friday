"""Forget a fact, a conversation or a source, and see where something is first.

North star NS-15.7-1: "Forget fact/person/conversation/source and 'show
everywhere' requests supported." Forget-person (M9) and the Library's own
forget existed; these are the other three forgets and the read-only look
(services/forget_kinds, routes/memory_forget).

Through the real entry points, on synthetic data:

* forget_fact removes the statement from the memory card, its review queue,
  the older user model, proposals, the dreaming log and the graph's fact node;
  the turns that stated it leave recall and search (not after a rebuild
  either) while the transcript keeps them; the day summary made from them is
  queued again; tombstones stop every derived path making it again, and the
  owner can still say it; unrelated lines, facts and turns are untouched.
* forget_conversation takes every turn of one conversation out of recall and
  search (a short one too, by its id), keeps the transcript byte for byte,
  queues its day and its graph turns, and leaves another conversation alone.
* stop_using_source handles a meeting, a site, a sender and a Library
  document, records a stopped meeting or site without its id or domain, and
  says "not supported yet" for what it cannot do.
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
TEA_TURN = "I drink green tea at breakfast every day, no sugar."
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
    yield tmp_path, mem, graph
    tombstones._reset_for_tests()


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

    receipt = fk.forget_fact("  the user prefers green tea at breakfast  ")

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
    assert "green" not in dumped and "breakfast" not in dumped and "sugar" not in dumped


def test_a_forgotten_fact_is_not_made_again_but_the_owner_can_still_say_it(home):
    tmp, mem, graph = home
    from agent_friday.services import core_memory
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import memory_proposals as mp
    from agent_friday.services import user_model
    from agent_friday.services.knowledge_graph import integration
    _seed_tea(tmp, mem)

    r = fk.forget_fact(TEA)

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

    r = fk.forget_fact("Likes jazz.")

    assert core_memory.entries("user") == ["The user sails to Harwich in June."]
    row = _rows(r)["conversation_recall"]
    assert row["removed"] == 0 and "fewer than 3 meaningful words" in row["not_removable"][0]["reason"]
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


# ── stop using a source ──────────────────────────────────────────────────────
MEET_FACT = "The user agreed to ship the Atlas beta on the 14th."
OTHER_MEET_FACT = "The user owns the Mercury budget review."


def test_stopping_a_meeting_removes_what_came_from_it_and_nothing_new_comes_in(home):
    tmp, mem, graph = home
    from agent_friday.services import core_memory
    from agent_friday.services import forget_kinds as fk
    from agent_friday.services import memory_proposals as mp
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
    _shape_ok(r)
    ids = {e["id"] for e in KnowledgeGraphStore(base_dir=tmp / "kg").load("entities")}
    assert ids == {"fact_bbbbbbbbbbbb", "fact_cccccccccccc", "fact_dddddddddddd"}
    rows = _rows(r)
    assert rows["kg_facts"]["removed"] == 1
    assert "not supported yet" in rows["conversation_recall"]["not_removable"][0]["reason"]
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
    r = _rows(fk.stop_using_source("Dana@Example.com"))
    assert seen == ["dana@example.com"] and r["sender_signals"]["removed"] == 1
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
    assert where["tombstones"]["count"] == 0
    # After the forget it is found nowhere, and recorded as deleted.
    fk.forget_fact(TEA)
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
                   "'Forget this conversation…'", "data-user-facts"):
        assert needle in html, "%s: %s missing" % (name, needle)
    card = html.split("function MemoryCardPanel(")[1].split("// End memory card panel")[0]
    assert "E(ForgetThisControls" in card and "delFact(" in card
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

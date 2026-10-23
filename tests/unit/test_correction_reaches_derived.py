"""A correction reaches what was made from the turn earlier (north star
§34.9-3/-4): the daily summary of that day is taken away at once and written
again without the corrected turn, and the knowledge graph drops what only
that turn supported. A restore re-queues the summary. Synthetic data; the real
ConversationMemory over an in-memory collection (CI installs no Chroma)."""
from __future__ import annotations

import hashlib
import json
import re

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
    import agent_friday.paths as paths
    from agent_friday.conversation_memory import ConversationMemory
    from agent_friday.services import model_router
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(model_router, "SESSION_SUMMARY_DIR", tmp_path / "memory" / "session_summaries")
    graph = []
    monkeypatch.setattr(md, "queue_graph_retraction", lambda texts: graph.append(list(texts)) or {})
    mem = ConversationMemory(persist_dir=tmp_path / "conv_mem")
    mem._collection = FakeCollection()
    monkeypatch.setattr(mem, "_assert_embedding_space", lambda: None)
    monkeypatch.setattr(mem, "_shared_query_ok", lambda: False)
    monkeypatch.setattr(model_router, "_get_conversation_memory", lambda: mem)
    return tmp_path, mem, graph


def _summary(day, text):
    from agent_friday.services import model_router
    model_router._save_session_summary(day, text)
    return model_router._session_summary_path(day, "md")


def test_a_correction_takes_the_day_s_summary_away_and_queues_it_again(home, monkeypatch):
    tmp, mem, graph = home
    from agent_friday.services import model_router
    mem.index("Our mooring fee is 400 pounds a year.", "user", session_id="2026-09-01",
              timestamp="2026-09-01T10:00:00", msg_id="m-fee")
    old = _summary("2026-09-01", "The owner pays 400 pounds a year for the mooring.")
    other = _summary("2026-09-02", "A walk by the harbour.")
    assert old.exists()
    mem.supersede(["m-fee"], "the owner corrected the fee")
    assert md.drain()
    assert not old.exists(), "the day's summary still states the corrected fact"
    assert other.exists()
    assert list(json.loads((tmp / "memory" / "session_summaries" / "redo.json").read_text())) == ["2026-09-01"]
    assert graph == [["Our mooring fee is 400 pounds a year."]]

    written = []
    def _fail(d, force=False):
        written.append((d, force))
        return None                                  # the model was not there
    monkeypatch.setattr(model_router, "_generate_session_summary", _fail)
    assert md.redo_summaries() == 0, "a day whose summary was not written left the queue"
    assert list(json.loads((tmp / "memory" / "session_summaries" / "redo.json").read_text())) == ["2026-09-01"]

    def _write(d, force=False):
        written.append((d, force))
        model_router._save_session_summary(d, "The mooring fee was discussed.")
        return "ok"
    monkeypatch.setattr(model_router, "_generate_session_summary", _write)
    assert md.redo_summaries() == 1 and written[-1] == ("2026-09-01", True)
    assert json.loads((tmp / "memory" / "session_summaries" / "redo.json").read_text()) == {}


def test_a_restore_requeues_the_summary_but_takes_nothing_from_the_graph(home):
    tmp, mem, graph = home
    mem.index("Our mooring fee is 520 pounds a year.", "user", session_id="2026-09-03",
              timestamp="2026-09-03T10:00:00", msg_id="m-fee2")
    mem.supersede(["m-fee2"], "x")
    assert md.drain()
    graph.clear()
    _summary("2026-09-03", "Summary written while the turn was corrected.")
    mem.unsupersede(["m-fee2"])
    assert md.drain()
    assert graph == []
    assert "2026-09-03" in json.loads((tmp / "memory" / "session_summaries" / "redo.json").read_text())


def test_the_graph_drops_what_only_the_corrected_turn_said(tmp_path, monkeypatch):
    import agent_friday.conversation_memory as cmod
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore

    class NoIndex:
        persist_dir = tmp_path / "no-index"                # never created: nothing to delete
    monkeypatch.setattr(cmod, "get_conversation_memory", lambda: NoIndex())
    store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
    text = "Peter Smallwood is the harbour master at Harwich and has been for years."
    tid = hashlib.sha1(text.encode()).hexdigest()[:10]
    store.save("entities", [
        {"id": "ent_peter", "title": "Peter Smallwood", "type": "person", "description": "Harbour master",
         "tier": "B", "provenance": {"conversations": [tid], "sensitivity": 1}},
        {"id": "ent_harwich", "title": "Harwich", "type": "place", "description": "A port where Peter works",
         "tier": "B", "provenance": {"conversations": [tid], "wiki_pages": ["notes/places.md"], "sensitivity": 1}},
        {"id": "fact_0001", "title": "Owner's fact", "type": "fact", "description": "Kept",
         "tier": "B", "provenance": {"conversations": [tid], "sensitivity": 1}},
    ])
    store.save("relationships", [
        {"id": "rel_peter_harwich", "source": "ent_peter", "target": "ent_harwich", "description": "works at",
         "tier": "B", "provenance": {"conversations": [tid], "sensitivity": 1}},
    ])
    from agent_friday.services.knowledge_graph import indexer
    out = indexer.purge_conversation_turns(md._turn_ids([text]), store=store)
    ents = {e["id"]: e for e in store.load("entities")}
    assert "ent_peter" not in ents, "a node only the corrected turn supported is still there"
    assert ents["ent_harwich"]["description"] == "" and ents["ent_harwich"]["provenance"]["wiki_pages"]
    assert "fact_0001" in ents, "an approved fact was touched"
    assert store.load("relationships") == []
    assert out["entities"] == 1 and out["described_again"] >= 1 and not out["refused"]


def test_a_correction_during_a_graph_pass_waits_for_the_pass(tmp_path, monkeypatch):
    from agent_friday.routes import knowledge_graph as kg_routes
    monkeypatch.setattr(md, "_pending_path", lambda: tmp_path / "kg-retract-pending.json")
    applied = []
    monkeypatch.setattr(md, "apply_pending_graph_retractions",
                        lambda store=None, **k: applied.append(sorted(md._read_pending())) or {})
    monkeypatch.setitem(kg_routes._TIER_B_STATE, "running", True)
    out = md.queue_graph_retraction(["A turn long enough to have been given a graph id."])
    assert out.get("deferred") and applied == [] and len(md._read_pending()) == 1
    monkeypatch.setitem(kg_routes._TIER_B_STATE, "running", False)
    md.queue_graph_retraction(["Another corrected turn of a decent length."])
    assert applied and len(applied[-1]) == 2


def test_a_pass_that_starts_during_the_purge_keeps_the_ids_queued(tmp_path, monkeypatch):
    from agent_friday.routes import knowledge_graph as kg_routes
    from agent_friday.services.knowledge_graph import indexer
    monkeypatch.setattr(md, "_pending_path", lambda: tmp_path / "kg-retract-pending.json")
    monkeypatch.setitem(kg_routes._TIER_B_STATE, "running", False)
    monkeypatch.setitem(kg_routes._TIER_B_STATE, "started_at", 1.0)

    def purge(ids, store=None):
        kg_routes._TIER_B_STATE["started_at"] = 2.0       # a pass began meanwhile
        return {"entities": 0, "refused": False}
    monkeypatch.setattr(indexer, "purge_conversation_turns", purge)
    md.queue_graph_retraction(["A corrected turn that a starting pass may still hold."])
    assert len(md._read_pending()) == 1, "the ids were cleared though a pass may hold the old graph"

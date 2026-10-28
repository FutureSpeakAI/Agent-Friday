"""The Doctor repairs the conversation vector store and the knowledge graph (BD-16; 32.5,
NS-32.6-1). FT4 (#404) did the search index; these are the other two stores Friday recalls from.

* A damaged vector store is found and said; the owner's press rebuilds it from the
  transcripts; if the rebuild fails, the old store is put back exactly as it was.
* A knowledge-graph file that does not parse used to read as empty and be written over at the
  next save. Now it is said, and it is kept aside before anything is written over it.

No ChromaDB, no model: the vector store is a stand-in with the same few methods.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import recall_health


@pytest.fixture
def msr():
    try:
        from agent_friday.services import memory_store_repair
    except ImportError:
        pytest.fail("the Doctor has no repair for Friday's other memory stores: "
                    "services/memory_store_repair does not exist")
    return memory_store_repair


@pytest.fixture(autouse=True)
def _clean():
    recall_health._reset()
    yield
    recall_health._reset()


class FakeMemory:
    def __init__(self, persist_dir, *, rebuild=None, init_error=None):
        self.persist_dir = persist_dir
        self._init_error = init_error
        self._init_attempted = init_error is not None
        self._rebuild = rebuild or (lambda: 7)

    def _ensure(self):
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        (self.persist_dir / "chroma.sqlite3").write_bytes(b"")     # a fresh, empty store
        return True

    def rebuild_from_transcripts(self, progress=None):
        return self._rebuild()


@pytest.fixture
def vec(tmp_path, monkeypatch, msr):
    from agent_friday import conversation_memory as cm
    d = tmp_path / "memory" / "conversations"
    state = {"mem": FakeMemory(d), "closed": 0, "next": None}

    def get(persist_dir=None):
        if state["mem"] is None:
            state["mem"] = state["next"] or FakeMemory(d)
        return state["mem"]

    def close():
        state["closed"] += 1
        state["mem"] = None
    monkeypatch.setattr(cm, "get_conversation_memory", get)
    monkeypatch.setattr(cm, "close_conversation_memory", close)
    return {"dir": d, "state": state}


def _damage_store(d):
    d.mkdir(parents=True, exist_ok=True)
    (d / "chroma.sqlite3").write_bytes(b"this is not a database at all " * 200)
    (d / "index.bin").write_bytes(b"old vectors")


def _receipts(target):
    from tests import ledger_support
    return [x.get("decision") for x in ledger_support.decisions()
            if x.get("tool") == "doctor_repair" and x.get("target") == target]


# ── the conversation vector store ─────────────────────────────────────────────

def test_a_damaged_vector_store_is_found_and_said(vec, msr):
    _damage_store(vec["dir"])
    assert msr.vector_health()["state"] == "damaged"
    from agent_friday.services import doctor
    state, code = doctor._c_vector_memory()
    assert state == "degraded" and code.startswith("conversation_memory_")


def test_a_missing_chromadb_is_not_damage(vec, msr):
    vec["state"]["mem"] = FakeMemory(vec["dir"], init_error=ImportError("no chromadb"))
    assert msr.vector_health()["state"] == "unavailable"


def test_the_rebuild_comes_from_the_transcripts_and_the_old_store_goes(vec, msr):
    _damage_store(vec["dir"])
    recall_health.note_failure("past_conversations", RuntimeError("x"))
    before = len(_receipts("vector_memory"))
    out = msr.repair_vector("owner:doctor")
    assert out == {"ok": True, "rows": 7}
    assert not (vec["dir"] / "index.bin").exists(), "the damaged store is still there"
    assert not list(vec["dir"].parent.glob("conversations.rebuilding-*")), "a copy of old text was kept"
    assert msr.vector_health()["state"] == "ok"
    assert "past_conversations" not in recall_health.degraded()
    assert _receipts("vector_memory")[before:] == ["rebuild_started", "rebuilt"]


def test_a_rebuild_that_fails_puts_the_old_store_back_as_it_was(vec, msr):
    _damage_store(vec["dir"])
    old = {p.name: p.read_bytes() for p in vec["dir"].iterdir()}

    def boom():
        raise RuntimeError("embedding model missing")
    vec["state"]["next"] = FakeMemory(vec["dir"], rebuild=boom)
    before = len(_receipts("vector_memory"))
    out = msr.repair_vector("owner:doctor")
    assert out["ok"] is False and "put back" in out["error"]
    assert {p.name: p.read_bytes() for p in vec["dir"].iterdir()} == old
    assert _receipts("vector_memory")[before:] == ["rebuild_started", "rebuild_failed"]


def test_one_vector_rebuild_at_a_time(vec, msr):
    msr._VEC_REPAIRING.set()
    try:
        assert msr.repair_vector("owner:doctor")["ok"] is False
        assert msr.vector_health()["state"] == "repairing"
    finally:
        msr._VEC_REPAIRING.clear()


# ── the knowledge graph ───────────────────────────────────────────────────────

@pytest.fixture
def kg(tmp_path, monkeypatch):
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    base = tmp_path / "knowledge-graph"
    base.mkdir()
    try:
        from agent_friday.services import memory_store_repair
        monkeypatch.setattr(memory_store_repair, "_kg_dir", lambda: base)
    except ImportError:
        pass
    return {"base": base, "store": KnowledgeGraphStore(base_dir=base)}


DAMAGED = b'[{"id": "p1", "name": "Council budget", "tier": "A"}, {"id": "p2", "na'


def test_a_damaged_graph_file_is_said_not_read_as_silence(kg):
    (kg["base"] / "entities.json").write_bytes(DAMAGED)
    assert kg["store"].load("entities") == []
    assert "knowledge_graph" in recall_health.degraded(), "a damaged graph file read as empty, silently"


def test_a_save_never_writes_over_a_damaged_graph_file(kg):
    (kg["base"] / "entities.json").write_bytes(DAMAGED)
    kg["store"].save("entities", [{"id": "n1", "name": "new", "tier": "A"}])
    kept = list(kg["base"].glob("entities.json.damaged-*"))
    assert len(kept) == 1 and kept[0].read_bytes() == DAMAGED, "what the damaged file held is gone"
    assert json.loads((kg["base"] / "entities.json").read_text(encoding="utf-8"))[0]["id"] == "n1"


def test_the_graph_repair_keeps_the_damaged_file_and_rebuilds_the_wiki_layer(kg, msr, monkeypatch):
    (kg["base"] / "relationships.json").write_bytes(DAMAGED)
    from agent_friday.services.knowledge_graph import wiki_graph
    ran = []
    monkeypatch.setattr(wiki_graph, "rebuild_tier_a", lambda *a, **k: ran.append(1) or {"entities": 3})
    recall_health.note_failure("knowledge_graph", ValueError("x"))
    assert msr.kg_health() == {"state": "damaged", "damaged": ["relationships"], "kept": 0}
    before = len(_receipts("knowledge_graph"))
    out = msr.repair_kg("owner:doctor")
    assert out["ok"] is True and ran == [1] and len(out["kept"]) == 1
    assert (kg["base"] / out["kept"][0]).read_bytes() == DAMAGED
    assert "knowledge_graph" not in recall_health.degraded()
    assert _receipts("knowledge_graph")[before:] == ["rebuild_started", "rebuilt"]
    from agent_friday.services import doctor
    assert doctor._c_knowledge_graph()[0] == "healthy"


def test_the_graph_repair_runs_no_model(kg, msr, monkeypatch):
    from agent_friday.services.knowledge_graph import indexer, wiki_graph
    monkeypatch.setattr(indexer, "reindex_tier_b",
                        lambda *a, **k: pytest.fail("the repair re-ran model extraction"))
    monkeypatch.setattr(wiki_graph, "rebuild_tier_a", lambda *a, **k: {})
    assert msr.repair_kg("owner:doctor")["ok"] is True

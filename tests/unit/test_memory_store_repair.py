"""The Doctor watches the conversation vector store and repairs the knowledge graph (BD-16;
32.5, NS-32.6-1). FT4 (#404) did the search index; these are the other two stores Friday
recalls from.

* A damaged vector store is found and said. Its rebuild is a later slice: nothing is deleted.
* A knowledge-graph file that does not parse used to read as empty and be written over at the
  next save. Now it is said, it is kept aside before anything is written over it, the owner's
  press rebuilds the wiki layer (running no model), and a forget or purge removes the kept
  copies so nothing forgotten survives in them.

No ChromaDB, no model: the vector store is a stand-in with the same few attributes.
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
        pytest.fail("the Doctor watches none of Friday's other memory stores: "
                    "services/memory_store_repair does not exist")
    return memory_store_repair


@pytest.fixture(autouse=True)
def _clean():
    recall_health._reset()
    yield
    recall_health._reset()


class FakeMemory:
    def __init__(self, persist_dir, *, init_error=None):
        self.persist_dir = persist_dir
        self._init_error = init_error
        self._init_attempted = init_error is not None


@pytest.fixture
def vec(tmp_path, monkeypatch):
    from agent_friday import conversation_memory as cm
    d = tmp_path / "memory" / "conversations"
    box = {"mem": FakeMemory(d)}
    monkeypatch.setattr(cm, "get_conversation_memory", lambda persist_dir=None: box["mem"])
    return {"dir": d, "box": box}


def _receipts(target):
    from tests import ledger_support
    return [x.get("decision") for x in ledger_support.decisions()
            if x.get("tool") == "doctor_repair" and x.get("target") == target]


# ── the conversation vector store: found and said ────────────────────────────

def test_a_damaged_vector_store_is_found_and_said_and_nothing_is_deleted(vec, msr):
    d = vec["dir"]
    d.mkdir(parents=True)
    (d / "chroma.sqlite3").write_bytes(b"this is not a database at all " * 200)
    (d / "index.bin").write_bytes(b"vectors")
    assert msr.vector_health()["state"] == "damaged"
    from agent_friday.services import doctor
    state, code = doctor._c_vector_memory()
    assert state == "degraded" and code.startswith("conversation_memory_")
    assert (d / "index.bin").read_bytes() == b"vectors"


def test_a_store_that_would_not_open_is_damage_but_a_missing_chromadb_is_not(vec, msr):
    vec["box"]["mem"] = FakeMemory(vec["dir"], init_error=RuntimeError("bad header"))
    assert msr.vector_health()["state"] == "damaged"
    vec["box"]["mem"] = FakeMemory(vec["dir"], init_error=ImportError("no chromadb"))
    assert msr.vector_health()["state"] == "unavailable"


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
    assert _receipts("knowledge_graph")[before:] == ["rebuild_started", "kept_damaged_file", "rebuilt"]
    from agent_friday.services import doctor
    assert doctor._c_knowledge_graph()[0] == "healthy"


def test_the_graph_repair_runs_no_model(kg, msr, monkeypatch):
    from agent_friday.services.knowledge_graph import indexer, wiki_graph
    monkeypatch.setattr(indexer, "reindex_tier_b",
                        lambda *a, **k: pytest.fail("the repair re-ran model extraction"))
    monkeypatch.setattr(wiki_graph, "rebuild_tier_a", lambda *a, **k: {})
    assert msr.repair_kg("owner:doctor")["ok"] is True


def test_a_rebuild_that_could_not_write_is_not_called_done(kg, msr, monkeypatch):
    from agent_friday.services.knowledge_graph import wiki_graph

    def refusing(store=None, wiki_dir=None):
        store.refused.append({"artifact": "entities", "reason": "cannot_decrypt"})
        return {}
    monkeypatch.setattr(wiki_graph, "rebuild_tier_a", refusing)
    out = msr.repair_kg("owner:doctor")
    assert out["ok"] is False and out["refused"]


def test_one_graph_repair_at_a_time(kg, msr):
    assert msr.begin_kg_repair() is True
    try:
        assert msr.begin_kg_repair() is False
        assert msr.repair_kg("owner:doctor")["ok"] is False
    finally:
        msr._KG_REPAIRING.clear()


def test_a_forget_removes_the_kept_copies(kg, msr):
    (kg["base"] / "entities.json").write_bytes(DAMAGED)
    kg["store"].save("entities", [])
    assert list(kg["base"].glob("entities.json.damaged-*"))
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphManifest
    m = KnowledgeGraphManifest(base_dir=kg["base"])
    m.forget("C:/somewhere/capped.md", scrub=False)          # routine bookkeeping in a pass
    assert list(kg["base"].glob("entities.json.damaged-*")), "bookkeeping threw away a kept copy"
    m.forget("C:/somewhere/notes.md")
    assert not list(kg["base"].glob("*.damaged-*")), "a forgotten document survives in a kept copy"


def test_a_library_purge_also_removes_a_damaged_file_it_cannot_read(kg, msr, monkeypatch):
    from agent_friday.services.knowledge_graph import indexer
    from agent_friday.services.knowledge_graph import store as kgs
    monkeypatch.setattr(kgs.KnowledgeGraphStore, "__init__",
                        lambda self, base_dir=None, _i=kgs.KnowledgeGraphStore.__init__: _i(self, kg["base"]))
    (kg["base"] / "entities.json").write_bytes(DAMAGED)
    indexer.purge_all_library()
    assert not (kg["base"] / "entities.json").exists(), "Library words may survive in a damaged file"

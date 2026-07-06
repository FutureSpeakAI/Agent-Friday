"""Phase 2b — retrieval: local/global/drift + router, all with stub LLMs."""

import json
from pathlib import Path

import pytest

from agent_friday.services.knowledge_graph import retrieval
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore


@pytest.fixture
def seeded_store(tmp_path, monkeypatch):
    # Pin the vault key: ent_bbb is TIER_2, so without a key the store's
    # fail-closed path (correctly) withholds it — and _get_vault_key() is
    # process-cached, so full-suite runs inherit whatever an earlier test
    # left there. The suite for that behavior is test_knowledge_graph_store.
    from agent_friday.services.knowledge_graph import store as store_mod
    monkeypatch.setattr(store_mod, "_vault_key", lambda: b"k" * 32)
    store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
    store.save("entities", [
        {"id": "ent_aaa", "title": "GraphRAG", "type": "concept",
         "description": "Builds knowledge graphs from text.", "degree": 1,
         "frequency": 2, "tier": "B", "community": "B0",
         "provenance": {"wiki_pages": ["research/graphrag.md"],
                        "sensitivity": 1}},
        {"id": "ent_bbb", "title": "Friday", "type": "tool",
         "description": "The assistant hosting the graph.", "degree": 1,
         "frequency": 3, "tier": "B", "community": "B0",
         "provenance": {"conversations": ["t1"], "sensitivity": 2}},
    ])
    store.save("relationships", [
        {"id": "rel_ab", "source": "ent_aaa", "target": "ent_bbb",
         "description": "Friday uses GraphRAG", "weight": 0.8, "tier": "B",
         "provenance": {"sensitivity": 1}},
    ])
    store.save("communities", [])
    store.save("community_reports", [
        {"id": "brep_0", "community": "B0", "level": 0, "tier": "B",
         "title": "Knowledge tooling",
         "summary": "How Friday builds its second brain.",
         "full_content": "Friday uses GraphRAG for retrieval.",
         "rank": 8.0, "provenance": {"sensitivity": 1}},
    ])
    return store


class StubLLM:
    def __init__(self, reply="stub answer"):
        self.reply = reply
        self.calls = []

    def __call__(self, messages, system, sensitivity, mode, orb_label=None):
        self.calls.append({"system": system or "",
                           "question": messages[0]["content"],
                           "sensitivity": sensitivity})
        return self.reply


class TestLocalSearch:
    def test_answers_with_provenance(self, seeded_store):
        llm = StubLLM("GraphRAG powers Friday's brain.")
        out = retrieval.local_search("what is graphrag?",
                                     store=seeded_store, llm=llm)
        assert out["answer"] == "GraphRAG powers Friday's brain."
        assert any(e["title"] == "GraphRAG" for e in out["entities"])
        assert out["entities"][0]["provenance"]
        # entity context reached the prompt
        assert "GraphRAG" in llm.calls[0]["system"]

    def test_sensitivity_escalates_with_entities(self, seeded_store):
        llm = StubLLM()
        retrieval.local_search("tell me about friday", store=seeded_store,
                               llm=llm)
        # Friday entity is TIER_2 → the call must be classified >= 2
        assert llm.calls[0]["sensitivity"] >= 2

    def test_empty_index_degrades(self, tmp_path):
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        out = retrieval.local_search("anything", store=store, llm=StubLLM())
        assert out["answer"] is None


class TestGlobalSearch:
    def test_map_reduce_over_reports(self, seeded_store):
        map_reply = json.dumps({"points": [
            {"description": "Friday relies on GraphRAG", "score": 90}]})

        class MapReduceLLM(StubLLM):
            def __call__(self, messages, system, sensitivity, mode,
                         orb_label=None):
                super().__call__(messages, system, sensitivity, mode)
                if "{report_data}" not in (system or "") and \
                        "Friday relies" in (system or ""):
                    return "Reduced: GraphRAG is central."
                if "analyst" in (system or "").lower() or "points" in \
                        (system or "").lower():
                    return map_reply
                return map_reply

        out = retrieval.global_search("what are the big themes?",
                                      store=seeded_store, llm=MapReduceLLM())
        assert out["answer"]
        assert out["reports_used"] == ["Knowledge tooling"]
        assert out["points"][0]["score"] == 90

    def test_no_reports_notes_reindex(self, tmp_path):
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        out = retrieval.global_search("themes?", store=store, llm=StubLLM())
        assert out["answer"] is None
        assert "reindex" in out["note"]


class TestRouter:
    def test_thematic_routes_global(self, seeded_store, monkeypatch):
        monkeypatch.setattr(retrieval.structural_query, "query",
                            lambda q: {"answer_type": "thematic",
                                       "index_only": False, "candidates": [],
                                       "path": [], "should_read": []})
        out = retrieval.route_query("what's the big picture of my knowledge?",
                                    store=seeded_store, llm=StubLLM('{"points":[]}'))
        assert out["mode"] == "global"

    def test_path_routes_structural_free(self, seeded_store, monkeypatch):
        monkeypatch.setattr(retrieval.structural_query, "query",
                            lambda q: {"answer_type": "path",
                                       "index_only": False, "candidates": [],
                                       "path": ["a", "b"], "should_read": []})
        llm = StubLLM()
        out = retrieval.route_query("how is a related to b?",
                                    store=seeded_store, llm=llm)
        assert out["mode"] == "structural"
        assert llm.calls == []              # zero LLM for path questions

    def test_forced_mode_wins(self, seeded_store, monkeypatch):
        monkeypatch.setattr(retrieval.structural_query, "query",
                            lambda q: {"answer_type": "direct",
                                       "index_only": True, "candidates": [],
                                       "path": [], "should_read": []})
        out = retrieval.route_query("what is graphrag?", mode="local",
                                    store=seeded_store, llm=StubLLM("x"))
        assert out["mode"] == "local"
        assert out["answer"] == "x"

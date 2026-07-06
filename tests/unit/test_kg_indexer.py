"""Phase 2a — Tier B indexer: parsing, merging, routing, incrementality.

All LLM traffic is a recorded stub — nothing leaves the process.
"""

import json
from pathlib import Path

import pytest

from agent_friday.services.knowledge_graph import indexer
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore


CANNED_EXTRACTION = (
    '("entity"<|>GRAPHRAG<|>concept<|>GraphRAG builds knowledge graphs from text)\n##\n'
    '("entity"<|>FRIDAY<|>tool<|>Friday is the assistant that hosts the graph)\n##\n'
    '("relationship"<|>GRAPHRAG<|>FRIDAY<|>Friday uses GraphRAG for its second brain<|>8)\n'
    '<|COMPLETE|>')

CANNED_REPORT = json.dumps({
    "title": "Knowledge tooling",
    "summary": "A cluster about Friday's knowledge machinery.",
    "rating": 7.5,
    "rating_explanation": "central to the product",
    "findings": [{"summary": "GraphRAG is core",
                  "explanation": "it powers retrieval"}],
})


class RecordingLLM:
    """Stub for the indexer's injectable llm(messages, system, sens, mode)."""

    def __init__(self):
        self.calls = []

    def __call__(self, messages, system, sensitivity, mode, orb_label=None):
        self.calls.append({"sensitivity": sensitivity, "mode": mode,
                           "text": messages[0]["content"],
                           "system": system})
        if "-Goal-" in messages[0]["content"]:
            return CANNED_EXTRACTION
        if "community" in (messages[0]["content"] or "").lower():
            return CANNED_REPORT
        return "merged description"


@pytest.fixture
def wiki_home(tmp_path, monkeypatch):
    """Point the indexer at a tiny wiki inside the hermetic home."""
    wiki = Path.home() / ".friday" / "wiki"
    (wiki / "research").mkdir(parents=True, exist_ok=True)
    (wiki / "research" / "notes.md").write_text(
        "# Notes\n\nGraphRAG and Friday work together.\n", encoding="utf-8")
    # keep the corpus to wiki-only for determinism
    import agent_friday.services.knowledge_graph as kg
    monkeypatch.setattr(kg, "_load_settings", lambda: {
        "knowledge_graph": {"index_sources": {
            "wiki": True, "soul": False, "cognitive": False,
            "conversations": False}}})
    return wiki


class TestParsing:
    def test_parse_extraction_tuples(self):
        ents, rels = indexer.parse_extraction(CANNED_EXTRACTION)
        assert {e["title"] for e in ents} == {"GRAPHRAG", "FRIDAY"}
        assert rels[0]["source"] == "GRAPHRAG"
        assert rels[0]["weight"] == 0.8

    def test_parse_garbage_is_empty(self):
        assert indexer.parse_extraction("no tuples here") == ([], [])
        assert indexer.parse_extraction("") == ([], [])

    def test_parse_report(self):
        rep = indexer._parse_report("```json\n" + CANNED_REPORT + "\n```")
        assert rep["title"] == "Knowledge tooling"
        assert "GraphRAG is core" in rep["full_content"]


class TestModelResolution:
    def test_local_only_always_pins_local(self):
        for sens in (1, 2, 3):
            model, pinned = indexer._resolve_model(sens, "local_only")
            assert pinned is True and model

    def test_gated_cloud_tier1_routes_cloud(self, monkeypatch):
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        model, pinned = indexer._resolve_model(1, "gated_cloud")
        assert model is None and pinned is False

    def test_gated_cloud_tier23_stays_local(self, monkeypatch):
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        for sens in (2, 3):
            model, pinned = indexer._resolve_model(sens, "gated_cloud")
            assert pinned is True and model

    def test_dead_gate_blocks_cloud(self, monkeypatch):
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "gate_operational", lambda: False)
        with pytest.raises(indexer.CloudIndexingDisabled):
            indexer._resolve_model(1, "gated_cloud")


class TestIndexPass:
    def test_full_index_produces_artifacts(self, wiki_home, tmp_path):
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="full", llm=llm)
        assert info["entities"] == 2
        assert info["relationships"] == 1
        ents = [e for e in store.load("entities") if e.get("tier") == "B"]
        assert {e["title"] for e in ents} == {"Graphrag", "Friday"}
        # provenance points back to the source, never a copy
        assert all(e["provenance"].get("wiki_pages") for e in ents)
        # layout covers Tier B nodes too
        assert all("x" in e for e in ents)

    def test_delta_reindex_skips_unchanged(self, wiki_home, tmp_path):
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        indexer.reindex_tier_b(store=store, mode="full", llm=llm)
        first_calls = len(llm.calls)
        assert first_calls > 0

        llm2 = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="delta", llm=llm2)
        assert info["extracted"] == 0
        extract_calls = [c for c in llm2.calls if "-Goal-" in c["text"]]
        assert extract_calls == []          # no re-extraction of unchanged

    def test_tier_a_rebuild_preserves_tier_b(self, wiki_home, tmp_path):
        from agent_friday.services.knowledge_graph import wiki_graph
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        indexer.reindex_tier_b(store=store, mode="full", llm=RecordingLLM())
        b_before = {e["id"] for e in store.load("entities")
                    if e.get("tier") == "B"}
        assert b_before
        wiki_graph.rebuild_tier_a(store=store)
        b_after = {e["id"] for e in store.load("entities")
                   if e.get("tier") == "B"}
        assert b_after == b_before

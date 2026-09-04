"""Gauntlet finding F31, live incident 2026-09-03/04: extraction had no cap
on cloud-eligible LLM calls per pass. When the corpus is large and the
routed cheap/free provider is unhealthy, model_router's own circuit breaker
(correct for chat) reroutes every remaining chunk straight to the paid
frontier default -- a single Tier B pass ran unattended for 7+ hours at
~$10/hour because nothing would ever stop asking. MAX_CLOUD_EXTRACT_CALLS
bounds cloud-eligible attempts per pass; chunks past the cap stay stale for
the next delta pass instead.

REMEDIATION NOTE (2026-09-04): this probe originally lived as
TestCloudExtractionCap inside tests/unit/test_kg_indexer.py -- an existing
test file, against this audit's own standing rule that new probes go only
in tests/gauntlet/. Moved here unchanged (content identical; the shared
RecordingLLM stub and wiki_home fixture are duplicated below rather than
imported from the unit test module, so this file is self-contained per
this codebase's established gauntlet-probe pattern) to correct that rule
violation, flagged directly by Stephen's independent cold re-verification
pass.
"""
from __future__ import annotations

import json
import shutil

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
    """Point the indexer at a tiny, exclusive wiki (local_only by default)."""
    from agent_friday.core import WIKI_DIR
    wiki = WIKI_DIR
    if wiki.exists():
        shutil.rmtree(wiki)
    (wiki / "research").mkdir(parents=True, exist_ok=True)
    (wiki / "research" / "notes.md").write_text(
        "# Notes\n\nGraphRAG and Friday work together.\n", encoding="utf-8")
    import agent_friday.services.knowledge_graph as kg
    monkeypatch.setattr(kg, "_load_settings", lambda: {
        "knowledge_graph": {"index_sources": {
            "wiki": True, "soul": False, "cognitive": False,
            "conversations": False}}})
    return wiki


class TestCloudExtractionCap:
    def test_cloud_extraction_stops_at_the_cap(self, tmp_path, monkeypatch):
        import agent_friday.services.egress_gate as eg
        import agent_friday.services.knowledge_graph as kg
        monkeypatch.setattr(kg, "_load_settings", lambda: {
            "knowledge_graph": {
                "indexing_mode": "gated_cloud",
                "index_sources": {"wiki": False, "soul": False,
                                  "cognitive": False, "conversations": False},
            }})
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        monkeypatch.setattr(indexer, "MAX_CLOUD_EXTRACT_CALLS", 2)

        fake_chunks = [{
            "id": f"chunk_{i}", "source_path": f"wiki/fake_{i}.md",
            "text": f"Fake chunk {i} about GraphRAG and Friday.",
            "sensitivity": 1, "provenance": {"sensitivity": 1},
        } for i in range(5)]
        monkeypatch.setattr(indexer, "gather_chunks", lambda: fake_chunks)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="full", llm=llm)

        extract_calls = [c for c in llm.calls if "-Goal-" in c["text"]]
        assert len(extract_calls) == 2, (
            "a 5-chunk corpus with a cap of 2 made %d cloud-eligible "
            "extraction calls instead of stopping at the cap"
            % len(extract_calls)
        )
        assert info["skipped_cloud_cap"] == 3, (
            "the 3 chunks past the cap should be reported as skipped, not "
            "silently dropped or counted as failures"
        )
        assert info["extract_failures"] == 0, (
            "chunks left for the next pass are a deferral, not a failure"
        )

    def test_local_only_mode_is_unaffected_by_the_cap(self, wiki_home, tmp_path,
                                                       monkeypatch):
        """No-op-shaped sanity check: local_only pins every chunk to the local
        model (pinned=True), so the cap -- which only counts cloud-eligible
        calls -- must never trigger for it. Reuses the wiki_home fixture
        (already indexing_mode='local_only' by default)."""
        monkeypatch.setattr(indexer, "MAX_CLOUD_EXTRACT_CALLS", 0)
        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="full", llm=llm)
        assert info["skipped_cloud_cap"] == 0
        assert info["extracted"] > 0

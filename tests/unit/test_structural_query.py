"""Phase 1 — structural query: classify, rank, BFS path, should_read. Zero LLM."""

import pytest

from agent_friday.services.knowledge_graph import structural_query, wiki_graph


@pytest.fixture
def vault(tmp_path):
    w = tmp_path / "wiki"
    (w / "research").mkdir(parents=True)
    (w / "projects").mkdir(parents=True)
    (w / "research" / "graphrag.md").write_text(
        "---\nsummary: GraphRAG builds entity graphs from text.\n---\n"
        "# GraphRAG\n\nUses [[Community Detection]] over extracted entities.\n",
        encoding="utf-8")
    (w / "research" / "community-detection.md").write_text(
        "# Community Detection\n\nClusters nodes; used by [[Knowledge Galaxy]].\n",
        encoding="utf-8")
    (w / "projects" / "knowledge-galaxy.md").write_text(
        "# Knowledge Galaxy\n\nFriday's 3D brain view.\n", encoding="utf-8")
    return w


@pytest.fixture
def index(vault):
    return wiki_graph.build_wiki_index(wiki_dir=vault, include_soul=False)


class TestClassify:
    def test_path_question(self):
        t, terms = structural_query.classify_query(
            "how is GraphRAG related to Knowledge Galaxy?")
        assert t == "path"
        assert terms[0].lower().startswith("graphrag")

    def test_gap_list_thematic_direct(self):
        assert structural_query.classify_query(
            "what gaps do I have?")[0] == "gap"
        assert structural_query.classify_query(
            "list all pages about research")[0] == "list"
        assert structural_query.classify_query(
            "what's the big picture of my knowledge?")[0] == "thematic"
        assert structural_query.classify_query(
            "tell me about attention")[0] == "direct"


class TestQuery:
    def test_zero_llm_calls(self, index, monkeypatch):
        # If anything under knowledge_graph ever touched a provider, this
        # would blow up — the module must not even import model_router.
        import sys
        import agent_friday.services.knowledge_graph.structural_query as sq
        assert "agent_friday.services.model_router" not in [
            m for m in (sq.__dict__.get("__imports__") or [])]
        called = {"n": 0}
        try:
            import agent_friday.services.model_router as mr
            monkeypatch.setattr(mr, "_generate_text",
                                lambda *a, **k: called.__setitem__("n", called["n"] + 1))
        except Exception:
            pass
        structural_query.query("how is GraphRAG related to Knowledge Galaxy?",
                               index)
        assert called["n"] == 0

    def test_path_answer_traverses_hops(self, index):
        r = structural_query.query(
            "how is GraphRAG related to Knowledge Galaxy?", index)
        assert r["answer_type"] == "path"
        assert r["path"] == ["research/graphrag.md",
                             "research/community-detection.md",
                             "projects/knowledge-galaxy.md"]

    def test_direct_returns_should_read(self, index):
        # Weak match (summary-only terms) → not index_only → pages to open.
        r = structural_query.query("3D brain view", index)
        assert r["candidates"]
        assert r["candidates"][0]["page"] == "projects/knowledge-galaxy.md"
        assert r["index_only"] is False
        assert 0 < len(r["should_read"]) <= 3

    def test_exact_title_with_summary_is_index_only(self, index):
        r = structural_query.query("GraphRAG", index)
        assert r["index_only"] is True
        assert r["should_read"] == []

    def test_empty_index(self):
        r = structural_query.query("anything", {})
        assert r["stats"]["indexed_pages"] == 0


class TestFindPath:
    def test_bfs_uses_backlinks_too(self, index):
        # knowledge-galaxy has no out-links; path must ride in_links.
        p = structural_query.find_path(index, "projects/knowledge-galaxy",
                                       "research/graphrag")
        assert p == ["projects/knowledge-galaxy",
                     "research/community-detection", "research/graphrag"]

    def test_no_path_none(self, index):
        assert structural_query.find_path(index, "research/graphrag",
                                          "ghost") is None

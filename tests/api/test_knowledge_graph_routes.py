"""Phase 1 — /api/knowledge-graph/* routes over a real (temp-home) wiki."""

from pathlib import Path

import pytest


@pytest.fixture
def seeded_wiki():
    """Seed a small wiki at the import-time WIKI_DIR and mark the graph dirty.

    WIKI_DIR (not Path.home()): tests/test_egress_adversarial.py leaks a
    USERPROFILE redirect session-wide, but the graph reads the constant
    frozen at first import.
    """
    from agent_friday.core import WIKI_DIR
    wiki = WIKI_DIR
    (wiki / "research").mkdir(parents=True, exist_ok=True)
    (wiki / "projects").mkdir(parents=True, exist_ok=True)
    (wiki / "research" / "graphrag.md").write_text(
        "# GraphRAG\n\nEntity graphs from text. See [[Galaxy View]].\n",
        encoding="utf-8")
    (wiki / "projects" / "galaxy-view.md").write_text(
        "# Galaxy View\n\nFriday's 3D knowledge explorer.\n", encoding="utf-8")
    from agent_friday.services.knowledge_graph import mark_wiki_dirty
    mark_wiki_dirty("test-seed")
    return wiki


def test_summary(client, seeded_wiki):
    r = client.get("/api/knowledge-graph/summary")
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok"
    assert d["counts"]["entities"] >= 2
    assert isinstance(d["communities"], list)
    assert d["settings"]["indexing_mode"] == "local_only"


def test_graph_returns_layout_positions(client, seeded_wiki):
    r = client.get("/api/knowledge-graph/graph")
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok"
    ids = {e["id"] for e in d["entities"]}
    assert {"page:research/graphrag", "page:projects/galaxy-view"} <= ids
    # Contract: /graph MUST return precomputed x,y,z (client never simulates).
    assert all("x" in e and "y" in e and "z" in e for e in d["entities"])
    pairs = {(r_["source"], r_["target"]) for r_ in d["relationships"]}
    assert ("page:research/graphrag", "page:projects/galaxy-view") in pairs
    assert d["layout"].get("algorithm")


def test_node_and_neighbors(client, seeded_wiki):
    r = client.get("/api/knowledge-graph/node/page:research/graphrag")
    assert r.status_code == 200
    d = r.get_json()
    assert d["node"]["title"] == "GraphRAG"
    assert any(n["id"] == "page:projects/galaxy-view" for n in d["neighbors"])

    r = client.get("/api/knowledge-graph/neighbors/page:research/graphrag?depth=1")
    assert r.status_code == 200
    ids = {e["id"] for e in r.get_json()["entities"]}
    assert "page:projects/galaxy-view" in ids

    assert client.get("/api/knowledge-graph/node/page:ghost").status_code == 404


def test_structural_query_route(client, seeded_wiki):
    r = client.post("/api/knowledge-graph/query",
                    json={"question": "how is GraphRAG related to Galaxy View?"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["mode"] == "structural"
    assert d["answer_type"] == "path"
    assert d["path"] == ["research/graphrag.md", "projects/galaxy-view.md"]

    assert client.post("/api/knowledge-graph/query",
                       json={}).status_code == 400


def test_search(client, seeded_wiki):
    r = client.get("/api/knowledge-graph/search?q=galaxy")
    assert r.status_code == 200
    hits = r.get_json()["results"]
    assert hits and hits[0]["id"] == "page:projects/galaxy-view"
    assert hits[0]["path"] == "projects/galaxy-view.md"

    assert client.get("/api/knowledge-graph/search").get_json()["results"] == []


def test_reindex_tier_a_and_wiki_edit_marks_dirty(client, seeded_wiki):
    r = client.post("/api/knowledge-graph/reindex", json={"tier": "A"})
    assert r.status_code == 200
    assert r.get_json()["entities"] >= 2

    # Editing a page through the wiki API must dirty the graph, and the next
    # graph read must pick up the new page.
    r = client.put("/api/wiki/edit", json={
        "file": "research/new-idea.md",
        "content": "# New Idea\n\nLinks to [[GraphRAG]].\n"})
    assert r.status_code == 200
    from agent_friday.services.knowledge_graph import peek_wiki_dirty
    assert peek_wiki_dirty() is True

    r = client.get("/api/knowledge-graph/graph")
    ids = {e["id"] for e in r.get_json()["entities"]}
    assert "page:research/new-idea" in ids

    # Tier B kicks off in the background (Phase 2); sync mode is covered by
    # tests/api/test_kg_reindex_route.py.
    r = client.post("/api/knowledge-graph/reindex", json={"tier": "B"})
    assert r.status_code in (200, 409)
    if r.status_code == 200:
        assert r.get_json()["status"] == "started"

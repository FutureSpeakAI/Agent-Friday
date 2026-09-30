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
    from agent_friday.services.knowledge_graph.wiki_graph import rebuild_tier_a
    from agent_friday.services.knowledge_graph import consume_wiki_dirty
    rebuild_tier_a()
    consume_wiki_dirty()
    return wiki


def test_summary(client, seeded_wiki):
    r = client.get("/api/knowledge-graph/summary")
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok"
    assert d["counts"]["entities"] >= 2
    assert isinstance(d["communities"], list)
    assert d["settings"]["indexing_mode"] == "local"


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
    # graph read schedules a refresh without blocking on the new page.
    r = client.put("/api/wiki/edit", json={
        "file": "research/new-idea.md",
        "content": "# New Idea\n\nLinks to [[GraphRAG]].\n"})
    assert r.status_code == 200
    from agent_friday.services.knowledge_graph import peek_wiki_dirty
    assert peek_wiki_dirty() is True

    client.get("/api/knowledge-graph/graph")
    from agent_friday.routes.knowledge_graph import _rebuild_lock
    with _rebuild_lock:
        pass
    r = client.get("/api/knowledge-graph/graph")
    ids = {e["id"] for e in r.get_json()["entities"]}
    assert "page:research/new-idea" in ids

    # Tier B kicks off in the background (Phase 2); sync mode is covered by
    # tests/api/test_kg_reindex_route.py.
    r = client.post("/api/knowledge-graph/reindex", json={"tier": "B"})
    assert r.status_code in (200, 409)
    if r.status_code == 200:
        assert r.get_json()["status"] == "started"


def test_duplicate_tier_a_reindex_returns_busy(client):
    from agent_friday.routes.knowledge_graph import _rebuild_lock
    with _rebuild_lock:
        response = client.post("/api/knowledge-graph/reindex", json={"tier": "A"})
    assert response.status_code == 409
    assert response.get_json()["status"] == "busy"


def test_wiki_structure_avoids_per_page_path_stats(client, tmp_path, monkeypatch):
    from pathlib import Path
    from agent_friday.routes import wiki
    folder = tmp_path / "wiki" / "reference"
    folder.mkdir(parents=True)
    for number in range(200):
        (folder / f"page-{number}.md").write_text("# Page", encoding="utf-8")
    monkeypatch.setattr(wiki, "WIKI_DIR", folder.parent)
    monkeypatch.setattr(wiki, "_load_pending_wiki", lambda: [])
    original = Path.stat
    calls = []

    def counted(path, *args, **kwargs):
        if path.suffix == ".md" and path.parent == folder:
            calls.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counted)
    data = client.get("/api/wiki/structure").get_json()
    assert len(data["structure"]["reference"]) == 200
    assert len(data["recent"]) == 5
    assert not calls, "Use directory-entry metadata rather than stat every page"


def test_graph_preview_keeps_detail_out_of_initial_payload(client, monkeypatch, tmp_path):
    from agent_friday.routes import knowledge_graph as route
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore(base_dir=tmp_path / "graph")
    node = {"id": "demo", "title": "Demo", "description": "a" * 2000,
            "provenance": {"sensitivity": 1, "wiki_pages": ["research/demo.md"],
                           "source_refs": ["long evidence record" * 1000]}}
    store.save("entities", [node])
    store.save("relationships", [])
    store.save("communities", [{"id": "c", "community": "c", "size": 1,
                                "entity_ids": ["demo"], "relationship_ids": []}])
    monkeypatch.setattr(route, "_ensure_fresh", lambda: store)
    preview = client.get("/api/knowledge-graph/graph?view=compact").get_json()
    shown = preview["entities"][0]
    assert "source_refs" not in shown["provenance"]
    assert shown["provenance"]["wiki_pages"] == ["research/demo.md"]
    assert len(shown["description"]) <= 480
    assert "entity_ids" not in preview["communities"][0]
    full = client.get("/api/knowledge-graph/node/demo").get_json()["node"]
    assert full == node
    assert client.get("/api/knowledge-graph/graph").get_json()["entities"][0] == node

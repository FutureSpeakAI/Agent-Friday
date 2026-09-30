"""Archive-scale scans must not monopolize knowledge workspace requests."""
import threading
import time

from agent_friday.services.knowledge_graph import wiki_graph


def test_archive_mention_scan_budget():
    pages = {
        str(i): dict(title=f"Archive article {i:05d}", stem=f"archive-{i:05d}",
                     out_links=[], in_links=[], mention_count=0)
        for i in range(6000)
    }
    bodies = {"0": ("ordinary prose with no matching title. " * 5000)
                     + " Archive article 05999!"}
    started = time.perf_counter()
    wiki_graph._extract_mentions(pages, bodies)
    elapsed = time.perf_counter() - started
    assert ("5999", "mention") in pages["0"]["out_links"]
    assert elapsed < 2.0, f"Archive mention scan took {elapsed:.2f}s"


def test_mentions_boundaries_longest_case_and_explicit():
    names = {"source": "Source", "a": "Alpha", "ab": "Alpha Beta", "b": "Beta"}
    pages = {k: dict(title=v, stem=v.lower().replace(" ", "-"),
                    out_links=[], in_links=[], mention_count=0)
             for k, v in names.items()}
    bodies = {"source": "ALPHA BETA; Alpha. xAlpha Alpha-x _Alpha Beta!"}
    wiki_graph._extract_mentions(pages, bodies)
    assert pages["ab"]["mention_count"] == 1
    assert pages["a"]["mention_count"] == 1
    assert pages["b"]["mention_count"] == 1
    pages["source"]["out_links"] = [("ab", "wikilink")]
    wiki_graph._extract_mentions(pages, {"source": "Alpha Beta"})
    assert pages["source"]["out_links"] == [("ab", "wikilink")]


def test_saved_graph_read_does_not_wait_for_rebuild(tmp_path, monkeypatch):
    from agent_friday.routes import knowledge_graph as route
    from agent_friday.services.knowledge_graph import mark_wiki_dirty, consume_wiki_dirty
    from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore(base_dir=tmp_path / "graph")
    store.save("entities", [{"id": "saved", "provenance": {"sensitivity": 1}}])
    monkeypatch.setattr(route, "KnowledgeGraphStore", lambda: store)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def rebuild(**kwargs):
        calls.append(1)
        entered.set()
        release.wait(0.8)
        finished.set()
        return {"tier": "A"}

    monkeypatch.setattr(route.wiki_graph, "rebuild_tier_a", rebuild)
    monkeypatch.setattr(route, "_rebuild_lock", threading.Lock())
    mark_wiki_dirty("test")
    try:
        started = time.perf_counter()
        assert route._ensure_fresh().load("entities")[0]["id"] == "saved"
        assert time.perf_counter() - started < 0.3
        assert entered.wait(1)
        route._ensure_fresh()
        assert len(calls) == 1
        mark_wiki_dirty("edit-during-rebuild")
    finally:
        release.set()
        finished.wait(2)
        # Join via the same lock so no worker leaks into another test.
        with route._rebuild_lock:
            pass
    assert consume_wiki_dirty(), "An edit made during indexing must remain dirty"

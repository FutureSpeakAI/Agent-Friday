"""Archive-scale scans must not monopolize knowledge workspace requests."""
import threading
import time

from agent_friday.services.knowledge_graph import wiki_graph


def _body_and_pages(titles):
    pages = {
        str(i): dict(title=f"Archive article {i:05d}", stem=f"archive-{i:05d}",
                     out_links=[], in_links=[], mention_count=0)
        for i in range(titles)
    }
    last = titles - 1
    body = ("ordinary prose with no matching title. " * 5000) + f" Archive article {last:05d}!"
    return pages, body, last


def _best_of_three(run):
    best = None
    for _ in range(3):
        started = time.perf_counter()
        run()
        spent = time.perf_counter() - started
        best = spent if best is None else min(best, spent)
    return best


# The scan may cost this many times what one pass over the same body costs on this machine. The real scan is
# about 8 times that unit; trying every title against the whole body (6000 regex searches) is thousands of times.
SCAN_LIMIT_IN_BODY_PASSES = 60


def test_archive_mention_scan_budget():
    """Judged against one pass over the same body on the same machine, so a busy shared runner (everything slower,
    the pass too) cannot fail it, while a scan that tries every title against the whole body still does. The old
    check was an absolute 2.0 s (F17)."""
    import re

    def scan_once():
        pages, body, last = _body_and_pages(6000)
        started = time.perf_counter()
        wiki_graph._extract_mentions(pages, {"0": body})
        elapsed = time.perf_counter() - started
        assert (str(last), "mention") in pages["0"]["out_links"]
        return elapsed

    pages, body, _ = _body_and_pages(6000)
    one_pass = max(_best_of_three(lambda: len(re.findall(r"\w+", body))), 0.002)
    scan = min(scan_once() for _ in range(3))
    assert scan <= SCAN_LIMIT_IN_BODY_PASSES * one_pass, (
        f"the mention scan cost {scan / one_pass:.0f} passes over the body ({scan:.2f}s against {one_pass:.3f}s)")


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

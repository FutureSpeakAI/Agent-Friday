"""Archive-scale scans must not monopolize knowledge workspace requests."""
import threading
import time

from agent_friday.services.knowledge_graph import wiki_graph


def test_the_mention_scan_check_does_not_depend_on_how_fast_the_machine_is(monkeypatch):
    """F17: the check below used a 2.0 s stopwatch and failed a busy ubuntu runner (2.85 s). A clock that jumps ten
    seconds on every read is a very slow machine, not a slow scan; the scan must still be judged fine."""
    ticks = iter(range(0, 10_000_000, 10))
    monkeypatch.setattr(time, "perf_counter", lambda: float(next(ticks)))
    test_archive_mention_scan_budget()


def _scan_seconds(titles):
    pages = {
        str(i): dict(title=f"Archive article {i:05d}", stem=f"archive-{i:05d}",
                     out_links=[], in_links=[], mention_count=0)
        for i in range(titles)
    }
    last = titles - 1
    bodies = {"0": ("ordinary prose with no matching title. " * 5000)
                     + f" Archive article {last:05d}!"}
    started = time.perf_counter()
    wiki_graph._extract_mentions(pages, bodies)
    elapsed = time.perf_counter() - started
    assert (str(last), "mention") in pages["0"]["out_links"]
    return elapsed


def test_archive_mention_scan_budget():
    """Four times the titles must cost well under sixteen times the time (a scan that tries every title against
    the whole body would be quadratic in work and show it). Compares the machine with itself, so a busy shared
    runner cannot fail it; the old check was an absolute 2.0 s (F17)."""
    small = max(min(_scan_seconds(1500) for _ in range(3)), 0.02)
    big = min(_scan_seconds(6000) for _ in range(3))
    assert big <= 8.0 * small, f"4x the titles cost {big / small:.1f}x the time ({small:.2f}s -> {big:.2f}s)"


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

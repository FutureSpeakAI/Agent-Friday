"""One archiver cycle reads and writes source_trust.json a bounded number of
times, however many articles the pool holds.

The file runs to megabytes. Counting each article with its own full load,
re-serialise and write made the archiver the largest CPU consumer in the
server. Counting is now one load, every counter bumped, one save.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.source_trust_graph import SourceTrustGraph

DOMAINS = ["outlet%d.test" % (i % 7) for i in range(100)]


@pytest.fixture
def graph(tmp_path):
    g = SourceTrustGraph(friday_dir=tmp_path)
    g.observe("outlet0.test", "claim_verified", "factual_accuracy", 0.9)
    return g


@pytest.fixture
def io_count(monkeypatch, graph):
    counts = {"read": 0, "write": 0}
    real_read, real_write = Path.read_text, Path.write_text

    def _read(self, *a, **k):
        if self == graph.path:
            counts["read"] += 1
        return real_read(self, *a, **k)

    def _write(self, *a, **k):
        if self == graph.path:
            counts["write"] += 1
        return real_write(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _read)
    monkeypatch.setattr(Path, "write_text", _write)
    return counts


def test_a_batch_of_100_articles_is_one_read_and_one_write(graph, io_count):
    graph.record_articles_seen(DOMAINS)
    assert io_count == {"read": 1, "write": 1}
    data = json.loads(graph.path.read_text(encoding="utf-8"))
    for i in range(7):
        want = DOMAINS.count("outlet%d.test" % i)
        assert data["sources"]["outlet%d.test" % i]["article_count"] == want


def test_one_article_still_counts(graph):
    graph.record_article_seen("https://www.outlet9.test/a/story")
    graph.record_article_seen("outlet9.test")
    assert graph.get("outlet9.test")["article_count"] == 2


def test_blank_domains_are_skipped(graph):
    graph.record_articles_seen(["", None, "outlet3.test"])
    assert graph.get("outlet3.test")["article_count"] == 1


def test_the_archiver_cycle_touches_the_file_once_per_step(monkeypatch, graph, io_count):
    """100 pool items: one read and one write to count them (the cross-source
    analysis keeps its own single read and write), never one per article."""
    from agent_friday.services import news_engine as ne

    pool = [{"source": d, "title": "Story %d" % i, "url": "https://%s/%d" % (d, i)}
            for i, d in enumerate(DOMAINS)]
    monkeypatch.setattr(ne, "_gather_front_page_pool", lambda per_cat=14: (pool, None))
    monkeypatch.setattr(ne, "_archive_articles", lambda items: 0)
    monkeypatch.setattr(ne, "_cluster_articles", lambda items, min_sources=2: [])
    monkeypatch.setattr(ne, "_HAS_TRUST_GRAPHS", True)
    monkeypatch.setattr(ne, "get_source_trust_graph", lambda friday_dir=None: graph)

    ne._news_archiver_tick()

    assert io_count == {"read": 2, "write": 2}, io_count
    data = json.loads(graph.path.read_text(encoding="utf-8"))
    assert sum(r["article_count"] for r in data["sources"].values()) == 100


def test_the_file_is_saved_compact(graph):
    graph.record_articles_seen(["outlet1.test"])
    text = graph.path.read_text(encoding="utf-8")
    assert "\n" not in text and ": " not in text

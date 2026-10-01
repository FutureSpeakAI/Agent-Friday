"""The structural query reuses the parsed wiki until the wiki changes.

The ambient knowledge block runs a structural query for every system prompt.
A parse reads and decrypts every page and builds a mention trie, so repeating
it per prompt churned hundreds of megabytes per turn on a large wiki.
"""
from __future__ import annotations

import os
import tracemalloc

import pytest

from agent_friday.services.knowledge_graph import structural_query, wiki_graph


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    w = tmp_path / "wiki"
    (w / "research").mkdir(parents=True)
    (w / "research" / "graphrag.md").write_text(
        "# GraphRAG\n\nUses [[Community Detection]] over extracted entities.\n", encoding="utf-8")
    (w / "research" / "community-detection.md").write_text(
        "# Community Detection\n\nClusters nodes for the galaxy view.\n", encoding="utf-8")
    monkeypatch.setattr(wiki_graph, "WIKI_DIR", w)
    monkeypatch.setattr(wiki_graph, "SOUL_FILE", tmp_path / "SOUL.md")
    wiki_graph.clear_wiki_index_cache()
    yield w
    wiki_graph.clear_wiki_index_cache()


@pytest.fixture
def reads(monkeypatch):
    seen: list[str] = []
    real = wiki_graph._read

    def counting(path):
        seen.append(path.name)
        return real(path)
    monkeypatch.setattr(wiki_graph, "_read", counting)
    return seen


def _bump(path, text):
    path.write_text(text, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_repeated_queries_read_each_page_once(wiki, reads):
    for _ in range(5):
        r = structural_query.query("tell me about graphrag")
        assert r["candidates"], r
    assert sorted(reads) == ["community-detection.md", "graphrag.md"]


def test_an_edited_page_is_seen_on_the_next_query(wiki, reads):
    structural_query.query("tell me about graphrag")
    _bump(wiki / "research" / "graphrag.md",
          "---\nsummary: Rewritten summary about zeppelins.\n---\n# GraphRAG\n")
    r = structural_query.query("tell me about graphrag")
    assert any("zeppelins" in c["summary"] for c in r["candidates"]), r


def test_an_added_or_removed_page_is_seen_on_the_next_query(wiki):
    structural_query.query("tell me about graphrag")
    (wiki / "research" / "zeppelin-history.md").write_text(
        "# Zeppelin History\n\nAirships.\n", encoding="utf-8")
    r = structural_query.query("tell me about zeppelin history")
    assert any(c["title"] == "Zeppelin History" for c in r["candidates"]), r
    (wiki / "research" / "zeppelin-history.md").unlink()
    r = structural_query.query("tell me about zeppelin history")
    assert not any(c["title"] == "Zeppelin History" for c in r["candidates"]), r


def test_unlocking_the_vault_reparses(wiki, reads, monkeypatch):
    # A page read while locked is a placeholder; once the key is available the
    # same unchanged file decrypts to real text, so the index must be rebuilt.
    state = {"unlocked": False}
    monkeypatch.setattr(wiki_graph, "_vault_unlocked", lambda: state["unlocked"])
    structural_query.query("tell me about graphrag")
    n = len(reads)
    state["unlocked"] = True
    structural_query.query("tell me about graphrag")
    assert len(reads) == 2 * n


def test_repeated_queries_allocate_almost_nothing(tmp_path, monkeypatch):
    # Flat memory: after the first parse, ten more queries over an unchanged
    # wiki must not allocate anywhere near a parse's worth of memory.
    w = tmp_path / "wiki"
    (w / "notes").mkdir(parents=True)
    for i in range(150):
        links = " ".join(f"[[Topic {j:03d}]]" for j in range(i % 7, 150, 37))
        (w / "notes" / f"topic-{i:03d}.md").write_text(
            f"# Topic {i:03d}\n\nAbout topic {i:03d} and its neighbours. {links}\n" + "filler text " * 400,
            encoding="utf-8")
    monkeypatch.setattr(wiki_graph, "WIKI_DIR", w)
    monkeypatch.setattr(wiki_graph, "SOUL_FILE", tmp_path / "SOUL.md")
    wiki_graph.clear_wiki_index_cache()
    tracemalloc.start()
    try:
        structural_query.query("tell me about topic 042")
        _, first_peak = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        base, _ = tracemalloc.get_traced_memory()
        for _ in range(10):
            structural_query.query("tell me about topic 042")
        _, later_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        wiki_graph.clear_wiki_index_cache()
    assert later_peak - base < first_peak / 4, (first_peak, later_peak - base)

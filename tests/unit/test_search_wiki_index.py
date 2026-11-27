"""search_wiki answers from the knowledge graph's index and stops on time (KG latency fix 1).

The failure, measured on 2026-11-26: a phone question, "what is the core philosophy of the
FutureSpeak aesthetic?", spent about 41 s of a 54 s answer inside two wiki tools. search_wiki
matched the query as one exact phrase, so a question worded as people speak matched no page and the
tool read every wiki file, with no time limit on a cloud turn. Synthetic fixtures only.
"""
import time

import pytest

from agent_friday.services import agent as A
from agent_friday.services import wiki_index_search as wis


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    from agent_friday.services.knowledge_graph import wiki_graph as wg
    root = tmp_path / "wiki"
    (root / "brand").mkdir(parents=True)
    (root / "brand" / "studio-look.md").write_text(
        "# Studio look\n\nThe philosophy of the Northwind aesthetic is signal over noise.\n", encoding="utf-8")
    (root / "notes").mkdir()
    for i in range(40):
        (root / "notes" / f"n{i:02d}.md").write_text(f"# Note {i}\n\nnothing relevant here {i}\n", encoding="utf-8")
    monkeypatch.setattr(wg, "WIKI_DIR", root)
    # the core-memory card is not part of this test
    from agent_friday.services import core_memory_tools as cmt
    monkeypatch.setattr(cmt, "search_card", lambda q, limit=3: [])
    wis.reset_cache()
    yield root
    wis.reset_cache()


def _entities(monkeypatch, rows):
    monkeypatch.setattr(wis, "_entity_files", lambda: [])
    monkeypatch.setattr(wis, "_load_entities", lambda: rows)


def test_a_spoken_question_finds_the_page_that_answers_it(wiki, monkeypatch):
    """Before: 'No wiki files matched' (no file holds the question word for word)."""
    _entities(monkeypatch, [])
    out = A._tool_search_wiki({"query": "the Northwind aesthetic and its philosophy"})
    assert "brand/studio-look.md" in out, out


def test_the_index_names_the_page_without_walking_the_wiki(wiki, monkeypatch):
    """The index's page is read first, and no other page is opened to find it."""
    _entities(monkeypatch, [{"title": "Studio look", "description": "the Northwind aesthetic: core philosophy",
                             "provenance": {"wiki_pages": ["brand/studio-look.md"]}}])
    opened = []
    real = A.wiki_read_text

    def counting(p):
        opened.append(str(p))
        return real(p)

    monkeypatch.setattr(A, "wiki_read_text", counting)
    out = A._tool_search_wiki({"query": "core philosophy of the Northwind aesthetic", "limit": 1})
    assert "brand/studio-look.md" in out, out
    assert len(opened) == 1, "the index named the page; the walk should not have run (%d reads)" % len(opened)


def test_the_walk_stops_at_its_budget_on_any_seat(wiki, monkeypatch):
    """No local-seat budget is set (a cloud turn): the walk still stops and says it is partial."""
    _entities(monkeypatch, [])
    monkeypatch.setattr(wis, "SCAN_BUDGET_S", 0.05)
    real = A.wiki_read_text

    def slow(p):
        time.sleep(0.02)
        return real(p)

    monkeypatch.setattr(A, "wiki_read_text", slow)
    t0 = time.monotonic()
    out = A._tool_search_wiki({"query": "zebra quantum"})
    assert time.monotonic() - t0 < 0.6, "the walk ran past its budget"
    assert "time budget ran out" in out, out


def test_words_drop_stop_words_and_repeats():
    assert wis.words("What is the core philosophy of the FutureSpeak aesthetic?") == [
        "core", "philosophy", "futurespeak", "aesthetic"]

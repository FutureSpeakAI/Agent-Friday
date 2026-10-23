"""The knowledge graph tells the current truth after an edit (north star
§34.9-3 the current state wins, §34.9-4 a correction reaches the graph,
§34.9-9 a rebuild keeps what it must and brings back nothing removed).

The extraction model is a stub that reads the chunk it is given; the wiki
is a tiny temporary one; nothing leaves the process."""
from __future__ import annotations

import shutil

import pytest

from agent_friday.services.knowledge_graph import indexer
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore


def _tuple(title, kind, desc):
    return f'("entity"<|>{title}<|>{kind}<|>{desc})'


class ReadingLLM:
    """Extracts what the chunk actually says, so an edit changes the answer."""

    def __call__(self, messages, system, sensitivity, mode, orb_label=None):
        text = messages[0]["content"]
        if "-Goal-" not in text:
            return "merged description"
        body = text.split("Text:", 1)[-1] if "Text:" in text else text
        out = []
        if "Peter Smallwood" in body:
            out.append(_tuple("HARWICH HARBOUR", "place", "The harbour master is Peter Smallwood"))
        if "Ana Ferreira" in body:
            out.append(_tuple("HARWICH HARBOUR", "place", "The harbour master is Ana Ferreira"))
        if "Wren" in body:
            out.append(_tuple("WREN", "vessel", "Wren is a sailing boat"))
        return "\n##\n".join(out) + "\n<|COMPLETE|>"


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    from agent_friday.core import WIKI_DIR
    if WIKI_DIR.exists():
        shutil.rmtree(WIKI_DIR)
    (WIKI_DIR / "notes").mkdir(parents=True, exist_ok=True)
    import agent_friday.services.knowledge_graph as kg
    monkeypatch.setattr(kg, "_load_settings", lambda: {
        "knowledge_graph": {"index_sources": {"wiki": True, "soul": False, "cognitive": False,
                                              "conversations": False}}})
    return WIKI_DIR / "notes"


def _entities(store):
    return {e["id"]: e for e in store.load("entities") if e.get("tier") == "B"}


def _by_title(store, title):
    return [e for e in _entities(store).values() if e.get("title", "").lower() == title.lower()]


def test_an_edited_page_replaces_what_the_graph_said(wiki, tmp_path):
    store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
    page = wiki / "harbour.md"
    page.write_text("# Harbour\n\nThe Harwich harbour master is Peter Smallwood.\n", encoding="utf-8")
    indexer.reindex_tier_b(store=store, mode="full", llm=ReadingLLM())
    assert "Peter" in _by_title(store, "Harwich Harbour")[0]["description"]

    page.write_text("# Harbour\n\nSince September the Harwich harbour master is Ana Ferreira.\n",
                    encoding="utf-8")
    indexer.reindex_tier_b(store=store, mode="delta", llm=ReadingLLM())
    desc = _by_title(store, "Harwich Harbour")[0]["description"]
    assert "Ana Ferreira" in desc and "Peter" not in desc, desc


def test_a_removed_page_takes_its_nodes_with_it(wiki, tmp_path):
    store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
    (wiki / "harbour.md").write_text("The Harwich harbour master is Peter Smallwood.\n", encoding="utf-8")
    boat = wiki / "boat.md"
    boat.write_text("My boat is called Wren.\n", encoding="utf-8")
    indexer.reindex_tier_b(store=store, mode="full", llm=ReadingLLM())
    assert _by_title(store, "Wren")

    boat.unlink()
    indexer.reindex_tier_b(store=store, mode="delta", llm=ReadingLLM())
    assert _by_title(store, "Wren") == [], "a node from a deleted page is still in the graph"
    assert _by_title(store, "Harwich Harbour"), "a node from an untouched page was dropped"
    # And it stays gone: the next pass does not see the page as "removed" for ever.
    info = indexer.reindex_tier_b(store=store, mode="delta", llm=ReadingLLM())
    assert info["extracted"] == 0 and _by_title(store, "Wren") == []


def test_a_node_another_page_still_supports_survives_an_edit(wiki, tmp_path):
    store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
    a, b = wiki / "a.md", wiki / "b.md"
    a.write_text("We sail Wren on Sundays.\n", encoding="utf-8")
    b.write_text("Wren needs new sails.\n", encoding="utf-8")
    indexer.reindex_tier_b(store=store, mode="full", llm=ReadingLLM())
    a.write_text("We walk on Sundays now.\n", encoding="utf-8")
    indexer.reindex_tier_b(store=store, mode="delta", llm=ReadingLLM())
    wren = _by_title(store, "Wren")
    assert wren, "a node still supported by another page was dropped"
    pages = wren[0]["provenance"]["wiki_pages"]
    assert pages and all(not p.replace("\\", "/").endswith("/a.md") and p != "a.md" for p in pages), pages


def test_a_full_rebuild_keeps_the_facts_the_owner_approved(wiki, tmp_path):
    store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
    (wiki / "harbour.md").write_text("The Harwich harbour master is Peter Smallwood.\n", encoding="utf-8")
    indexer.reindex_tier_b(store=store, mode="full", llm=ReadingLLM())
    ents = store.load("entities")
    ents.append({"id": "fact_0123456789ab", "title": "Owner prefers morning sails", "type": "fact",
                 "description": "The owner prefers to sail in the morning.", "degree": 0, "frequency": 1,
                 "level": 0, "tier": "B", "provenance": {"conversations": ["c1"], "sensitivity": 2,
                                                          "learned": "conversation"}})
    store.save("entities", ents)
    indexer.reindex_tier_b(store=store, mode="full", llm=ReadingLLM())
    assert "fact_0123456789ab" in _entities(store), "a full rebuild dropped an approved fact"

"""knowledge_query and read_wiki agree about where a page is.

Every path the knowledge graph hands out (candidates, should_read, the SOUL.md
entry that lives beside the wiki rather than in it) opens with read_wiki
exactly as named, and the ways a model writes a path to the serving wiki
(~/.friday/wiki/..., the legacy ~/wiki/..., no .md) all reach the same file.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    from agent_friday.services.knowledge_graph import wiki_graph as wg
    root = tmp_path / ".friday" / "wiki"
    (root / "projects").mkdir(parents=True)
    (root / "projects" / "atlas.md").write_text(
        "---\ntitle: Atlas\ntags: [project]\n---\n# Atlas\n\nThe Atlas launch plan.\n",
        encoding="utf-8")
    (root / "people.md").write_text("# People\n\nSee [[projects/atlas]].\n", encoding="utf-8")
    soul = tmp_path / ".friday" / "SOUL.md"
    soul.write_text("# Soul\n\nFriday's identity and voice.\n", encoding="utf-8")
    monkeypatch.setattr(wg, "WIKI_DIR", root)
    monkeypatch.setattr(wg, "SOUL_FILE", soul)
    monkeypatch.setattr(wg, "cached_wiki_index",
                        lambda: wg.build_wiki_index(wiki_dir=root, include_soul=True,
                                                    mention_edges=False))
    from agent_friday.services.knowledge_graph import structural_query as sq
    monkeypatch.setattr(sq, "cached_wiki_index", wg.cached_wiki_index)
    return root


def _read(path):
    from agent_friday.services import agent
    return agent.CLAUDE_TOOL_HANDLERS["read_wiki"]({"path": path})


def _query(question):
    from agent_friday.services import agent
    return json.loads(agent.CLAUDE_TOOL_HANDLERS["knowledge_query"]({"question": question}))


def test_the_page_knowledge_query_names_is_the_page_read_wiki_opens(wiki):
    out = _query("atlas launch")
    pages = [c["page"] for c in out["candidates"]]
    assert "projects/atlas.md" in pages
    assert "The Atlas launch plan." in _read("projects/atlas.md")


def test_every_path_the_index_hands_out_opens(wiki):
    from agent_friday.services.knowledge_graph import wiki_graph as wg
    index = wg.build_wiki_index(wiki_dir=wiki, include_soul=True, mention_edges=False)
    assert {e["path"] for e in index.values()} >= {"projects/atlas.md", "people.md", "SOUL.md"}
    for entry in index.values():
        text = _read(entry["path"])
        assert not text.startswith(("Wiki file not found", "Path escapes")), (entry["path"], text)


def test_soul_md_from_the_index_opens_with_read_wiki(wiki):
    assert "Friday's identity and voice." in _read("SOUL.md")


@pytest.mark.parametrize("spelling", [
    "~/.friday/wiki/projects/atlas.md",
    "~/wiki/projects/atlas.md",
    "wiki/projects/atlas.md",
    "projects/atlas",
    "projects\\atlas.md",
])
def test_the_ways_a_model_spells_a_wiki_path_reach_the_same_file(wiki, spelling):
    from agent_friday.services.knowledge_graph import wiki_graph as wg
    assert wg.resolve_page(spelling) == (wiki / "projects" / "atlas.md").resolve()
    assert "The Atlas launch plan." in _read(spelling)


def test_a_path_outside_the_wiki_is_still_refused(wiki, tmp_path):
    (tmp_path / "secret.md").write_text("nope", encoding="utf-8")
    from agent_friday.services.knowledge_graph import wiki_graph as wg
    assert wg.resolve_page("../../secret.md") is None
    assert _read("../../secret.md").startswith("Path escapes the wiki root")


def test_search_wiki_hits_open_with_read_wiki(wiki):
    from agent_friday.services import agent
    out = json.loads(agent.CLAUDE_TOOL_HANDLERS["search_wiki"]({"query": "Atlas"}))
    assert out["hits"], out
    for hit in out["hits"]:
        assert not _read(hit["path"]).startswith("Wiki file not found"), hit

"""Removing what Friday holds about a person who never agreed to be held.

THE GAP THIS CLOSES
-------------------
Friday accumulates records about people who are not her user. The knowledge
graph's entity types include ``person``; its sources include the wiki and every
conversation turn; it re-runs nightly at 03:30. Over a year that is a linked
picture of the user's colleagues, family, and anyone they deal with often. None
of those people were asked.

Until now there was no way to remove one. ``people_graph.py`` exposed
``add_person`` and ``edit`` and nothing else -- no delete, no forget, no
retention window, no expiry. There was no ``/api/trust/delete-person``. The
honest answer to "a colleague asked me to delete what you hold about them" was:
hand-edit ``~/.friday/people_graph.json``, remember its legacy mirror
``trust_graph.json``, then find and remove the derived nodes in
``~/.friday/knowledge-graph``, then rebuild the index.

THE PART THAT IS EASY TO GET WRONG
----------------------------------
The knowledge graph is DERIVED. Purging the entity is not enough on its own:
the nightly reindex reads the same wiki pages and conversation turns and builds
the person straight back. A delete that is undone at 03:30 is not a delete, so
``test_a_forgotten_person_is_not_resurrected_by_the_next_reindex`` is the test
that actually matters here.

WHAT IS DELIBERATELY *NOT* CLAIMED
----------------------------------
Forgetting does not rewrite the user's own wiki pages or conversation history.
Those are things the user wrote, in their own words, in files they can open --
silently editing them would be a different and worse product. So ``find()``
reports which source documents still mention the person, and the UI shows that
list. ``test_the_receipt_names_sources_it_did_not_touch`` pins that honesty:
the receipt must not imply a completeness it does not have.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def friday(tmp_path, monkeypatch):
    """An isolated ~/.friday holding one person, in every store that has them."""
    import agent_friday.core as core

    home = tmp_path / ".friday"
    (home / "knowledge-graph").mkdir(parents=True)
    (home / "wiki" / "people").mkdir(parents=True)
    monkeypatch.setattr(core, "FRIDAY_DIR", home)

    # 1. The people graph, and its legacy mirror.
    people = {"people": {"dana_okafor": {
        "name": "Dana Okafor", "aliases": ["Dana"], "entity_type": "human",
        "scores": {"reliability": 0.8, "overall": 0.8},
        "evidence": [{"note": "ran the migration review"}],
    }, "sam_reyes": {
        "name": "Sam Reyes", "aliases": [], "entity_type": "human",
        "scores": {"reliability": 0.5, "overall": 0.5}, "evidence": [],
    }}}
    (home / "people_graph.json").write_text(json.dumps(people), encoding="utf-8")
    (home / "trust_graph.json").write_text(json.dumps(people), encoding="utf-8")

    # 2. The knowledge graph: the person, a bystander, and an edge between them.
    entities = [
        {"id": "ent_dana", "title": "Dana Okafor", "type": "person",
         "description": "Colleague on the migration.", "tier": "B",
         "provenance": {"sensitivity": 1, "paths": ["wiki/people/dana-okafor.md"]}},
        {"id": "ent_sam", "title": "Sam Reyes", "type": "person",
         "description": "Colleague.", "tier": "B", "provenance": {"sensitivity": 1}},
        {"id": "ent_proj", "title": "Atlas Migration", "type": "project",
         "description": "The migration.", "tier": "B", "provenance": {"sensitivity": 1}},
    ]
    relationships = [
        {"id": "rel_dana_proj", "source": "ent_dana", "target": "ent_proj",
         "description": "Dana Okafor led the Atlas Migration.", "weight": 0.9},
        {"id": "rel_sam_proj", "source": "ent_sam", "target": "ent_proj",
         "description": "Sam Reyes reviewed it.", "weight": 0.5},
    ]
    kg = home / "knowledge-graph"
    (kg / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    (kg / "relationships.json").write_text(json.dumps(relationships), encoding="utf-8")
    (kg / "communities.json").write_text(json.dumps([]), encoding="utf-8")
    (kg / "community_reports.json").write_text(json.dumps([
        {"id": "c0", "title": "cluster B0",
         "summary": "Dana Okafor and Sam Reyes worked on the Atlas Migration."},
    ]), encoding="utf-8")

    # 3. A wiki page that mentions her -- a SOURCE, deliberately not rewritten.
    (home / "wiki" / "people" / "dana-okafor.md").write_text(
        "# Dana Okafor\n\nLed the Atlas migration review.\n", encoding="utf-8")

    return home


def test_the_person_is_findable_before_being_forgotten(friday):
    """You cannot honour a deletion request you cannot answer first."""
    from agent_friday.services import forget_person as fp

    report = fp.find("Dana Okafor")

    assert report["found"] is True
    assert report["people_graph"]["present"] is True
    assert report["knowledge_graph"]["entities"] == 1
    assert report["knowledge_graph"]["relationships"] == 1
    assert any("dana-okafor.md" in s for s in report["sources"]), report["sources"]


def test_forgetting_removes_them_from_both_people_graph_files(friday):
    """The legacy mirror is still read directly by server.py context builders.

    Removing from one file and not the other leaves the person alive in every
    consumer that reads the mirror -- which would be a delete button that
    reports success and does nothing.
    """
    from agent_friday.services import forget_person as fp

    fp.forget("Dana Okafor")

    for name in ("people_graph.json", "trust_graph.json"):
        data = json.loads((friday / name).read_text(encoding="utf-8"))
        assert "dana_okafor" not in data["people"], "%s still holds her" % name
        assert "sam_reyes" in data["people"], "%s lost an unrelated person" % name


def test_forgetting_removes_their_knowledge_graph_nodes_and_edges(friday):
    from agent_friday.services import forget_person as fp

    fp.forget("Dana Okafor")

    kg = friday / "knowledge-graph"
    ents = json.loads((kg / "entities.json").read_text(encoding="utf-8"))
    rels = json.loads((kg / "relationships.json").read_text(encoding="utf-8"))

    assert [e["id"] for e in ents] == ["ent_sam", "ent_proj"], ents
    assert [r["id"] for r in rels] == ["rel_sam_proj"], (
        "an edge pointing at a deleted entity was left behind -- a dangling "
        "reference that still names her in its description"
    )


def test_forgetting_scrubs_community_reports_that_name_them(friday):
    """Community reports are LLM prose. The name is in the text, not a field."""
    from agent_friday.services import forget_person as fp

    fp.forget("Dana Okafor")

    reports = json.loads(
        (friday / "knowledge-graph" / "community_reports.json").read_text(encoding="utf-8"))
    blob = json.dumps(reports)
    assert "Dana Okafor" not in blob, "her name survives in a community summary"
    assert "Sam Reyes" in blob, "the scrub removed an unrelated person's name"


def test_a_forgotten_person_is_not_resurrected_by_the_next_reindex(friday):
    """THE ONE THAT MATTERS.

    The knowledge graph is derived from the wiki and from conversation turns,
    and it rebuilds itself nightly at 03:30. Purging the entity without
    recording that she must stay purged produces a delete button that works
    until the user goes to bed.
    """
    from agent_friday.services import forget_person as fp

    fp.forget("Dana Okafor")

    assert fp.is_forgotten("Dana Okafor") is True
    assert fp.is_forgotten("dana okafor") is True, "matching must be case-insensitive"
    assert fp.is_forgotten("Dana") is True, "an alias must be honoured too"
    assert fp.is_forgotten("Sam Reyes") is False

    # And the indexer must consult that list rather than merely storing it.
    kept = fp.filter_entities([
        {"id": "ent_dana", "title": "Dana Okafor", "type": "person"},
        {"id": "ent_sam", "title": "Sam Reyes", "type": "person"},
    ])
    assert [e["title"] for e in kept] == ["Sam Reyes"], (
        "the indexer would write her straight back on the next nightly run"
    )


def test_the_receipt_names_sources_it_did_not_touch(friday):
    """Do not claim a completeness we do not have.

    Forgetting does not rewrite the user's own wiki pages -- those are theirs,
    in their words, in files they can open. The receipt has to say so, or the
    user will tell their colleague something untrue.
    """
    from agent_friday.services import forget_person as fp

    receipt = fp.forget("Dana Okafor")

    assert receipt["removed"]["people_graph"] == 1
    assert receipt["removed"]["kg_entities"] == 1
    assert any("dana-okafor.md" in s for s in receipt["remaining_sources"]), receipt
    assert (friday / "wiki" / "people" / "dana-okafor.md").exists(), (
        "forgetting silently deleted the user's own wiki page"
    )


def test_forgetting_someone_who_is_not_there_is_not_an_error(friday):
    from agent_friday.services import forget_person as fp

    receipt = fp.forget("Nobody At All")
    assert receipt["removed"]["people_graph"] == 0
    # Still recorded, so a person mentioned only in future conversations is
    # never derived in the first place.
    assert fp.is_forgotten("Nobody At All") is True


def test_forgetting_is_idempotent(friday):
    from agent_friday.services import forget_person as fp

    fp.forget("Dana Okafor")
    again = fp.forget("Dana Okafor")
    assert again["removed"]["people_graph"] == 0
    assert fp.is_forgotten("Dana Okafor") is True


def test_a_forget_can_be_undone_before_the_data_is_gone_forever(friday):
    """Un-forgetting stops future exclusion. It does NOT restore the records.

    Saying so in a test keeps the UI copy honest: this is a tombstone being
    lifted, not an undelete.
    """
    from agent_friday.services import forget_person as fp

    fp.forget("Dana Okafor")
    fp.unforget("Dana Okafor")

    assert fp.is_forgotten("Dana Okafor") is False
    data = json.loads((friday / "people_graph.json").read_text(encoding="utf-8"))
    assert "dana_okafor" not in data["people"], (
        "unforget restored deleted records -- it must only lift the tombstone"
    )


# -- The production paths, not just the helper -------------------------------

def test_tier_a_does_not_rebuild_a_forgotten_person_from_a_wiki_title(friday, monkeypatch):
    """Tier A derives an entity from every wiki page TITLE.

    So a page called "Dana Okafor" resurrects her through the structural path
    even after the Tier B extractor has been taught to skip her -- and Tier A
    needs no model, so it runs on every machine. The tombstone has to hold on
    every route into the graph, not the first one anyone thought of.

    This drives the real build function rather than the helper, because the
    helper being correct proves nothing about whether anything calls it.
    """
    import agent_friday.core as core
    from agent_friday.services import forget_person as fp
    from agent_friday.services.knowledge_graph import wiki_graph

    wiki = friday / "wiki"
    monkeypatch.setattr(core, "WIKI_DIR", wiki, raising=False)
    monkeypatch.setattr(wiki_graph, "WIKI_DIR", wiki, raising=False)
    (wiki / "people" / "sam-reyes.md").write_text(
        "# Sam Reyes\n\nSee [[Dana Okafor]].\n", encoding="utf-8")

    def titles():
        index = wiki_graph.build_wiki_index(wiki_dir=wiki)
        recs = wiki_graph.index_to_records(index)
        return {e.get("title") for e in recs["entities"]}

    before = titles()
    assert any("dana" in (t or "").lower() for t in before), (
        "the fixture's Dana page did not produce a Tier A entity, so this test "
        "would pass without proving anything: %r" % (before,)
    )

    fp.forget("Dana Okafor")

    after = titles()
    assert not any("dana" in (t or "").lower() for t in after), (
        "Tier A rebuilt a forgotten person from her wiki page title: %r" % (after,)
    )

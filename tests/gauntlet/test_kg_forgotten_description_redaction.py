"""Gauntlet finding Q9: forgetting a person doesn't stop them resurfacing via
someone else's description.

indexer.py's and wiki_graph.py's forgotten-people filters (services/
forget_person.py's ``forgotten_names()``) only ever checked an entity's
TITLE. A forgotten person's own titled node correctly could not resurface --
but nothing inspected entity DESCRIPTION text, and forget_person.forget()'s
one-time prose-scrub only touches ``community_reports`` at the moment of
forgetting, never future indexer writes. A later conversation mentioning the
forgotten person in relation to someone else ("Bob's colleague Jane
recommended the vendor", with Jane tombstoned) could write "Jane" straight
into the surviving "Bob" entity's description on the very next reindex,
completely untouched by any forgotten-name check.

The fix extends the existing title filter in both indexer.py (Tier B) and
wiki_graph.py (Tier A) to also redact forgotten names out of surviving
entities' descriptions, via a new shared primitive,
``forget_person.redact_forgotten_names()`` -- whole-word, case-insensitive,
so a tombstoned "Ann" cannot also eat "Anna".

Self-contained per this codebase's established gauntlet-probe pattern (see
test_kg_indexer_pin_enforcement.py / test_kg_indexer_cloud_extraction_cap.py):
small shared fixtures are duplicated locally rather than imported from
tests/unit/test_kg_indexer.py or tests/unit/test_forget_person.py.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import forget_person as fp
from agent_friday.services.knowledge_graph import indexer, wiki_graph
from agent_friday.services.knowledge_graph.store import KnowledgeGraphStore

CANNED_EXTRACTION = (
    '("entity"<|>BOB<|>person<|>Bob is a colleague. Jane Doe recommended '
    'the vendor for the migration.)\n##\n<|COMPLETE|>')


class RecordingLLM:
    """Stub for the indexer's injectable llm(messages, system, sens, mode)."""

    def __init__(self, extraction=CANNED_EXTRACTION):
        self.calls = []
        self._extraction = extraction

    def __call__(self, messages, system, sensitivity, mode, orb_label=None):
        self.calls.append(messages[0]["content"])
        if "-Goal-" in messages[0]["content"]:
            return self._extraction
        return "merged description"


@pytest.fixture
def isolated_friday_dir(tmp_path, monkeypatch):
    """A throwaway ~/.friday so forget_person's tombstone file (and any KG
    artifacts forget() touches) never leak into the shared test session
    home -- mirrors tests/unit/test_forget_person.py's own `friday` fixture."""
    import agent_friday.core as core
    home = tmp_path / ".friday"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    return home


class TestRedactForgottenNamesPrimitive:
    """Unit-level checks on the new forget_person.redact_forgotten_names()."""

    def test_redacts_a_tombstoned_name_case_insensitively(self, monkeypatch):
        monkeypatch.setattr(fp, "_load_tombstones", lambda: {
            "forgotten": [{"name": "Jane Doe", "aliases": []}]})
        out = fp.redact_forgotten_names("Bob's colleague JANE DOE recommended it.")
        assert "jane doe" not in out.lower()
        assert "[removed]" in out

    def test_whole_word_boundary_protects_a_similar_longer_name(self, monkeypatch):
        """'Ann' is tombstoned; 'Anna' (a different person, or just a longer
        name that happens to start with the same letters) must survive
        untouched -- a substring match here would be exactly the false
        positive this audit's own queue_reason warned against."""
        monkeypatch.setattr(fp, "_load_tombstones", lambda: {
            "forgotten": [{"name": "Ann", "aliases": []}]})
        out = fp.redact_forgotten_names("Anna reviewed the budget with Ann.")
        assert "Anna reviewed the budget" in out
        assert "with [removed]." in out

    def test_no_tombstones_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(fp, "_load_tombstones", lambda: {"forgotten": []})
        text = "Nothing here is forgotten."
        assert fp.redact_forgotten_names(text) == text

    def test_non_string_input_passes_through(self, monkeypatch):
        monkeypatch.setattr(fp, "_load_tombstones", lambda: {
            "forgotten": [{"name": "Jane", "aliases": []}]})
        assert fp.redact_forgotten_names(None) is None
        assert fp.redact_forgotten_names("") == ""


class TestTierBSurvivorDescriptionIsRedacted:
    """indexer.reindex_tier_b: a forgotten person named only inside a
    SURVIVING entity's description must be scrubbed there too, not just
    blocked from getting her own titled node."""

    def test_forgotten_name_inside_a_bystanders_description_is_scrubbed(
            self, isolated_friday_dir, tmp_path, monkeypatch):
        # Jane Doe was forgotten before this reindex pass runs.
        fp.forget("Jane Doe")
        assert fp.is_forgotten("Jane Doe")

        fake_chunks = [{
            "id": "chunk_bob", "source_path": "wiki/bob.md",
            "text": "Bob is a colleague. Jane Doe recommended the vendor.",
            "sensitivity": 1, "provenance": {"sensitivity": 1},
        }]
        monkeypatch.setattr(indexer, "gather_chunks", lambda: fake_chunks)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        info = indexer.reindex_tier_b(store=store, mode="full", llm=llm)

        assert info["extracted"] == 1
        entities = store.load("entities")
        titles = {e["title"]: e for e in entities}
        assert "Bob" in titles, "Bob's own entity should survive untouched"
        bob_desc = titles["Bob"]["description"]
        assert "jane doe" not in bob_desc.lower(), (
            "Jane Doe's name leaked into a surviving entity's description "
            "via the extraction pipeline -- forgetting her did not stop her "
            "resurfacing through someone else's node"
        )
        assert "[removed]" in bob_desc

    def test_a_name_that_was_never_forgotten_is_left_alone(
            self, isolated_friday_dir, tmp_path, monkeypatch):
        """No-op-shaped sanity check: without any tombstone, descriptions
        pass through unchanged -- the fix must not touch ordinary text."""
        fake_chunks = [{
            "id": "chunk_bob", "source_path": "wiki/bob.md",
            "text": "Bob is a colleague. Jane Doe recommended the vendor.",
            "sensitivity": 1, "provenance": {"sensitivity": 1},
        }]
        monkeypatch.setattr(indexer, "gather_chunks", lambda: fake_chunks)

        store = KnowledgeGraphStore(base_dir=tmp_path / "kg")
        llm = RecordingLLM()
        indexer.reindex_tier_b(store=store, mode="full", llm=llm)

        entities = {e["title"]: e for e in store.load("entities")}
        assert "jane doe" in entities["Bob"]["description"].lower()


class TestTierASurvivorDescriptionIsRedacted:
    """wiki_graph.index_to_records: same gap, structural path. A page's own
    first-paragraph summary (a DERIVED field this module builds, not the
    wiki page file itself) can name a forgotten person in relation to the
    page's own subject."""

    def test_forgotten_name_inside_a_pages_summary_is_scrubbed(
            self, isolated_friday_dir, tmp_path, monkeypatch):
        fp.forget("Jane Doe")

        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "bob.md").write_text(
            "# Bob\n\nBob is a colleague. Jane Doe recommended the vendor.\n",
            encoding="utf-8")

        index = wiki_graph.build_wiki_index(wiki_dir=wiki, include_soul=False,
                                            mention_edges=False)
        records = wiki_graph.index_to_records(index)

        titles = {e["title"]: e for e in records["entities"]}
        assert "Bob" in titles
        assert "jane doe" not in titles["Bob"]["description"].lower(), (
            "Jane Doe's name survived inside Bob's Tier A page-summary "
            "description after being forgotten"
        )

    def test_forgotten_persons_own_page_is_still_dropped(
            self, isolated_friday_dir, tmp_path, monkeypatch):
        """No-op-shaped sanity check: the pre-existing title filter this fix
        extends must still work -- Jane's own page node is excluded."""
        fp.forget("Jane Doe")

        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "jane.md").write_text(
            "# Jane Doe\n\nJane Doe works with the vendor team.\n",
            encoding="utf-8")

        index = wiki_graph.build_wiki_index(wiki_dir=wiki, include_soul=False,
                                            mention_edges=False)
        records = wiki_graph.index_to_records(index)

        assert "Jane Doe" not in {e["title"] for e in records["entities"]}

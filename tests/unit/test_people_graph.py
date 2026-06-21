"""Unit tests for people_graph.py — the human-contact trust graph.

Tests cover:
  - _key_for: name normalisation / deduplication key
  - _recompute_overall: mean of dimension scores, correctly clamped
  - Full CRUD lifecycle: add_person / edit / find / contacts_list
  - Duplicate guard: adding the same person twice → update, not duplicate
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from people_graph import PeopleGraph, PEOPLE_DIMENSIONS


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_graph(tmp_path):
    """A PeopleGraph rooted in an isolated temp dir."""
    return PeopleGraph(friday_dir=tmp_path)


# ── _key_for ───────────────────────────────────────────────────────────────────

class TestKeyFor:
    def test_lowercases(self):
        assert PeopleGraph._key_for("Alice") == "alice"

    def test_strips_whitespace(self):
        assert PeopleGraph._key_for("  Bob  ") == "bob"

    def test_replaces_spaces_with_underscore(self):
        assert PeopleGraph._key_for("John Doe") == "john_doe"

    def test_replaces_hyphens_with_underscore(self):
        assert PeopleGraph._key_for("Mary-Jane") == "mary_jane"

    def test_combined_normalisation(self):
        assert PeopleGraph._key_for("  Dr. Evil-Genius  ") == "dr._evil_genius"

    def test_empty_string_returns_empty(self):
        assert PeopleGraph._key_for("") == ""

    def test_none_returns_empty(self):
        assert PeopleGraph._key_for(None) == ""

    def test_same_name_same_key(self):
        """Ensures two spellings that normalise identically produce the same key."""
        assert PeopleGraph._key_for("alice smith") == PeopleGraph._key_for("Alice Smith")


# ── _recompute_overall ─────────────────────────────────────────────────────────

class TestRecomputeOverall:
    def test_mean_of_four_equal_values(self):
        scores = {"reliability": 1.0, "emotional_safety": 1.0,
                  "alignment": 1.0, "competence": 1.0}
        result = PeopleGraph._recompute_overall(scores)
        assert result["overall"] == pytest.approx(1.0)

    def test_mean_of_mixed_values(self):
        scores = {"reliability": 0.0, "emotional_safety": 1.0,
                  "alignment": 0.5, "competence": 0.5}
        result = PeopleGraph._recompute_overall(scores)
        assert result["overall"] == pytest.approx(0.5)

    def test_overall_excluded_from_mean(self):
        """The 'overall' field itself must not be included in the mean calculation."""
        scores = {"overall": 999.0, "reliability": 0.8, "competence": 0.6}
        result = PeopleGraph._recompute_overall(scores)
        # Only 0.8 + 0.6 = 1.4 / 2 = 0.7
        assert result["overall"] == pytest.approx(0.7)

    def test_non_numeric_values_skipped(self):
        scores = {"reliability": 0.9, "emotional_safety": "N/A", "competence": 0.7}
        result = PeopleGraph._recompute_overall(scores)
        # Only 0.9 + 0.7 = 1.6 / 2 = 0.8
        assert result["overall"] == pytest.approx(0.8)

    def test_modifies_in_place_and_returns(self):
        scores = {"reliability": 0.4, "competence": 0.6}
        returned = PeopleGraph._recompute_overall(scores)
        assert returned is scores  # same object mutated
        assert "overall" in scores

    def test_empty_scores_no_crash(self):
        """Empty scores dict should not raise."""
        scores = {}
        PeopleGraph._recompute_overall(scores)
        # No assertion — just must not raise; 'overall' may or may not be set.


# ── CRUD lifecycle ─────────────────────────────────────────────────────────────

class TestAddPerson:
    def test_add_and_find(self, tmp_graph):
        key, err = tmp_graph.add_person("Carol Kane")
        assert err is None
        assert key == "carol_kane"
        person = tmp_graph.find("carol_kane")
        assert person is not None
        assert person["name"] == "Carol Kane"

    def test_add_creates_canonical_dimensions(self, tmp_graph):
        tmp_graph.add_person("Dana Scully")
        person = tmp_graph.find("dana_scully")
        for dim in PEOPLE_DIMENSIONS:
            assert dim in person["scores"]

    def test_add_empty_name_returns_error(self, tmp_graph):
        key, err = tmp_graph.add_person("")
        assert key is None
        assert err is not None

    def test_add_none_name_returns_error(self, tmp_graph):
        key, err = tmp_graph.add_person(None)
        assert key is None
        assert err is not None

    def test_add_with_aliases(self, tmp_graph):
        tmp_graph.add_person("Edwin Hubble", aliases=["Ed", "E. Hubble"])
        person = tmp_graph.find("ed")
        # find should resolve via alias
        assert person is not None
        assert person["name"] == "Edwin Hubble"

    def test_default_scores_are_0_5(self, tmp_graph):
        tmp_graph.add_person("Frank Test")
        person = tmp_graph.find("frank_test")
        for dim in PEOPLE_DIMENSIONS:
            assert person["scores"][dim] == pytest.approx(0.5)

    def test_add_persists_to_disk(self, tmp_graph):
        tmp_graph.add_person("Grace Hopper")
        # Re-instantiate from same dir — data should survive
        pg2 = PeopleGraph(friday_dir=tmp_graph.friday_dir)
        assert pg2.find("grace_hopper") is not None


class TestDuplicateGuard:
    def test_adding_same_person_twice_returns_error(self, tmp_graph):
        tmp_graph.add_person("Hiro Protagonist")
        key2, err2 = tmp_graph.add_person("Hiro Protagonist")
        assert key2 is None
        assert err2 is not None
        assert "already exists" in err2.lower() or "exists" in err2.lower()

    def test_second_add_does_not_duplicate(self, tmp_graph):
        tmp_graph.add_person("Iris West")
        tmp_graph.add_person("Iris West")  # ignored / error
        contacts = tmp_graph.contacts_list()
        names = [c["name"] for c in contacts]
        assert names.count("Iris West") == 1


class TestEdit:
    def test_edit_updates_scores(self, tmp_graph):
        tmp_graph.add_person("James Kirk")
        person, err = tmp_graph.edit("james_kirk", scores={"reliability": 0.9})
        assert err is None
        assert person["scores"]["reliability"] == pytest.approx(0.9)

    def test_edit_recomputes_overall(self, tmp_graph):
        tmp_graph.add_person("Leia Organa")
        tmp_graph.edit("leia_organa", scores={
            "reliability": 1.0, "emotional_safety": 1.0,
            "alignment": 1.0, "competence": 1.0,
        })
        person = tmp_graph.find("leia_organa")
        assert person["scores"]["overall"] == pytest.approx(1.0)

    def test_edit_appends_evidence(self, tmp_graph):
        tmp_graph.add_person("Mulder Fox")
        tmp_graph.edit("mulder_fox", add_evidence={
            "type": "observation",
            "magnitude": 0.8,
            "notes": "very reliable on X-Files cases",
            "dimension": "reliability",
        })
        person = tmp_graph.find("mulder_fox")
        assert len(person["evidence"]) == 1
        assert person["evidence"][0]["notes"] == "very reliable on X-Files cases"

    def test_edit_missing_person_returns_error(self, tmp_graph):
        person, err = tmp_graph.edit("nobody_at_all")
        assert person is None
        assert err is not None

    def test_edit_persists(self, tmp_graph):
        tmp_graph.add_person("Nyota Uhura")
        tmp_graph.edit("nyota_uhura", scores={"competence": 0.99})
        pg2 = PeopleGraph(friday_dir=tmp_graph.friday_dir)
        person = pg2.find("nyota_uhura")
        assert person["scores"]["competence"] == pytest.approx(0.99)


class TestFind:
    def test_find_by_key(self, tmp_graph):
        tmp_graph.add_person("Owen Lars")
        assert tmp_graph.find("owen_lars") is not None

    def test_find_case_insensitive(self, tmp_graph):
        tmp_graph.add_person("Padme Amidala")
        assert tmp_graph.find("PADME AMIDALA") is not None

    def test_find_nonexistent_returns_none(self, tmp_graph):
        assert tmp_graph.find("nobody_here") is None

    def test_find_empty_returns_none(self, tmp_graph):
        assert tmp_graph.find("") is None

    def test_find_guarantees_all_dimensions(self, tmp_graph):
        """find() must ensure all canonical dims present even for legacy entries."""
        tmp_graph.add_person("Quinn Mallory")
        person = tmp_graph.find("quinn_mallory")
        for dim in PEOPLE_DIMENSIONS:
            assert dim in person["scores"]


class TestContactsList:
    def test_empty_graph_returns_empty_list(self, tmp_graph):
        assert tmp_graph.contacts_list() == []

    def test_contacts_sorted_by_overall_desc(self, tmp_graph):
        tmp_graph.add_person("A Person")
        tmp_graph.add_person("B Person")
        tmp_graph.edit("a_person", scores={
            "reliability": 0.2, "emotional_safety": 0.2,
            "alignment": 0.2, "competence": 0.2,
        })
        tmp_graph.edit("b_person", scores={
            "reliability": 0.9, "emotional_safety": 0.9,
            "alignment": 0.9, "competence": 0.9,
        })
        contacts = tmp_graph.contacts_list()
        assert contacts[0]["name"] == "B Person"
        assert contacts[1]["name"] == "A Person"

    def test_contacts_list_shape(self, tmp_graph):
        tmp_graph.add_person("Shape Tester")
        c = tmp_graph.contacts_list()[0]
        for field in ("name", "aliases", "domains", "overall",
                      "last_interaction", "evidence_count"):
            assert field in c

    def test_contacts_list_multiple_people(self, tmp_graph):
        for name in ("Alpha", "Beta", "Gamma"):
            tmp_graph.add_person(name)
        assert len(tmp_graph.contacts_list()) == 3


# ── Legacy mirror ──────────────────────────────────────────────────────────────

class TestLegacyMirror:
    def test_save_creates_both_files(self, tmp_graph):
        tmp_graph.add_person("Twin Files")
        assert tmp_graph.path.exists()
        assert tmp_graph.legacy_path.exists()

    def test_legacy_file_has_same_content(self, tmp_graph):
        tmp_graph.add_person("Mirror Image")
        import json
        primary = json.loads(tmp_graph.path.read_text(encoding="utf-8"))
        legacy = json.loads(tmp_graph.legacy_path.read_text(encoding="utf-8"))
        assert primary == legacy


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

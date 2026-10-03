""""Save to Trust Graph" must not lose data.

Red on main 7c49be86: `_flow_trust_graph` wrote only the legacy mirror
(`trust_graph.json`), with the old five dimensions, so `PeopleGraph.load()`
never saw the saved intelligence (the canonical file wins) and the next
`PeopleGraph.save()` mirrored the canonical file over it: the intelligence
was gone. The writer now goes through `PeopleGraph` into the canonical store.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.people_graph import PeopleGraph, PEOPLE_DIMENSIONS


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.services import misc_engine as me
    monkeypatch.setattr(me, "FRIDAY_DIR", tmp_path)
    pg = PeopleGraph(friday_dir=tmp_path)
    pg.save({"people": {"pat_example": {
        "name": "Pat Example", "aliases": ["Pat"], "entity_type": "human",
        "scores": {d: 0.5 for d in PEOPLE_DIMENSIONS}, "evidence": [], "domains": []}}})
    assert (tmp_path / "people_graph.json").exists(), "the canonical file must already exist"
    return tmp_path


def test_sendto_then_a_people_graph_save_keeps_the_intelligence(home):
    from agent_friday.services.misc_engine import _flow_trust_graph
    out = _flow_trust_graph("SAVED-INTEL: prefers morning calls", {"person_name": "Pat Example"})
    assert out["ok"], out
    pg = PeopleGraph(friday_dir=home)
    graph = pg.load()
    intel = graph["people"]["pat_example"].get("intelligence") or []
    assert any("SAVED-INTEL" in i.get("content", "") for i in intel), \
        "the saved intelligence never reached the canonical store"
    pg.save(graph)   # any later save, e.g. an edit or an import
    again = PeopleGraph(friday_dir=home).load()
    assert any("SAVED-INTEL" in i.get("content", "")
               for i in again["people"]["pat_example"].get("intelligence") or [])
    mirror = json.loads((home / "trust_graph.json").read_text(encoding="utf-8"))
    assert any("SAVED-INTEL" in i.get("content", "")
               for i in mirror["people"]["pat_example"].get("intelligence") or [])


def test_sendto_finds_a_person_by_alias(home):
    from agent_friday.services.misc_engine import _flow_trust_graph
    out = _flow_trust_graph("note", {"person_name": "Pat"})
    assert out["ok"] and out["person"] == "pat_example"
    graph = PeopleGraph(friday_dir=home).load()
    assert len(graph["people"]) == 1, "an alias must not create a second person"


def test_sendto_creates_a_person_with_the_canonical_dimensions(home):
    from agent_friday.services.misc_engine import _flow_trust_graph
    out = _flow_trust_graph("note", {"person_name": "Sam New"})
    assert out["ok"]
    person = PeopleGraph(friday_dir=home).load()["people"]["sam_new"]
    assert set(PEOPLE_DIMENSIONS) <= set(person["scores"])
    assert "information_quality" not in person["scores"] and "timeliness" not in person["scores"]


def test_sendto_without_a_name_is_refused(home):
    from agent_friday.services.misc_engine import _flow_trust_graph
    assert _flow_trust_graph("note", {})["ok"] is False

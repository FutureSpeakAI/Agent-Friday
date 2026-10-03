"""Evidence about a person comes from the owner's own dealings only.

Red on main 7c49be86: `PeopleGraph.edit` accepted any evidence. Anything
from the public web, a profile service or a research pass is refused at
write time with `origin_not_allowed`, and the refusal leaves no event.
"""
from __future__ import annotations

import pytest

from agent_friday.people_graph import PeopleGraph


@pytest.fixture
def pg(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    g = PeopleGraph(friday_dir=tmp_path)
    g.add_person("Pat Example")
    return g


@pytest.mark.parametrize("origin", ["web", "research", "profile_service", "data_broker", "other_person"])
def test_outside_origins_are_refused(pg, origin):
    from agent_friday.trust import people as tp
    before = len(tp.log_for("pat_example"))
    person, err = pg.edit("pat_example", add_evidence={"type": "claim", "origin": origin,
                                                       "notes": "found online", "dimension": "reliability"})
    assert person is None and err and err.startswith("origin_not_allowed")
    assert pg.load()["people"]["pat_example"]["evidence"] == []
    assert len(tp.log_for("pat_example")) == before, "a refusal must leave no event"


@pytest.mark.parametrize("origin", ["owner", "system"])
def test_the_owners_own_dealings_are_accepted(pg, origin):
    person, err = pg.edit("pat_example", add_evidence={"type": "kept_commitment", "origin": origin,
                                                       "dimension": "reliability"})
    assert err is None and person["evidence"][-1]["origin"] == origin


def test_scores_are_clamped_to_the_unit_interval(pg):
    person, err = pg.edit("pat_example", scores={"reliability": 7, "competence": -2})
    assert err is None
    assert person["scores"]["reliability"] == 1.0 and person["scores"]["competence"] == 0.0

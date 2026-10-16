"""query_trust_graph shows an outlet's or organisation's full trust scores.

Red on main 2f88c152a: query_trust_graph looked up people only. Asked about a
news outlet it answered "No trust-graph entry found", so no chat or voice tool
could show an outlet's dimensions, what they rest on or how much evidence they
have; an organisation came back as a person (name and aliases only on a cloud
loop, no scores).
"""
from __future__ import annotations

import json

import pytest

from agent_friday import source_trust_graph as stg
from agent_friday.services import agent as ag

PERSON = {"name": "Pat Example", "aliases": ["Pat"], "entity_type": "human",
          "role": "accountant", "scores": {"reliability": 0.91},
          "evidence": [{"type": "kept_commitment", "notes": "returned the contract"}]}
ORG = {"name": "Example Water Board", "aliases": ["the water board"], "entity_type": "organization",
       "scores": {"overall": 0.6, "reliability": 0.7, "competence": 0.5},
       "evidence": [{"type": "observation", "dimension": "reliability", "magnitude": 0.7,
                     "notes": "PRIVATE-NOTE fixed the main within a day"}]}


@pytest.fixture
def graphs(monkeypatch, tmp_path):
    from agent_friday.services import misc_engine as me
    monkeypatch.setattr(me, "_load_trust_graph", lambda *a, **k: {"people": {
        "pat_example": PERSON, "example_water_board": ORG}})
    g = stg.SourceTrustGraph(friday_dir=tmp_path)
    monkeypatch.setattr(stg, "get_source_trust_graph", lambda friday_dir=None: g)
    g.analyze_fetch([{"source": "outlet.test", "title": "Council votes",
                      "snippet": "Officials said the vote passed.",
                      "url": "https://outlet.test/council"}], [])
    g.record_user_action("outlet.test", "boost")
    return g


def _q(**inp):
    tok = ag._LOOP_PROVIDER.set("anthropic")   # a cloud loop
    try:
        return ag._tool_query_trust_graph(inp)
    finally:
        ag._LOOP_PROVIDER.reset(tok)


@pytest.mark.parametrize("name", ["outlet.test", "https://www.outlet.test/council?utm_source=x"])
def test_an_outlet_returns_every_dimension_with_its_basis(graphs, name):
    out = json.loads(_q(name=name))
    assert out["kind"] == "source" and out["domain"] == "outlet.test"
    dims = out["dimensions"]
    assert set(dims) == set(stg.DIMENSIONS)
    for d in dims.values():
        assert d["based_on"] and "evidence_count" in d and "status" in d
    assert dims["source_attribution"]["evidence_count"] == 1
    assert dims["prediction_accuracy"]["status"] == "not measured"
    assert dims["prediction_accuracy"]["score"] is None
    assert out["evidence_count"] >= 1
    assert out["owner_preference"]["state"] == "boosted"
    assert out["trust_score"] > out["evidence_score"]


def test_an_outlet_by_its_name_and_by_explicit_kind(graphs):
    data = json.loads(graphs.path.read_text(encoding="utf-8"))
    data["sources"]["outlet.test"]["name"] = "The Outlet"
    graphs.path.write_text(json.dumps(data), encoding="utf-8")
    assert json.loads(_q(name="The Outlet"))["domain"] == "outlet.test"
    assert json.loads(_q(name="outlet.test", kind="source"))["kind"] == "source"


def test_a_never_seen_outlet_says_its_score_is_only_a_prior(graphs):
    out = json.loads(_q(name="reuters.com", kind="source"))
    assert out["known"] is False and "prior" in out["note"]
    assert out["prior"]["based_on"] == "built-in high-trust outlet list"


def test_an_organisation_returns_its_dimensions_without_notes(graphs):
    out_text = _q(name="the water board")
    out = json.loads(out_text)
    assert out["kind"] == "organisation"
    assert out["dimensions"]["reliability"]["score"] == 0.7
    assert out["dimensions"]["reliability"]["evidence_count"] == 1
    assert out["dimensions"]["competence"]["evidence_count"] == 0
    assert out["evidence_count"] == 1
    assert "PRIVATE-NOTE" not in out_text


def test_a_person_is_unchanged_and_still_stays_home(graphs):
    out = _q(name="Pat")
    assert "0.91" not in out and "kept_commitment" not in out
    assert json.loads(out)["role"] == "accountant"
    # Asked for a source, a person's name is not looked up as a person.
    assert "No trust-graph entry" in _q(name="Pat", kind="source")


def test_an_unknown_kind_is_refused_with_the_reason(graphs):
    assert "Unknown kind" in _q(name="outlet.test", kind="planet")


def test_the_tool_declares_kind_and_voice_reaches_outlets_natively():
    spec = next(t for t in ag.CLAUDE_TOOLS if t.get("name") == "query_trust_graph")
    props = spec["input_schema"]["properties"]
    assert set(props["kind"]["enum"]) == {"any", "person", "organisation", "source"}
    assert spec["input_schema"]["required"] == ["name"]
    from agent_friday.services import voice_engine as ve
    # Voice reads an outlet's breakdown through its own get_source_trust tool;
    # query_trust_graph stays off the spoken contract, which has a fixed budget.
    assert "query_trust_graph" not in ve._VOICE_SHARED_TOOLS
    assert "get_source_trust" in ve.build_voice_tool_contract()["names"]

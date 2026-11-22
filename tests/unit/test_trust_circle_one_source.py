"""The trust circle in the context and query_trust_graph read one source and one naming rule. SYNTHETIC PEOPLE ONLY.

The context builders used to open the legacy trust_graph.json directly and print each entry's dict KEY ("jane_smith"), while
query_trust_graph loads the canonical people graph and matched only display names and aliases, so a name taken from the context
was "not found". Now one loader (PeopleGraph.load, canonical file first, legacy mirror as its seed) feeds both, the context lists
display names, and the tool accepts a display name, an alias or the entry's key. What each check bites on:
  1. every person the live-voice context lists is found by the tool, whether it is asked for by the listed name or by the key;
  2. the same for the vault summary the text-chat context is built from;
  3. when the canonical file and the legacy mirror disagree, every reader shows the canonical one (a stale mirror is not a store);
  4. the list-shaped legacy file and aliases give the same agreement;
  5. the check can fail: with only the legacy file and a key that differs from the name, the old readers print the key.
"""
import json

import pytest

import agent_friday.people_graph as pg
from agent_friday.services import agent as ag
from agent_friday.services import model_router, voice_engine

PEOPLE = {
    "jane_smith": {"name": "Jane Smith", "aliases": ["Janey"], "scores": {"overall": 0.5}, "entity_type": "human"},
    "dr_a_o_neil": {"name": "Dr. A. O'Neil", "scores": {"overall": 0.7}, "entity_type": "human"},
    "pat": {"name": "Patricia Example", "aliases": ["Pat E"], "scores": {"overall": 0.6}, "entity_type": "human"},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "_instance", pg.PeopleGraph(friday_dir=tmp_path))
    for mod in (voice_engine, model_router):
        monkeypatch.setattr(mod, "FRIDAY_DIR", tmp_path)
    return tmp_path


def _legacy(home, people):
    (home / "trust_graph.json").write_text(json.dumps({"version": 1, "people": people}), encoding="utf-8")


def _tool(name):
    return ag._tool_query_trust_graph({"name": name, "kind": "person"})


def _found(answer):
    return "No trust-graph entry found" not in answer


def test_every_person_the_live_voice_context_lists_is_found_by_the_tool(home):
    _legacy(home, PEOPLE)
    context = voice_engine._build_live_context()
    assert "TRUST CIRCLE" in context
    section = context.split("TRUST CIRCLE")[1]
    assert "jane_smith" not in section and "dr_a_o_neil" not in section, "the circle lists names, not dict keys"
    for key, person in PEOPLE.items():
        assert person["name"] in section, "the context lists the display name of %r" % key
        assert _found(_tool(person["name"])), person["name"]
        assert _found(_tool(key)), "the key is accepted too: %s" % key
        for alias in person.get("aliases", []):
            assert _found(_tool(alias)), alias


def test_every_person_in_the_text_chat_vault_summary_is_found_by_the_tool(home):
    _legacy(home, PEOPLE)
    listed = model_router._load_vault_summary()["trust_people"]
    assert sorted(listed) == sorted(p["name"] for p in PEOPLE.values())
    for name in listed:
        assert _found(_tool(name)), name


def test_a_stale_legacy_mirror_never_shows_when_the_canonical_file_says_otherwise(home):
    _legacy(home, PEOPLE)
    (home / "people_graph.json").write_text(json.dumps({"people": {"alice": {"name": "Alice Example"}}}), encoding="utf-8")
    context = voice_engine._build_live_context()
    assert "Alice Example" in context and "Jane Smith" not in context
    assert list(model_router._load_vault_summary()["trust_people"]) == ["Alice Example"]
    assert _found(_tool("Alice Example")) and not _found(_tool("Jane Smith"))


def test_the_list_shaped_legacy_file_agrees_too(home):
    _legacy(home, [{"name": "Jane Smith", "aliases": ["Janey"]}, {"name": "Pat Example"}])
    listed = model_router._load_vault_summary()["trust_people"]
    assert sorted(listed) == ["Jane Smith", "Pat Example"]
    assert all(_found(_tool(n)) for n in listed) and _found(_tool("Janey"))
    assert "Jane Smith" in voice_engine._build_live_context()


def test_an_empty_circle_lists_nobody_and_the_tool_says_so(home):
    context = voice_engine._build_live_context()
    assert "TRUST CIRCLE" not in context
    assert model_router._load_vault_summary().get("trust_people") in (None, {})
    assert not _found(_tool("Nobody Example"))

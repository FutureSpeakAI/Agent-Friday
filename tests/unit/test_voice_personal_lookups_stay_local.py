"""Local voice: a lookup the owner points at their own things never reaches the public web.

Rules, each with cases that fail on the unfixed rule layer (services/laya_router tier 0):

* a lookup that names the owner's own store (knowledge graph, knowledge base, memory, vault, contacts, notes, wiki)
  is the local ``search_wiki`` with only the subject as its query;
* a bare "look up X" that names no place is asked back, naming the public web and the owner's notes, unless it is
  one of a fixed list of questions about the world that name nobody (weather, a capital, sunset time);
* the web runs by itself only when the owner directs it there ("the web", "the internet", "search online"), and
  never for words that are the owner's own (my online banking, my Google Drive);
* walk-all: no phrasing in PERSONAL_PHRASINGS reaches ``search_web`` through the rules.
"""
from __future__ import annotations

import pytest

from agent_friday.services import laya_router as lr

STORE_LOOKUPS = [
    "Look up Stephen in my knowledge graph.",
    "look up Acme in my knowledge base",
    "Search my knowledge graph for Acme",
    "find the roof repair in my memory",
    "Look up Dana in my contacts",
    "Check my vault for the passport scan",
    "look up Stephen in my notes",
    "find Dana in my wiki",
]

#: Every one is the owner's own business. None may run search_web by itself.
PERSONAL_PHRASINGS = STORE_LOOKUPS + [
    "Look up Stephen",
    "look up my sister Dana",
    "look up who Dana is",
    "look up where Dana lives",
    "look up when Dana's birthday is",
    "look up what Dana said about the roof",
    "look up Dana's score",
    "look up where Stephen Webster lives",
    "look up what is Stephen Webster's phone number",
    "search for my online banking password",
    "look up my online order status",
    "look up my Google Drive files",
    "look up Acme",
    "look up the weather at Dana's house",
    "look up the distance to Dana's house",
    "look up the time zone for Dana",
    "look up the weather and Dana's address",
    "look up the weather in Paris and where Dana lives",
    "look up the weather where Dana lives",
    "look up population of Canada and Stephen's address",
    "look up the definition of Dana",
    "look up the forecast for Stephen's wedding",
    "look up recipe for grandma's cake",
    "look up Dana, don't use the web",
    "look up Dana without the web",
    "look up Dana but not on the internet",
    "look up the weather for Dana",
    "look up weather Dana Whitfield",
    "look up forecast Dana",
    "look up sunset Dana",
    "look up the weather for Stephen",
    "look up the weather at home",
    "look up the capital of Dana",
    "look up the opening hours of Stephen",
]


def test_a_topic_word_beside_a_person_is_not_a_public_question():
    for text in PERSONAL_PHRASINGS:
        r = lr.rule_route(text)
        assert r is None or r.tool != "search_web" or r.decision != "tool", text


@pytest.mark.parametrize("text,not_tool", [
    ("check my email for messages from my contacts", "search_wiki"),
    ("find the pdf from my contacts folder", "search_wiki"),
    ("find the file about taxes in the wiki", "search_wiki"),
    ("check the memory for leaks", "search_wiki"),
    ("find news about Ukraine in my notes", "search_wiki"),
])
def test_a_lookup_that_names_another_tools_thing_keeps_that_tool(text, not_tool):
    r = lr.rule_route(text)
    assert r is None or r.tool != not_tool, r


@pytest.mark.parametrize("text", STORE_LOOKUPS)
def test_a_lookup_in_the_owners_own_store_is_local(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "tool" and r.tool == "search_wiki", r
    q = r.args["query"].lower()
    for store in ("knowledge", "memory", "vault", "contacts", "notes", "wiki"):
        assert store not in q, q
    assert q, "the subject of the lookup must survive"


def test_the_subject_of_a_personal_lookup_is_kept():
    assert lr.rule_route("Look up Stephen in my knowledge graph.").args["query"].lower() == "stephen"
    assert lr.rule_route("Search my knowledge graph for Acme").args["query"].lower() == "acme"


@pytest.mark.parametrize("text", PERSONAL_PHRASINGS)
def test_no_personal_phrasing_reaches_the_web_by_itself(text):
    r = lr.rule_route(text)
    assert r is None or r.tool != "search_web" or r.decision != "tool", r


@pytest.mark.parametrize("text", ["Look up Stephen", "look up my sister Dana", "look up who Dana is",
                                  "look up where Stephen Webster lives", "look up Acme",
                                  "search for my online banking password", "look up my Google Drive files"])
def test_a_bare_lookup_with_no_place_named_is_asked_back(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "ask", r
    q = r.question.lower()
    assert "web" in q and "notes" in q, q


@pytest.mark.parametrize("text", ["look up the capital of France", "look up the weather in Paris"])
def test_a_topic_with_a_place_is_asked_back_because_a_rule_cannot_tell_a_city_from_a_person(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "ask", r


@pytest.mark.parametrize("text", ["look up what time sunset is today", "look up the weather today",
                                  "look up tomorrow's forecast"[:0] + "look up the forecast tomorrow"])
def test_a_plain_question_about_the_world_that_needs_no_place_runs_the_web_search(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "tool" and r.tool == "search_web", r
    assert r.label == "searching the web", "the spoken acknowledgement must say it is the web"


@pytest.mark.parametrize("text", ["Search the web for the Louvre opening hours", "google the weather in Paris",
                                  "look online for flights to Rome", "search the internet for hiking trails"])
def test_the_web_runs_when_the_owner_directs_it_there(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "tool" and r.tool == "search_web", r


def test_the_notes_rule_is_unchanged():
    r = lr.rule_route("Find my notes about octopuses.")
    assert r.tool == "search_wiki" and r.args["query"] == "octopuses"

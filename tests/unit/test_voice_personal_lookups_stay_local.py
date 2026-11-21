"""Local voice: a lookup in the owner's own stores never reaches the public web.

Measured 2026-11-21 on the live desktop: "Look up Stephen in my knowledge graph" was routed to ``search_web`` with
the query "Stephen in my knowledge graph" and read the web's answer aloud. The rule layer matched the bare
"look up" verb and never looked at where the owner said to look. These tests pin three rules, each with a case
that fails on that code:

* a lookup that names the owner's own store (knowledge graph, knowledge base, memory, vault, contacts) is a local
  ``search_wiki``, with only the subject as its query, and is never ``search_web``;
* a bare "look up X" that does not say where to look is asked back, naming the public web and the owner's notes,
  and never runs a web search by itself (a person's name is not a public-world question);
* the web runs only when the owner says so ("search the web for", "google", "online"), and the Route says it
  is the web, so the spoken acknowledgement can name it.
"""
from __future__ import annotations

import pytest

from agent_friday.services import laya_router as lr

PERSONAL = [
    "Look up Stephen in my knowledge graph.",
    "look up Acme in my knowledge base",
    "Search my knowledge graph for Acme",
    "find the roof repair in my memory",
    "Look up Dana in my contacts",
    "Check my vault for the passport scan",
]


@pytest.mark.parametrize("text", PERSONAL)
def test_a_lookup_in_the_owners_own_store_is_local_and_never_the_web(text):
    r = lr.rule_route(text)
    assert r is not None and r.tool == "search_wiki", r
    assert r.decision == "tool"
    q = r.args["query"].lower()
    assert "knowledge" not in q and "memory" not in q and "vault" not in q and "contacts" not in q, q
    assert q, "the subject of the lookup must survive"


def test_the_subject_of_a_personal_lookup_is_kept():
    assert lr.rule_route("Look up Stephen in my knowledge graph.").args["query"].lower() == "stephen"
    assert lr.rule_route("Search my knowledge graph for Acme").args["query"].lower() == "acme"


@pytest.mark.parametrize("text", ["Look up Stephen", "look up my sister Dana", "look up Acme"])
def test_a_bare_lookup_with_no_place_named_is_asked_back_not_sent_to_the_web(text):
    r = lr.rule_route(text)
    assert r is not None
    assert r.decision == "ask", r
    q = r.question.lower()
    assert "web" in q and "notes" in q, q
    assert r.tool != "search_web" or r.decision == "ask"


@pytest.mark.parametrize("text", ["look up what time sunset is today", "look up the capital of France"])
def test_a_plain_question_about_the_world_still_runs_the_web_search(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "tool" and r.tool == "search_web", r
    assert r.label == "searching the web", "the spoken acknowledgement must say it is the web"


@pytest.mark.parametrize("text", ["Search the web for the Louvre opening hours", "google the weather in Paris",
                                  "look online for flights to Rome"])
def test_the_web_runs_only_when_the_owner_says_the_web(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "tool" and r.tool == "search_web", r


def test_the_notes_rule_is_unchanged():
    r = lr.rule_route("Find my notes about octopuses.")
    assert r.tool == "search_wiki" and r.args["query"] == "octopuses"

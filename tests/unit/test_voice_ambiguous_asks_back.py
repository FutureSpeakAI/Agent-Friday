"""Local voice: a command with no recognisable object is asked back with named options, never guessed at.

A vague imperative (do/fix/handle/sort out/take care of + the thing, the stuff) is an ``ask`` whose question names
two or three things the owner may mean. A command with a real object is unchanged, and so is a bare "do it" /
"fix that": its object may be the conversation's own last offer, which the conversation carries out.
"""
from __future__ import annotations

import pytest

from agent_friday.services import laya_router as lr

VAGUE = [
    "Do the thing with the file.",
    "Do the thing.",
    "Sort out the stuff.",
    "Take care of the thing.",
    "please deal with the stuff",
    "Can you do the thing with the file?",
    "Friday, handle the things.",
]

ANSWERS_AN_OFFER = ["Do it.", "Do that.", "Run it.", "Fix it.", "Handle that.", "Do this", "Friday, do it",
                    "please deal with this"]


@pytest.mark.parametrize("text", VAGUE)
def test_a_vague_command_is_asked_back_with_named_options(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "ask", r
    q = r.question
    assert " or " in q and "," in q, q
    assert q.rstrip().endswith("?"), q
    assert r.tool in (None, ""), "nothing may be run or handed to the deeper mind"


@pytest.mark.parametrize("text", ANSWERS_AN_OFFER)
def test_a_bare_do_it_is_left_to_the_conversation_not_asked_back(text):
    r = lr.rule_route(text)
    assert r is None or r.question != lr._ASK_WHAT_TO_DO, r
    assert not lr._VAGUE_COMMAND.match(text)


@pytest.mark.parametrize("text,decision,tool", [
    ("Send the report to the team.", "defer", None),
    ("Open the news workspace.", "tool", "navigate_to"),
    ("Delete the note titled TEST voice check.", "defer", None),
    ("Set a timer for two minutes.", "defer", None),
    ("What's on my calendar today?", "tool", "query_calendar"),
])
def test_a_command_with_a_real_object_is_unchanged(text, decision, tool):
    r = lr.rule_route(text)
    assert r is not None and r.decision == decision and (r.tool or None) == tool, r

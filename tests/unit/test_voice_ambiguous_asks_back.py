"""Local voice: a command with no recognisable object is asked back with named options, never guessed at.

"Do the thing with the file." was handed to the deeper mind, which invented a job and raised an approval card.
A vague imperative (do/fix/handle/sort out/take care of + it, that, this, the thing, the stuff) is now an ``ask``
whose question names two or three things the owner may mean. A command with a real object is unchanged.
"""
from __future__ import annotations

import pytest

from agent_friday.services import laya_router as lr

VAGUE = [
    "Do the thing with the file.",
    "Do the thing.",
    "Fix it.",
    "Handle that.",
    "Sort out the stuff.",
    "Take care of the thing.",
    "please deal with this",
    "Do it.",
    "Can you do the thing with the file?",
]


@pytest.mark.parametrize("text", VAGUE)
def test_a_vague_command_is_asked_back_with_named_options(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "ask", r
    q = r.question
    assert " or " in q and "," in q, q
    assert q.rstrip().endswith("?"), q
    assert r.tool in (None, ""), "nothing may be run or handed to the deeper mind"


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

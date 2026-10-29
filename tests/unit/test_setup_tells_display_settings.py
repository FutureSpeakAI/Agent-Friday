"""Setup tells the person how the screen follows their accessibility settings
(NS-8.4-2, the accessibility part; BD-21 AX20).

Before: setup asked "talk or type" (and offered "I use a screen reader") but
said nothing about motion or contrast, so a person who needs less movement or
stronger text had no way to learn that Friday follows the system settings.
Now, whichever way they answer, Friday says it once, in plain words, and what
she says is true of the page: reduced motion stops the decorative loops, and a
contrast theme strengthens dim text and the focus outline.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday.services import setup_chat as sc
from agent_friday.services import setup_chat_copy as copy
from agent_friday.services import setup_profile as profile
from tests.unit.test_first_run_conversation import home  # noqa: F401  (fixture, used by name)

ROOT = Path(__file__).resolve().parents[2]


def _texts():
    return [e["text"] for e in profile.load_transcript() if e["role"] == "friday"]


def _note():
    return getattr(copy, "DISPLAY_NOTE", None)


@pytest.mark.parametrize("choice", ["talk", "type", "screen_reader"])
def test_every_answer_to_talk_or_type_hears_how_the_screen_follows_the_system(home, choice):
    sc.begin("cloud_only", "")
    v = sc.answer("welcome", choice, "")
    note = _note()
    assert note, "setup says nothing about motion or contrast"
    assert _texts().count(note) == 1, "said once, after the talk-or-type answer"
    assert v["stage"] == "offer"


def test_skipping_talk_or_type_still_hears_it(home):
    sc.begin("cloud_only", "")
    sc.answer("welcome", "skip", "")
    assert _note() and _note() in _texts()


def test_the_note_names_where_the_settings_are_in_plain_words():
    note = _note() or ""
    assert "Visual effects" in note and "Contrast themes" in note
    assert "nothing to set here" in note
    assert len(note) < 420, "short enough to hear once"


def _css(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/head.html"])
def test_what_the_note_promises_is_what_the_page_does(rel):
    css = _css(rel)
    assert re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)", css), "reduced motion is followed"
    m = re.search(r"@media\s*\(prefers-contrast:\s*more\)\s*\{(.{0,600})", css, re.S)
    assert m and "--fr-dim" in m.group(1) and "outline-width" in m.group(1), "more contrast strengthens dim text and focus"
    assert re.search(r"@media\s*\(forced-colors:\s*active\)", css), "a contrast theme is followed"

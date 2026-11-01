"""Arrow keys belong to the focused control (north star NS-33.1-2).

The desktop's scene listened for ArrowLeft/ArrowRight on the whole document
and took them from every focused element except text fields, so a keyboard
user could not move through a radio group, a list or a menu. Radio groups now
follow the WAI-ARIA pattern (arrows move and pick, Home/End), and the scene
takes arrows only when no control that uses them has focus. The behaviour is
driven in a browser by tests/app/specs/accessibility_keyboard.spec.ts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "styles_and_scene.html"]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_the_scene_leaves_arrow_keys_to_controls_that_use_them(page):
    text = page.read_text(encoding="utf-8")
    i = text.index("window.fridayVibe.nextStructure(); e.preventDefault();")
    handler = text[text.rfind("document.addEventListener('keydown'", 0, i):i]
    assert "FRIDAY_ARROW_WIDGETS" in handler and "e.defaultPrevented" in handler, \
        "the scene's arrow handler still takes arrows from radios, lists and menus"
    widgets = re.search(r"var FRIDAY_ARROW_WIDGETS = (.*?);\n", text, flags=re.S).group(1)
    for role in ("radio", "radiogroup", "listbox", "option", "menu", "menuitem", "tab", "slider"):
        assert '[role="%s"]' % role in widgets, role


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_radio_groups_answer_the_arrow_keys(page):
    text = page.read_text(encoding="utf-8")
    m = re.search(r"var step = \{ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1, Home: 'first', End: 'last'\}"
                  r".*?\n\}\);", text, flags=re.S)
    assert m, "no radio-group arrow handler"
    body = m.group(0)
    assert "closest('[role=\"radiogroup\"]')" in body and ".focus()" in body and ".click()" in body
    # A group with its own arrow handling (it calls preventDefault) is left alone.
    assert "e.defaultPrevented" in body and "stopPropagation" not in body


def test_the_app_spec_drives_it():
    spec = (ROOT / "tests/app/specs/accessibility_keyboard.spec.ts").read_text(encoding="utf-8")
    assert "arrow keys move through a radio group" in spec

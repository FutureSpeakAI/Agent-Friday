"""A closed panel is out of the Tab order (WCAG 2.2 2.4.3 focus order, 2.4.7; north star NS-34.14-2).

The quick Settings panel slides off screen when closed (right: -360px) but stayed
in the page: Tab walked through its buttons while nothing was visible, and a screen
reader read them. Closed, it is now hidden (visibility), after its slide-out; open,
it is visible at once.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("rel", ["index.html", "ui_parts/head.html"])
def test_the_closed_settings_panel_is_hidden_after_it_slides_out(rel):
    css = (ROOT / rel).read_text(encoding="utf-8")
    rule = re.search(r"\.settings-panel \{([^}]*)\}", css).group(1)
    assert "visibility: hidden;" in rule
    assert "visibility 0s linear 0.35s" in rule, "hidden only once the slide-out has finished"
    assert ".settings-panel.open { right: 0; visibility: visible; transition-delay: 0s; }" in css


def test_a_dock_button_says_its_name_once():
    """The icon's alt text and the label beside it both said it: "Settings Settings"."""
    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "\"aria-label\": w.label + (noticeWs.has(w.id) && !openWins.has(w.id) ? ', something waiting' : '')," in idx
    app = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    assert "aria-label={w.label+((noticeWs.has(w.id)&&!openWins.has(w.id))?', something waiting':'')}" in app

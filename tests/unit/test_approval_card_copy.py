"""An approval card says what will happen, in words, once.

The card is where the owner decides whether Friday acts, so it shows the
card's description and never the internal kind and policy class as its text.
Both the served page and its hand-maintained mirror are checked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_cards_render_the_description_through_one_helper(page):
    html = page.read_text(encoding="utf-8")
    assert html.count("function approvalDetailLines(") == 1
    helper = html[html.index("function approvalDetailLines("):]
    helper = helper[:helper.index("\n}\n")]
    assert "a.description" in helper and "a.action_description" in helper
    # Both approval surfaces (the System window and a workspace's own tab).
    assert html.count("approvalDetailLines(a).map(") == 2


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_internal_identifiers_are_not_the_cards_text(page):
    html = page.read_text(encoding="utf-8")
    for old in ('}, a.kind, " \\xB7 ", a.policy_class',
                "{a.kind} · {a.policy_class}"):
        assert old not in html, old

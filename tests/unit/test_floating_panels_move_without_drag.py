"""Floating panels move without dragging (WCAG 2.2 AA 2.5.7; north star
NS-33.1-14). Follow-up to tests/unit/test_nothing_is_drag_only.py.

The Workspace Studio dialog, the process thread panel and the camera preview
could only be moved by dragging. Each now has a "Move to the next corner"
button; the Live page's camera preview moves to the next corner on a tap (or
Enter). One helper, fridayNextCorner, picks the corner: clockwise from the top
left of the desktop, so repeated presses visit all four.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


def _t(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_one_helper_picks_the_next_corner(page):
    s = _t(page)
    body = s[s.index("function fridayNextCorner(p, w, h)"):]
    body = body[:body.index("window.fridayNextCorner")]
    assert body.count("{ x: a.x") == 4, "four corners"
    assert "return corners[(at + 1) % corners.length];" in body


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_each_floating_panel_has_a_move_button(page):
    s = _t(page)
    for which in ("studio", "thread", "camera"):
        assert ("'data-panel-move':'%s'" % which in s or '"data-panel-move": "%s"' % which in s
                or 'data-panel-move="%s"' % which in s), which
    assert "setThreadPos(p=>fridayNextCorner(p,400,320))" in s
    assert "fridayNextCorner(p, 390" in s
    # The camera pair is placed by the screen view's left edge; the webcam sits 130 px to its left.
    assert "x: p.x - 130" in s or "x:p.x-130" in s


def test_the_live_page_preview_moves_on_a_tap():
    s = _t(ROOT / "static" / "live" / "friday_live.html")
    assert "camPip.addEventListener('click', () => { if (!moved) nextCorner(); moved = false; });" in s
    assert "camPip.setAttribute('role', 'button');" in s and "e.key === 'Enter'" in s

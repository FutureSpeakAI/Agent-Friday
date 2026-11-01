"""Nothing that can be dragged needs a drag (WCAG 2.2 AA 2.5.7; north star
NS-33.1-14; burn-down BD-21 "non-drag alternatives").

An audit of every drag in the UI found these with no single-click way to do
the same thing; each now has one:

- the Compose timeline's clips: Move earlier / Move later buttons (and the
  remove control is a real button);
- calendar events: move buttons (15 minutes earlier or later, a day earlier
  or later) beside Delete in the Day view, making the same proposal a drag
  makes, so the same confirmation follows
  (tests/app/specs/calendar_move.spec.ts drives one);
- the chat tray's height: "Where the chat goes" > Height (full or half);
- the divider between the chat and its side panel: a click steps through
  three widths and, focused, the arrow keys resize it (a separator);
- the composer no longer invites a drop it never accepted.

The window and chat moves and resizes already had snap layouts, the Home
board has Move earlier/later, the 3D views have keys and buttons.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]


def _t(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_timeline_clips_move_by_button(page):
    s = _t(page)
    assert "'Move clip ' + (i + 1) + ' earlier'" in s or "'Move clip '+(i+1)+' earlier'" in s
    assert "'Move clip ' + (i + 1) + ' later'" in s or "'Move clip '+(i+1)+' later'" in s
    assert "reorder(i, i - 1)" in s or "reorder(i,i-1)" in s
    assert "'Remove clip ' + (i + 1)" in s or "'Remove clip '+(i+1)" in s


def test_calendar_events_move_by_button_through_the_same_proposal():
    cv = _t(ROOT / "static" / "calendar_views.js")
    body = cv[cv.index("function MoveButtons(props)"):cv.index("function dragProps(")]
    assert "if (!canMove(ev)) return null;" in body
    assert body.count("propose(ev, days, minutes)") == 1
    for label in ("15 minutes earlier", "15 minutes later", "A day earlier", "A day later"):
        assert label in body
    assert "MoveButtons," in cv[cv.index("window.CalendarViews = {"):]
    for page in PAGES:
        assert "CalendarViews.MoveButtons" in _t(page), page.name


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_the_chat_tray_height_has_a_menu(page):
    s = _t(page)
    assert "\"snap-menu-title\" }, \"Height\")" in s
    assert "'Full height'" in s and "'Half height'" in s
    assert "window.dispatchEvent(new CustomEvent('friday:chat-height'" in s
    assert "window.addEventListener('friday:chat-height', on)" in s


def test_the_side_panel_divider_works_without_dragging():
    s = _t(ROOT / "static" / "friday_artifacts.js")
    i = s.index("className: 'fa-divider'")
    div = s[i - 1200:i + 1400]
    assert "role: 'separator'" in div and "tabIndex: 0" in div
    assert "ArrowLeft" in div and "ArrowRight" in div and "Home" in div and "End" in div
    assert "onClick: () => { if (dragStart.current && dragStart.current.moved) return; next(); }" in div


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_the_composer_does_not_invite_a_drop_it_never_takes(page):
    assert ". Type, speak, or drop a file." not in _t(page)

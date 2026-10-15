"""A drag proposes and the owner confirms; quick add shows before it writes. Read from the page sources.

The rendered behaviour (drag, confirm, cancel, a slipped drag, the preview and its Add) is proved in
tests/app/specs/calendar_move.spec.ts; these hold the page to the same contract without a browser.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "calendar_views.js").read_text(encoding="utf-8")


def _calendar_ws(name):
    src = (ROOT / name).read_text(encoding="utf-8")
    i = src.index("function CalendarWS(")
    return src[i:src.index("// ═══ MEETINGS", i)] if "// ═══ MEETINGS" in src[i:] else src[i:i + 40000]


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_day_view_events_can_be_dragged_and_one_move_layer_confirms(page):
    body = _calendar_ws(page)
    assert "CalendarViews.dragProps(e" in body
    assert "CalendarViews.MoveLayer" in body


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_quick_add_shows_a_preview_and_only_the_owners_add_writes(page):
    body = _calendar_ws(page)
    assert "CalendarViews.QuickAddPreview" in body
    assert "confirmed: true" in body or "confirmed:true" in body
    assert "created_in_google ? 'Added to Google Calendar' : 'Added (stored locally)'" not in body
    assert "created_in_google?'Added to Google Calendar':'Added (stored locally)'" not in body


def test_a_drag_only_proposes_and_the_write_happens_in_the_confirmation():
    drag = JS[JS.index("function dragProps("):JS.index("function MoveLayer(")]
    assert "fetch(" not in drag and "api(" not in drag, "a drag never writes by itself"
    assert "MOVE_EVENT" in drag
    layer = JS[JS.index("function MoveLayer("):JS.index("function QuickAddPreview(")]
    assert "confirmed: true" in layer
    assert "moveGrantUntil" in layer and "30 minutes" in layer, "the batch permission is the owner's, named, and runs out"
    assert re.search(r"if \(Date\.now\(\) < moveGrantUntil\) run\(p\); else", layer)


def test_a_slipped_drag_proposes_nothing_and_ctrl_click_still_ticks():
    drag = JS[JS.index("function dragProps("):JS.index("function MoveLayer(")]
    assert "Math.abs(dy) < 6 && Math.abs(dx) < 6" in drag
    assert "e.ctrlKey || e.metaKey" in drag
    assert "if (!days && !minutes) return;" in JS
    assert "const STEP_MIN = 15;" in JS


def test_only_a_timed_google_event_the_owner_may_edit_can_be_dragged():
    assert "function canMove(ev) { return !!ev && !ev.media && !ev.all_day && ev.can_edit === true && ev.source === 'google'; }" in JS

"""Deleting an event is on the event, styled as destructive, and asks with the event named first.

Only the page: the delete itself is POST /api/calendar/events/<id>/delete (calendar_write.delete_event,
from the field bug-log work). The click path is tests/app/specs/calendar_delete.spec.ts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "calendar_views.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_day_view_has_delete_on_the_event_and_one_layer_that_asks(page):
    src = (ROOT / page).read_text(encoding="utf-8")
    body = src[src.index("function CalendarWS("):]
    body = body[:body.index("// ═══ MEETINGS")] if "// ═══ MEETINGS" in body else body[:40000]
    assert "CalendarViews.DeleteButton" in body and "CalendarViews.DeleteLayer" in body
    t0 = body.index('className: "calv-toolbar"') if 'className: "calv-toolbar"' in body else body.index('className="calv-toolbar"')
    toolbar = body[t0:body.index("FreeBusyView", t0)]
    assert "Delete" not in toolbar, "a destructive action is never in the toolbar (HIG 4.3)"


def test_the_button_only_proposes_and_the_confirmation_names_the_event_and_sends_it_exactly():
    button = JS[JS.index("function DeleteButton("):JS.index("function DeleteLayer(")]
    assert "api(" not in button and "fetch(" not in button
    assert "ev.can_edit !== true" in button, "no delete on an event she cannot change"
    layer = JS[JS.index("function DeleteLayer("):]
    layer = layer[:layer.index("// ── styles")]
    assert ("'Delete \u201c' + pending.ev.title + '\u201d?'" in layer
            or "'Delete \\u201c' + pending.ev.title + '\\u201d?'" in layer), "the question names the event"
    assert "confirmed: true" in layer and "calendar_id: ev.calendar_id" in layer
    assert "role: 'alertdialog'" in layer and "calv-danger" in layer
    assert ".calv-delete{" in JS and "var(--fr-error)" in JS[JS.index(".calv-delete{"):][:200]

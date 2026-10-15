"""The Calendar page offers Day, Week and Month, and every view shows Friday its events.

Reads the served page (index.html), its mirror (ui_parts/app.html) and the views
file (static/calendar_views.js); it starts no browser. The rendered views are
proved in tests/app/specs/calendar_views.spec.ts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VIEWS = ROOT / "static" / "calendar_views.js"


def _src(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _calendar_ws(src):
    i = src.index("function CalendarWS(")
    return src[i:src.index("// ═══ MEETINGS", i)] if "// ═══ MEETINGS" in src[i:] else src[i:i + 40000]


@pytest.mark.parametrize("page", ["index.html", "ui_parts/styles_and_scene.html"])
def test_the_page_loads_the_views(page):
    assert '<script src="/static/calendar_views.js"></script>' in _src(page)


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_calendar_offers_week_and_month_beside_day(page):
    src = _src(page)
    body = _calendar_ws(src)
    assert "CalendarViews.ViewSwitch" in body
    assert "CalendarViews.WeekView" in body and "CalendarViews.MonthView" in body
    decl = src[src.index("const CAL_NAV_SECTIONS"):]
    decl = decl[:decl.index("];")]
    for view in ("day", "week", "month", "freebusy"):
        assert re.search(r"id:\s*'%s'" % view, decl), "Friday can open the %s view by name" % view


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_day_view_draws_each_event_in_its_google_colour(page):
    body = _calendar_ws(_src(page))
    assert body.count("'--ev': e.color") + body.count("'--ev':e.color") >= 2, "timed and all-day events"


def test_every_view_publishes_its_events_to_the_stage_and_reads_the_range_route():
    js = VIEWS.read_text(encoding="utf-8")
    assert "window.fridayRow('event'" in js
    assert "/api/calendar/range?start=" in js
    for name in ("WeekView", "MonthView", "ViewSwitch"):
        assert re.search(r"function %s\(" % name, js), name


def test_a_failed_read_is_said_not_drawn_as_an_empty_week():
    js = VIEWS.read_text(encoding="utf-8")
    assert js.count("Couldn't read your calendar.") >= 2
    assert "Try again" in js


def test_the_views_add_no_colour_of_their_own():
    """Event colours are the owner's (from Google); everything else is a brand token."""
    css = VIEWS.read_text(encoding="utf-8").split("const CSS = `", 1)[1].split("`;", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"rgba?\(", css)


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_a_day_whose_read_failed_says_so_instead_of_no_timed_events(page):
    body = _calendar_ws(_src(page))
    assert "read_failed" in body and "Couldn't read your calendar." in body
    assert body.index("read_failed") < body.index("No timed events.")


def test_a_late_night_event_runs_to_the_bottom_of_the_grid_not_one_hour():
    js = VIEWS.read_text(encoding="utf-8")
    assert "function endY(ev, top)" in js and "dayKeyOf(ev.end_time) !== dayKeyOf(ev.start_time)" in js


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_events_at_the_same_time_sit_side_by_side_in_the_day_view(page):
    body = _calendar_ws(_src(page))
    assert "CalendarViews.lanes(timedAll" in body and "dayLanes[e.id]" in body
    assert ".cal-timeline>.cal-event,.cal-timeline>.cal-gap{left:68px}" in VIEWS.read_text(encoding="utf-8"), \
        "Day-view events start after the hour labels instead of covering them"

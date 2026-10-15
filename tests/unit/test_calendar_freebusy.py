"""Free and busy, worked out on this PC from the calendars the owner has switched on.

calendar_views.free_busy merges every event that holds its time in Google into busy
blocks that name their calendars and never their titles, leaves out what the owner
declined or marked as free, and counts the free time left inside working hours on
working days (the scheduling settings Friday uses to offer meeting times). Nothing
asks Google: no free/busy query leaves this PC.
Synthetic events only.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MON = datetime(2026, 10, 19).astimezone()        # a Monday
WORK = {"working_hours": {"start": "09:00", "end": "17:00"}, "working_days": [0, 1, 2, 3, 4]}


def _ev(day, h1, m1, h2, m2, cal, title="Secret title", **extra):
    s = (MON + timedelta(days=day)).replace(hour=h1, minute=m1)
    e = (MON + timedelta(days=day)).replace(hour=h2, minute=m2)
    out = {"title": title, "start_time": s.isoformat(), "end_time": e.isoformat(), "calendar_name": cal}
    out.update(extra)
    return out


@pytest.fixture
def fb():
    from agent_friday.services import calendar_views as cv
    return lambda evs: cv.free_busy(evs, MON, MON + timedelta(days=7), working=WORK)


def test_overlapping_events_merge_into_one_busy_block_that_names_calendars_not_titles(fb):
    out = fb([_ev(0, 9, 0, 10, 0, "Home"), _ev(0, 9, 30, 11, 0, "Family")])
    mon = out["days"][0]
    assert [(b["start"][11:16], b["end"][11:16], b["calendars"]) for b in mon["busy"]] == [("09:00", "11:00", ["Home", "Family"])]
    assert "Secret title" not in repr(out)


def test_free_time_is_what_is_left_inside_working_hours_on_working_days(fb):
    out = fb([_ev(0, 9, 0, 11, 0, "Home"), _ev(0, 12, 0, 12, 10, "Home"), _ev(5, 10, 0, 12, 0, "Home")])
    mon = out["days"][0]
    # 11:00-12:00 and 12:10-17:00; a gap under 15 minutes would not count
    assert [(f["start"][11:16], f["end"][11:16]) for f in mon["free"]] == [("11:00", "12:00"), ("12:10", "17:00")]
    assert mon["free_minutes"] == 60 + 290
    sat = out["days"][5]
    assert sat["working"] is False and sat["free"] == [] and len(sat["busy"]) == 1
    assert out["free_minutes"] == 350 + 4 * 480
    assert out["working"] == {"start": "09:00", "end": "17:00", "days": [0, 1, 2, 3, 4]}


def test_declined_and_show_as_free_events_do_not_take_time(fb):
    out = fb([_ev(1, 9, 0, 17, 0, "Home", self_rsvp="declined"), _ev(1, 9, 0, 17, 0, "Home", busy=False)])
    tue = out["days"][1]
    assert tue["busy"] == [] and tue["free_minutes"] == 480


def test_no_free_busy_query_leaves_this_pc():
    src = (ROOT / "src/agent_friday/services/calendar_views.py").read_text(encoding="utf-8")
    calls = {n.func.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "freebusy" not in calls and "query" not in calls


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_calendar_has_free_busy_the_calendars_panel_and_fridays_only_chip(page):
    src = (ROOT / page).read_text(encoding="utf-8")
    body = src[src.index("function CalendarWS("):]
    body = body[:body.index("// ═══ MEETINGS")] if "// ═══ MEETINGS" in body else body[:40000]
    for name in ("CalendarViews.FreeBusyView", "CalendarViews.CalendarsPanel", "CalendarViews.OnlyChip", "setOnlySpec("):
        assert name in body, name
    assert len(re.findall(r"keys[:=]\s*\{?onlyKeys", body)) == 3, "Free/busy, Week and Month read only the chip's calendars"
    decl = src[src.index("fridayDeclareNav('calendar'"):]
    assert "'only'" in decl[:decl.index(";")]
    nav = src[src.index("const CAL_NAV_SECTIONS"):]
    nav = nav[:nav.index("];")]
    assert "id: 'freebusy'" in nav or "id:'freebusy'" in nav
    assert "id: 'all'" in nav or "id:'all'" in nav


def test_switching_a_calendar_is_a_tick_that_says_it_changes_friday_only():
    js = (ROOT / "static/calendar_views.js").read_text(encoding="utf-8")
    panel = js[js.index("function CalendarsPanel("):js.index("function OnlyChip(")]
    assert "/api/calendar/calendars/visible" in panel and "never Google" in panel
    chip = js[js.index("function OnlyChip("):js.index("function FreeBusyView(")]
    assert "by ' + (window.fridayName" in chip and "aria-label': 'Show every calendar again'" in chip

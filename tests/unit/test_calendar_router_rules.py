"""The Calendar's voice rules open its views and narrow it to a calendar, and never take over a calendar READ.

"just show me today's calendar" or "just show me what's on my calendar" are questions about the day
(query_calendar), not a calendar called "today's"; the "only this calendar" rule must leave them alone.
"""
from __future__ import annotations

import pytest

from agent_friday.services import laya_router as lr


@pytest.mark.parametrize("text", [
    "just show me today's calendar", "only show me tomorrow's calendar",
    "just show me what's on my calendar", "just show me the meetings on my work calendar",
    "show only the calendar", "show me my week",
])
def test_a_calendar_read_is_not_taken_for_a_calendar_name(text):
    r = lr._tier0(text)
    assert r is None or r.tool != "navigate_to" or r.args.get("section") is None, (text, r and r.args)


@pytest.mark.parametrize("text,section", [
    ("show only my family calendar", "family"), ("just show me the work calendar", "work"),
    ("show all my calendars", "all"), ("switch to the free/busy view", "freebusy"),
    ("show my calendar by month", "month"),
])
def test_the_views_and_calendars_by_name_open_the_calendar_on_them(text, section):
    r = lr._tier0(text)
    assert r is not None and r.tool == "navigate_to"
    assert r.args == {"kind": "workspace", "workspace": "calendar", "query": "calendar", "section": section}

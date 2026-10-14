"""Real date fields declare a date format, so services/call_verifier checks them.

The verifier treats a string property as a date only when its schema says
`format: date` or `date-time` (owner-approved Option A). A tool whose field
takes an ISO 8601 / RFC 3339 value but carries no format would be skipped by
that check, and a malformed date would reach its handler.
"""
from __future__ import annotations

import re

import pytest

from agent_friday.services import agent, call_verifier

#: (tool, field, a malformed value, a good value)
DATE_FIELDS = [
    ("update_calendar_event", "start", "next tuesday-ish", "2026-08-18T11:30:00-05:00"),
    ("update_calendar_event", "end", "soon", "2026-08-18T12:30:00-05:00"),
    ("find_free_slots", "window_start", "after lunch", "2026-08-18T09:00:00-05:00"),
    ("find_free_slots", "window_end", "whenever", "2026-08-21"),
    ("create_task", "due", "friday afternoon", "2026-09-01T00:00:00Z"),
    ("update_task", "due", "tomorrow", "2026-09-01T00:00:00Z"),
    ("revert_workspace", "when", "this morning", "2026-08-17T08:00:00"),
]


def _all_tools():
    yield from agent.CLAUDE_TOOLS
    for extra in agent.WORKSPACE_TOOLS.values():
        yield from extra


@pytest.mark.parametrize("tool,field,bad,good", DATE_FIELDS)
def test_a_real_date_field_declares_its_format(tool, field, bad, good):
    schema = agent._schema_for_tool(tool)
    spec = schema["properties"][field]
    assert spec["type"] == "string" and spec["format"] in ("date", "date-time")


@pytest.mark.parametrize("tool,field,bad,good", DATE_FIELDS)
def test_a_malformed_date_is_still_refused_and_a_good_one_passes(tool, field, bad, good):
    schema = agent._schema_for_tool(tool)
    fixes = call_verifier.check_sanity(tool, {field: bad}, schema)
    assert any(field in f and "ISO 8601" in f for f in fixes), fixes
    assert call_verifier.check_sanity(tool, {field: good}, schema) == []


def test_no_tool_describes_an_iso_date_without_declaring_the_format():
    said_iso = re.compile(r"ISO[ -]?8601|ISO (?:timestamp|datetime|date)|RFC ?3339", re.I)
    missing = []
    for tool in _all_tools():
        props = (tool.get("input_schema") or {}).get("properties") or {}
        for key, spec in props.items():
            if (isinstance(spec, dict) and spec.get("type") == "string"
                    and said_iso.search(str(spec.get("description") or ""))
                    and spec.get("format") not in ("date", "date-time")):
                missing.append("%s.%s" % (tool.get("name"), key))
    assert missing == []


def test_fields_that_take_a_phrase_are_not_made_dates():
    """Adding a format there would refuse what the tool accepts today."""
    for tool, field in (("content_schedule_post", "publish_at"),
                        ("content_create_post", "publish_at")):
        spec = agent._schema_for_tool(tool)["properties"][field]
        assert "format" not in spec

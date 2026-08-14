"""A6 — authoritative clock (Incident 2, F3).

The F3 red-test anchor: 2026-08-14 is a FRIDAY. The live incident labeled it
"Thursday" and doubled down — because no clock existed in context and the
model did its own weekday arithmetic.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_friday.services.clock import (
    annotate_weekdays,
    clock_context_block,
    weekday_of,
)


class TestWeekdayOf:
    def test_f3_anchor_2026_08_14_is_friday(self):
        assert weekday_of(2026, 8, 14) == "Friday"

    def test_incident_day_2026_08_13_is_thursday(self):
        assert weekday_of(2026, 8, 13) == "Thursday"

    def test_invalid_date_returns_none(self):
        assert weekday_of(2026, 13, 99) is None


class TestClockBlock:
    def test_block_has_datetime_weekday_timezone_and_rule(self):
        now = datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc)
        block = clock_context_block(now)
        assert "== AUTHORITATIVE CLOCK ==" in block
        assert "2026-08-14 09:30" in block
        assert "(Friday)" in block
        assert "UTC" in block
        assert "NEVER derive a weekday" in block
        # Challenge-handling rule (the doubling-down half of F3).
        assert "re-read this clock" in block


class TestAnnotateWeekdays:
    def test_iso_date_annotated(self):
        assert annotate_weekdays("Next event: 2026-08-14 at 3pm") == \
            "Next event: 2026-08-14 (Friday) at 3pm"

    def test_us_date_with_year_annotated(self):
        assert "(Friday)" in annotate_weekdays("Due 8/14/2026.")

    def test_bare_us_date_left_alone_too_ambiguous(self):
        assert annotate_weekdays("Use 3/4 cup of flour") == "Use 3/4 cup of flour"

    def test_already_annotated_not_doubled(self):
        text = "2026-08-14 (Friday) at 3pm"
        assert annotate_weekdays(text) == text

    def test_idempotent(self):
        once = annotate_weekdays("Meeting 2026-08-14.")
        assert annotate_weekdays(once) == once

    def test_invalid_iso_date_untouched(self):
        assert annotate_weekdays("id 2026-99-99 raw") == "id 2026-99-99 raw"

    def test_multiple_dates(self):
        out = annotate_weekdays("From 2026-08-13 to 2026-08-14.")
        assert "2026-08-13 (Thursday)" in out
        assert "2026-08-14 (Friday)" in out

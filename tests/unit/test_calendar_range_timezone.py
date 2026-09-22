"""The Calendar panel's Google read must send RFC 3339 times with an offset.

Google's events.list rejects a timeMin/timeMax without a UTC offset with
HTTP 400 Bad Request. _events_for_day builds naive local-midnight datetimes,
and the read sent them as-is, so every Calendar panel read failed, and the
failure was reported as an empty calendar.
"""
import re
from datetime import datetime, timedelta

import pytest

from agent_friday.services import calendar_engine as ce

OFFSET = re.compile(r"(Z|[+-]\d\d:\d\d)$")


class _FakeService:
    def __init__(self, sent):
        self.sent = sent

    def events(self):
        return self

    def list(self, **kw):
        self.sent.update(kw)
        return self

    def execute(self):
        return {"items": []}


@pytest.fixture
def sent(monkeypatch):
    captured = {}
    import googleapiclient.discovery as discovery
    monkeypatch.setattr(ce, "_google_credentials", lambda: object())
    monkeypatch.setattr(discovery, "build", lambda *a, **k: _FakeService(captured))
    return captured


def test_naive_day_bounds_are_sent_with_a_utc_offset(sent):
    start = datetime(2026, 9, 22)
    ce._fetch_calendar_range_live(start, start + timedelta(days=1))
    assert OFFSET.search(sent["timeMin"]), sent["timeMin"]
    assert OFFSET.search(sent["timeMax"]), sent["timeMax"]


def test_naive_bounds_mean_local_time(sent):
    start = datetime(2026, 9, 22)
    ce._fetch_calendar_range_live(start, start + timedelta(days=1))
    assert datetime.fromisoformat(sent["timeMin"]) == start.astimezone()


def test_aware_bounds_are_sent_unchanged(sent):
    start = datetime(2026, 9, 22).astimezone()
    ce._fetch_calendar_range_live(start, start + timedelta(days=1))
    assert sent["timeMin"] == start.isoformat()

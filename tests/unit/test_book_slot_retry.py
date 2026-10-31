"""Booking a hold twice (a retry after a lost reply, or the model asking again) is one booking, and
says so (NS-33.6-2: zero duplicate outward actions from retry).

Before: the repeat found the hold already booked and answered "not an active Friday hold; nothing
was changed" (or "no single active hold starts at ..."), although the booking and its invitations
had gone out; told nothing happened, the model could make the meeting again by other means. Now the
repeat returns the booked event as already booked, and changes and sends nothing. Fakes from
tests/unit/test_scheduling.py; synthetic data.
"""
from __future__ import annotations

from agent_friday.services import scheduling as sch
from tests.unit.test_scheduling import SLOTS, world  # noqa: F401 (the fixture)


def _patches(cal):
    return [c for c in cal.calls if c[0] == "patch"]


def test_booking_the_same_hold_twice_books_it_once_and_says_so(world):  # noqa: F811
    held = sch.hold_slots(title="Call", slots=SLOTS, account_id="w1")
    series, hid = held["series_id"], held["holds"][1]["id"]
    cal = world["cals"]["w1"]
    first = sch.book_slot(series_id=series, hold_event_id=hid, title="Call with Dana",
                          attendees=["dana@example.com"], account_id="w1")
    assert first["ok"], first
    n = len(_patches(cal))
    again = sch.book_slot(series_id=series, hold_event_id=hid, title="Call with Dana",
                          attendees=["dana@example.com"], account_id="w1")
    assert again.get("ok") is True and again.get("already_booked"), again
    assert again["event"]["id"] == hid
    assert len(_patches(cal)) == n, "the repeat changed (and re-sent) the booking"


def test_booking_by_start_time_twice_books_it_once(world):  # noqa: F811
    held = sch.hold_slots(title="Call", slots=SLOTS, account_id="w1")
    series = held["series_id"]
    cal = world["cals"]["w1"]
    first = sch.book_slot(series_id=series, start="2026-10-07T14:00:00Z", title="Call", account_id="w1")
    assert first["ok"], first
    n = len(_patches(cal))
    again = sch.book_slot(series_id=series, start="2026-10-07T14:00:00Z", title="Call", account_id="w1")
    assert again.get("ok") is True and again.get("already_booked"), again
    assert len(_patches(cal)) == n

"""A same-day plan with several timed stops gets its gaps checked against the drive.

check_plan_travel estimates the drive between consecutive stops from their
coordinates (straight line times a road factor at an urban average speed,
labelled a rough estimate) and flags a gap shorter than drive plus buffer.
Stops without coordinates are reported as not checked, never guessed. Several
calendar events created in one turn get the same check, appended to the
create result.

Fixture geography is synthetic: a downtown at (40.00, -90.00), a suburb about
20 miles north, a zoo on the far southwest side.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate

DOWNTOWN_LIBRARY = {"name": "Central Library", "lat": 40.000, "lon": -90.000}
NORTH_SUBURB = {"name": "Harvest fair", "lat": 40.290, "lon": -90.000}
SOUTHWEST_ZOO = {"name": "City Zoo", "lat": 39.800, "lon": -90.300}
CAFE_NEAR_LIBRARY = {"name": "Corner cafe", "lat": 40.004, "lon": -90.003}


def _stop(base, start, end=None):
    s = dict(base, start=start)
    if end:
        s["end"] = end
    return s


def _tool(stops):
    """The tool as the agent loop runs it: through the governed dispatcher."""
    out = agent._execute_tool("check_plan_travel", {"stops": stops})
    assert str(out).lstrip().startswith("{"), "no travel check ran: " + str(out)[:200]
    return json.loads(out)


def _reset_turns():
    try:
        from agent_friday.services import plan_travel
        plan_travel._reset()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _fresh():
    _reset_turns()
    yield
    _reset_turns()


def test_far_apart_stops_with_a_75_minute_gap_are_flagged():
    out = _tool([
        _stop(DOWNTOWN_LIBRARY, "2026-10-17T09:30:00", "2026-10-17T10:30:00"),
        _stop(NORTH_SUBURB, "2026-10-17T11:45:00", "2026-10-17T12:45:00"),
        _stop(SOUTHWEST_ZOO, "2026-10-17T14:00:00", "2026-10-17T16:00:00"),
    ])
    zoo = next(p for p in out["pairs"] if p["to"] == "City Zoo")
    assert zoo["gap_min"] == 75
    assert zoo["drive_min"] >= 60, zoo
    assert zoo["status"] == "tight"
    assert out["flagged"] >= 1
    assert "City Zoo" in out["summary"] and "rough estimate" in out["summary"]


def test_close_stops_are_not_flagged():
    out = _tool([
        _stop(DOWNTOWN_LIBRARY, "10:00", "11:00"),
        _stop(CAFE_NEAR_LIBRARY, "11:20", "12:00"),
    ])
    assert out["flagged"] == 0 and out["unchecked"] == 0
    assert out["pairs"][0]["status"] == "ok"


def test_stops_without_coordinates_are_reported_as_not_checked():
    out = _tool([
        {"name": "Riverside Park", "start": "10:00", "end": "11:00", "location": "Riverside Park"},
        {"name": "Museum of Trains", "start": "11:30", "location": "Museum of Trains"},
    ])
    assert out["flagged"] == 0 and out["unchecked"] == 1
    assert "couldn't check travel time between Riverside Park and Museum of Trains" in out["summary"]
    assert "drive_min" not in out["pairs"][0]


def test_coordinates_written_into_a_location_are_used():
    out = _tool([
        {"name": "Central Library", "start": "2026-10-17T10:00:00-05:00", "end": "2026-10-17T11:00:00-05:00",
         "location": "Central Library, 40.000, -90.000"},
        {"name": "City Zoo", "start": "2026-10-17T11:30:00-05:00",
         "location": "https://maps.example.test/place/@39.800,-90.300,15z"},
    ])
    assert out["pairs"][0]["status"] == "tight", out


def test_the_tool_is_internal_and_read_only():
    assert action_gate.classify("check_plan_travel", {"stops": []})[0] == action_gate.INTERNAL
    assert agent.TOOL_RINGS.get("check_plan_travel") == 0


# ── the post-check on events created in one turn ─────────────────────────────

def _create(monkeypatch, created, **inp):
    from agent_friday.services import calendar_write as cw

    def fake(**kw):
        created.append(kw)
        return {"ok": True, "id": "ev%d" % len(created), "title": kw["title"],
                "start": kw["start"], "end": kw["end"] or kw["start"]}
    monkeypatch.setattr(cw, "create_event", fake)
    return agent._tool_create_calendar_event(inp)


def test_creating_far_apart_events_in_one_turn_warns_about_the_gap(monkeypatch):
    from agent_friday.services import turn_plan
    tok = turn_plan._current.set("turn-plan-a")
    created = []
    try:
        r1 = _create(monkeypatch, created, title="Harvest fair", start="2026-10-17T11:45:00-05:00",
                     end="2026-10-17T12:45:00-05:00", location="Harvest fair grounds @40.290,-90.000")
        r2 = _create(monkeypatch, created, title="City Zoo", start="2026-10-17T14:00:00-05:00",
                     end="2026-10-17T16:00:00-05:00", location="City Zoo @39.800,-90.300")
    finally:
        turn_plan._current.reset(tok)
    assert "TRAVEL CHECK" not in r1
    assert "TRAVEL CHECK" in r2 and "too short" in r2 and "Harvest fair to City Zoo" in r2


def test_events_without_coordinates_say_the_travel_was_not_checked(monkeypatch):
    from agent_friday.services import turn_plan
    tok = turn_plan._current.set("turn-plan-b")
    created = []
    try:
        _create(monkeypatch, created, title="Story time", start="2026-10-17T10:00:00",
                end="2026-10-17T11:00:00", location="Riverside Library")
        r2 = _create(monkeypatch, created, title="Pumpkin patch", start="2026-10-17T11:30:00",
                     end="2026-10-17T13:00:00", location="Hillside Farm")
    finally:
        turn_plan._current.reset(tok)
    assert "couldn't check travel time between Story time and Pumpkin patch" in r2


def test_close_events_in_one_turn_carry_no_warning(monkeypatch):
    from agent_friday.services import turn_plan
    tok = turn_plan._current.set("turn-plan-c")
    created = []
    try:
        _create(monkeypatch, created, title="Story time", start="2026-10-17T10:00:00",
                end="2026-10-17T11:00:00", location="Central Library 40.000, -90.000")
        r2 = _create(monkeypatch, created, title="Lunch", start="2026-10-17T11:20:00",
                     end="2026-10-17T12:00:00", location="Corner cafe 40.004, -90.003")
    finally:
        turn_plan._current.reset(tok)
    assert "TRAVEL CHECK" not in r2

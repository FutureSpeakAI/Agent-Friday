"""organize_calendar: "move these two" is ONE card listing each event's old and new time; nothing moves before the
owner's yes; the events are exactly the ones ticked (or pointed at); Undo puts each one back.

Google is a fake here: calendar_write.get_event and update_event are replaced, so the test proves what Friday
asks Google to do, not Google.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import agent, approvals as ap, calendar_write as cw, desktop_bus, item_actions as ia
from tests.screen_fixtures import report

EVENTS = {
    "e1": {"title": "Dentist", "start": "2026-10-06T14:00:00-05:00", "end": "2026-10-06T15:00:00-05:00", "tz": "America/Chicago", "guests": 0},
    "e2": {"title": "Lunch with Dana", "start": "2026-10-06T12:00:00-05:00", "end": "2026-10-06T13:00:00-05:00", "tz": "America/Chicago", "guests": 2},
    "e3": {"title": "Standup", "start": "2026-10-07T09:00:00-05:00", "end": "2026-10-07T09:15:00-05:00", "tz": "America/Chicago", "guests": 0},
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    from agent_friday.services import dissent_gate as dg
    desktop_bus.reset()
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    journal.reset()
    state = {k: dict(v) for k, v in EVENTS.items()}
    calls = []

    def get_event(eid, account_id=None):
        if eid not in state:
            return {"error": "could not read event %s" % eid}
        e = state[eid]
        return {"ok": True, "id": eid, "title": e["title"], "start": e["start"], "end": e["end"], "tz": e["tz"],
                "all_day": e.get("all_day", False), "guests": e["guests"], "account_id": "acct1"}

    def update_event(eid, start=None, end=None, account_id=None, **kw):
        calls.append((eid, start, end))
        state[eid]["start"], state[eid]["end"] = start, end
        return {"ok": True, "id": eid}

    monkeypatch.setattr(cw, "get_event", get_event)
    monkeypatch.setattr(cw, "update_event", update_event)
    yield state, calls
    journal.reset()
    desktop_bus.reset()


def _org(**kw):
    return agent._tool_organize_calendar(dict({"action": "shift"}, **kw))


def _pending():
    return [r for r in ap.list_approvals(kind="governed_action") if (r.get("payload") or {}).get("handler") == ia.HANDLER]


def test_moving_two_events_is_one_card_and_nothing_moves_before_the_yes(world):
    state, calls = world
    out = _org(events=["event:e1", "event:e2"], days=2)
    assert calls == [] and state["e1"]["start"] == EVENTS["e1"]["start"]
    cards = _pending()
    assert len(cards) == 1 and cards[0]["status"] == "pending"
    assert "move 2 events 2 days later" in cards[0]["title"].lower() or "2 days later" in cards[0]["title"]
    assert "Dentist" in cards[0]["description"] and "has guests; they are not notified" in cards[0]["description"]
    assert "Shall I" in out or "card" in out.lower()


def test_one_event_still_waits_for_a_card_because_it_reaches_google(world):
    _org(events=["e3"], minutes=30)
    assert len(_pending()) == 1


def test_the_yes_moves_each_event_by_the_same_amount_keeping_its_length(world):
    state, calls = world
    _org(events=["e1", "e3"], days=1, minutes=-30)
    ap.decide_with_outcome(_pending()[0]["approval_id"], "approve", decided_by="owner")
    assert state["e1"]["start"].startswith("2026-10-07T13:30:00") and state["e1"]["end"].startswith("2026-10-07T14:30:00")
    assert state["e3"]["start"].startswith("2026-10-08T08:30:00") and state["e3"]["end"].startswith("2026-10-08T08:45:00")
    assert {c[0] for c in calls} == {"e1", "e3"}


def test_a_day_forward_across_a_clock_change_keeps_the_wall_clock_time():
    # US clocks go back on 2026-11-01: 14:00 Saturday stays 14:00 Sunday
    moved = ia._shifted("2026-10-31T14:00:00-05:00", "America/Chicago", 1, 0)
    assert moved.startswith("2026-11-01T14:00:00") and moved.endswith("-06:00"), moved


def test_undo_puts_each_event_back_at_its_old_time(world):
    state, calls = world
    _org(events=["e1", "e2"], days=3)
    ap.decide_with_outcome(_pending()[0]["approval_id"], "approve", decided_by="owner")
    assert state["e1"]["start"] != EVENTS["e1"]["start"]
    rec = journal.latest(None)
    res = ia.undo(rec["receipt_id"])
    assert res["status"] == "complete"
    assert state["e1"]["start"] == EVENTS["e1"]["start"] and state["e2"]["end"] == EVENTS["e2"]["end"]


def test_the_ticked_events_are_exactly_the_ones_the_card_is_bound_to(world):
    state, calls = world
    stage = {"workspace": "calendar", "rev": 4, "loaded": 3, "filters": [],
             "items": [{"ref": "event:e%d" % i, "n": i, "facets": {"kind": "normal", "when": "today"}, "title": EVENTS["e%d" % i]["title"]} for i in (1, 2, 3)],
             "selection": {"id": "sel1", "refs": ["event:e1", "event:e2"], "count": 2, "source": "owner"}}
    report(stage)
    out = _org(selection="screen", days=1)
    cards = _pending()
    assert len(cards) == 1 and cards[0]["payload"]["refs"] == ["event:e1", "event:e2"]
    assert "Standup" not in cards[0]["description"], "a tick added later, or an unticked event, is not in it"
    assert calls == []


def test_it_refuses_what_it_cannot_do_before_raising_a_card(world):
    state, _ = world
    assert "say how far" in _org(events=["e1"])
    assert "no events" in _org(events=[], days=1).lower() or "name the events" in _org(events=[], days=1)
    state["e1"]["all_day"] = True
    state["e1"]["start"] = ""
    assert "all-day" in _org(events=["e1"], days=1)
    assert "could not read" in _org(events=["nope"], days=1)
    assert "further than a move can go" in _org(events=["e3"], days=400)
    assert _pending() == []


def test_the_gate_classifies_it_outward_and_the_tool_is_registered_everywhere():
    assert ag.classify("organize_calendar", {"events": ["e1"], "days": 1})[0] == ag.OUTWARD
    from agent_friday.services import taint, voice_engine
    assert "organize_calendar" in agent.CLAUDE_TOOL_HANDLERS and agent.TOOL_RINGS["organize_calendar"] == 1
    assert "organize_calendar" in taint.TOOL_ROLES
    assert "organize_calendar" in voice_engine._VOICE_SHARED_TOOLS
    assert "organize_calendar" in {t["name"] for t in agent.CLAUDE_TOOLS}

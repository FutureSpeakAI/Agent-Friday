"""Friday reminds the owner of an event when Google would, once, and points at the prep for what matters.

Google holds a reminder list for each event (its own, or its calendar's defaults);
services/calendar_views reads it with the events. calendar_reminders.tick, a
scheduler builtin every five minutes, raises a tray notice for each pop-up reminder
that has come due: never twice, never for an event that has started or that the
owner declined, never invented for an event Google has no reminder for, and never
by running a model. An event with other people in it says its prep card is one
click away, and the notice opens the event with its prep.
Synthetic events only.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 20, 8, 31).astimezone()


def _ev(eid, title, start, reminders, **extra):
    out = {"id": eid, "title": title, "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat(),
           "all_day": False, "reminders": reminders, "calendar_key": "home|primary", "calendar_name": "Home",
           "attendees": [], "type": "normal", "self_rsvp": ""}
    out.update(extra)
    return out


POP30 = [{"method": "popup", "minutes": 30}]
EVENTS = [
    _ev("e1", "Dentist", NOW + timedelta(minutes=29), POP30),                                  # due
    _ev("e2", "Lunch with Dana", NOW + timedelta(minutes=10), [{"method": "popup", "minutes": 15}],
        attendees=["dana@example.com"]),                                                        # due, matters
    _ev("e3", "Gym", NOW + timedelta(minutes=45), POP30),                                       # not yet
    _ev("e4", "Book club", NOW + timedelta(minutes=5), [{"method": "email", "minutes": 30}]),    # Google emails it
    _ev("e5", "Declined call", NOW + timedelta(minutes=5), POP30, self_rsvp="declined"),
    _ev("e6", "Already started", NOW - timedelta(minutes=5), POP30),
    _ev("e7", "No reminder in Google", NOW + timedelta(minutes=5), []),
    {"id": "e8", "title": "Holiday", "start_time": NOW.date().isoformat(), "end_time": "", "all_day": True,
     "reminders": POP30},
]


def test_only_popup_reminders_that_have_come_due_for_events_not_started_or_declined():
    from agent_friday.services import calendar_reminders as cr
    assert sorted((e["id"], m) for e, m in cr.due(EVENTS, NOW)) == [("e1", 30), ("e2", 15)]


@pytest.fixture
def ticking(monkeypatch, tmp_path):
    from agent_friday.services import calendar_engine as ce
    from agent_friday.services import calendar_reminders as cr
    pushed = []
    monkeypatch.setattr(cr, "FIRED_FILE", tmp_path / "fired.json")
    monkeypatch.setattr(cr, "CALENDAR_DIR", tmp_path)
    monkeypatch.setattr(cr, "_push", lambda **kw: pushed.append(kw))
    monkeypatch.setattr(ce, "_events_for_day", lambda d: [e for e in EVENTS if e["start_time"][:10] == d.isoformat()])
    return cr, pushed


def test_a_due_reminder_is_raised_once_through_the_registry_and_opens_the_event(ticking):
    cr, pushed = ticking
    out = cr.tick(NOW)
    assert out["raised"] == 2 and out["errors"] == 0
    by = {p["target"]["event"]: p for p in pushed}
    assert by["e1"]["title"] == "Dentist in 29 minutes", "worded from the time actually left"
    assert by["e1"]["emitter"] == "CR1" and by["e1"]["kind"] == "calendar_reminder"
    assert by["e1"]["dedupe_key"] == "calendar-reminder:e1:30"
    assert by["e1"]["target"] == {"workspace": "calendar", "event": "e1", "date": "2026-10-20"}
    assert cr.tick(NOW + timedelta(minutes=1))["raised"] == 0, "a reminder fires once"
    assert len(pushed) == 2


def test_an_event_that_matters_points_at_its_prep_card(ticking):
    cr, pushed = ticking
    cr.tick(NOW)
    lunch = [p for p in pushed if p["target"]["event"] == "e2"][0]
    assert lunch["target"]["prep"] == "1"
    assert "prep card" in lunch["body"]
    dentist = [p for p in pushed if p["target"]["event"] == "e1"][0]
    assert "prep" not in dentist["target"] and "prep card" not in dentist["body"]


def test_no_model_runs_and_nothing_is_written_to_google():
    src = (ROOT / "src/agent_friday/services/calendar_reminders.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for forbidden in ("_generate_text", "insert", "patch", "update_event", "create_event", "visible_job"):
        assert forbidden not in names, forbidden


def test_the_reminder_notice_is_a_registered_emitter_and_the_scheduler_runs_it():
    from agent_friday.services import notice_registry as reg
    em = reg.get("CR1")
    assert em is not None and em.place == reg.UPDATES and em.source == "calendar" and not em.safety_critical
    sched = (ROOT / "src/agent_friday/services/scheduler.py").read_text(encoding="utf-8")
    assert re.search(r'register_builtin_task\("calendar_reminders", _cal_remind,', sched)
    assert '"every_minutes": 2' in sched[sched.index('"calendar_reminders"'):][:400]
    from agent_friday.services import calendar_reminders as cr
    assert cr.TICK_MINUTES == 2


@pytest.mark.parametrize("page", ["index.html", "ui_parts/app.html"])
def test_the_day_view_says_each_events_reminder_and_a_reminder_can_open_the_prep(page):
    src = (ROOT / page).read_text(encoding="utf-8")
    body = src[src.index("function CalendarWS("):]
    body = body[:body.index("// ═══ MEETINGS")] if "// ═══ MEETINGS" in body else body[:40000]
    assert "CalendarViews.reminderText(e)" in body
    assert "setPrepWanted(String(t.event))" in body
    decl = src[src.index("fridayDeclareNav('calendar'"):]
    assert "'prep'" in decl[:decl.index(";")]


def test_reminder_words_follow_googles_minutes():
    js = (ROOT / "static/calendar_views.js").read_text(encoding="utf-8")
    assert "function reminderText(ev)" in js and "(r.method || 'popup') === 'popup'" in js


def test_an_at_the_start_reminder_is_raised_by_a_tick_just_after_the_start_and_not_later():
    from agent_friday.services import calendar_reminders as cr
    at0 = [{"method": "popup", "minutes": 0}]
    just_started = _ev("s1", "Standup", NOW - timedelta(minutes=1), at0)
    long_started = _ev("s2", "Old standup", NOW - timedelta(minutes=10), at0)
    not_yet = _ev("s3", "Later standup", NOW + timedelta(minutes=1), at0)
    assert [e["id"] for e, _ in cr.due([just_started, long_started, not_yet], NOW)] == ["s1"]


def test_a_five_minute_reminder_cannot_fall_between_two_ticks():
    from agent_friday.services import calendar_reminders as cr
    start = NOW + timedelta(minutes=5)
    ev = _ev("t1", "Call", start, [{"method": "popup", "minutes": 5}])
    hits = [m for m in range(0, 7) if cr.due([ev], start - timedelta(minutes=5) + timedelta(minutes=m))]
    # every tick from the reminder's moment until the start sees it due; ticks are 2 minutes apart
    assert hits == [0, 1, 2, 3, 4]


def test_reminders_that_came_due_together_raise_one_notice_worded_from_the_time_left(ticking, monkeypatch):
    cr, pushed = ticking
    from agent_friday.services import calendar_engine as ce
    soon = _ev("m1", "Standup", NOW + timedelta(minutes=8),
               [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 60}, {"method": "popup", "minutes": 10}])
    monkeypatch.setattr(ce, "_events_for_day", lambda d: [soon] if d == NOW.date() else [])
    assert cr.tick(NOW)["raised"] == 1
    assert pushed[0]["title"] == "Standup in 8 minutes"
    assert cr.tick(NOW + timedelta(minutes=1))["raised"] == 0, "the day-before and hour-before ones are not raised late"

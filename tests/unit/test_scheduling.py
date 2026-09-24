"""Offering meeting times: free/busy across accounts, holds, booking.

A fake Google Calendar client stands in for every account; nothing here
reaches the network.
"""
from __future__ import annotations

import copy
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import calendar_write as cw
from agent_friday.services import google_accounts as ga
from agent_friday.services import scheduling as sch

CHI = ZoneInfo("America/Chicago")
W = cw.FULL_CALENDAR_SCOPE


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ── fake Google Calendar ────────────────────────────────────────────────────

class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeCalendar:
    """One account's primary calendar: freebusy plus events CRUD."""

    def __init__(self, busy=None, fb_errors=None):
        self.busy = busy or []
        self.fb_errors = fb_errors
        self.fb_bodies = []
        self.events_by_id = {}
        self.calls = []
        self.list_ignores_filter = False
        self._n = 0

    # freebusy
    def freebusy(self):
        return self

    def query(self, body):
        self.fb_bodies.append(body)
        cal = {"errors": self.fb_errors} if self.fb_errors else {"busy": self.busy}
        return _Exec(lambda: {"calendars": {"primary": cal}})

    # events
    def events(self):
        return self

    def insert(self, calendarId, body, **kw):
        self.calls.append(("insert", calendarId, kw))
        self._n += 1
        ev = dict(copy.deepcopy(body), id="ev%d" % self._n)
        self.events_by_id[ev["id"]] = ev
        return _Exec(lambda: copy.deepcopy(ev))

    def get(self, calendarId, eventId):
        return _Exec(lambda: copy.deepcopy(self.events_by_id[eventId]))

    def patch(self, calendarId, eventId, body, **kw):
        self.calls.append(("patch", eventId, kw))
        ev = self.events_by_id[eventId]
        for k, v in copy.deepcopy(body).items():
            if k == "extendedProperties":
                priv = ev.setdefault("extendedProperties", {}).setdefault("private", {})
                priv.update(v.get("private") or {})
            else:
                ev[k] = v
        return _Exec(lambda: copy.deepcopy(ev))

    def delete(self, calendarId, eventId, **kw):
        self.calls.append(("delete", eventId, kw))
        return _Exec(lambda: self.events_by_id.pop(eventId) and None)

    def list(self, **kw):
        key, _, val = (kw.get("privateExtendedProperty") or "").partition("=")
        items = [copy.deepcopy(e) for e in self.events_by_id.values()
                 if self.list_ignores_filter or
                 ((e.get("extendedProperties") or {}).get("private") or {}).get(key) == val]
        return _Exec(lambda: {"items": items})


def _acct(aid, label, healthy=True):
    return {"id": aid, "label": label, "email": "%s@example.com" % aid,
            "status": "connected" if healthy else "needs_reauth",
            "scopes": [W], "services": {"calendar": True},
            "health": {"healthy": healthy}}


@pytest.fixture
def world(monkeypatch):
    """Two connected accounts, each with its own fake calendar."""
    cals = {"p1": FakeCalendar(), "w1": FakeCalendar()}
    accts = [_acct("p1", "Personal"), _acct("w1", "Work")]
    monkeypatch.setattr(ga, "list_accounts", lambda: accts)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: "creds:" + aid)
    monkeypatch.setattr(sch, "_calendar_client", lambda creds: cals[creds.split(":")[1]])
    monkeypatch.setattr(cw, "write_ready", lambda: (True, None))
    monkeypatch.setattr(cw, "_service", lambda aid=None: (cals[aid], None) if aid in cals
                        else (None, "no such account"))
    monkeypatch.setattr(sch, "scheduling_settings", lambda: {
        "timezone": "America/Chicago",
        "working_hours": {"start": "09:00", "end": "17:00"},
        "working_days": [0, 1, 2, 3, 4], "min_notice_hours": 0, "buffer_minutes": 0})
    return {"cals": cals, "accts": accts}


# ── settings ────────────────────────────────────────────────────────────────

def test_settings_block_is_declared_and_read_over_its_defaults(monkeypatch):
    from agent_friday import core
    assert "scheduling" in core.DEFAULT_SETTINGS
    monkeypatch.setattr(core, "_load_settings", lambda: {"scheduling": {
        "timezone": "Europe/London", "working_hours": {"start": "08:00"}}})
    cfg = sch.scheduling_settings()
    assert cfg["timezone"] == "Europe/London"
    assert cfg["working_hours"] == {"start": "08:00", "end": "17:00"}
    assert cfg["buffer_minutes"] == core.DEFAULT_SETTINGS["scheduling"]["buffer_minutes"]
    with pytest.raises(sch.SchedulingError):
        sch.zone_for("Not/AZone")


# ── free/busy merge ─────────────────────────────────────────────────────────

def test_merge_joins_overlapping_and_touching_blocks():
    a = [(utc(2026, 10, 5, 14), utc(2026, 10, 5, 15)),
         (utc(2026, 10, 5, 14, 30), utc(2026, 10, 5, 16)),
         (utc(2026, 10, 5, 16), utc(2026, 10, 5, 17)),
         (utc(2026, 10, 5, 19), utc(2026, 10, 5, 20))]
    assert sch.merge_intervals(a) == [(utc(2026, 10, 5, 14), utc(2026, 10, 5, 17)),
                                      (utc(2026, 10, 5, 19), utc(2026, 10, 5, 20))]


def test_busy_is_merged_across_every_account(world):
    world["cals"]["p1"].busy = [{"start": "2026-10-05T14:00:00Z", "end": "2026-10-05T15:00:00Z"}]
    # Work reports in its own offset; the overlap with Personal is merged.
    world["cals"]["w1"].busy = [{"start": "2026-10-05T09:30:00-05:00", "end": "2026-10-05T11:00:00-05:00"}]
    fb = sch.busy_across_accounts(utc(2026, 10, 5), utc(2026, 10, 6), CHI)
    assert fb["checked"] == ["Personal", "Work"] and not fb["errors"]
    assert fb["busy"] == [(utc(2026, 10, 5, 14), utc(2026, 10, 5, 16))]
    assert world["cals"]["p1"].fb_bodies[0]["items"] == [{"id": "primary"}]


def test_an_all_day_block_covers_the_whole_local_day(world):
    world["cals"]["w1"].busy = [{"start": "2026-10-06", "end": "2026-10-07"}]
    fb = sch.busy_across_accounts(utc(2026, 10, 5), utc(2026, 10, 9), CHI)
    assert fb["busy"] == [(datetime(2026, 10, 6, tzinfo=CHI), datetime(2026, 10, 7, tzinfo=CHI))]
    out = sch.find_free_slots(duration_minutes=60, window_start="2026-10-05",
                              window_end="2026-10-07", count=3,
                              now=utc(2026, 10, 1))
    days = {s["start"][:10] for s in out["slots"]}
    assert "2026-10-06" not in days and days == {"2026-10-05", "2026-10-07"}


def test_an_unreadable_calendar_is_reported_never_treated_as_free(world):
    world["accts"][1]["health"] = {"healthy": False}
    world["cals"]["p1"].fb_errors = None
    out = sch.find_free_slots(window_start="2026-10-05", window_end="2026-10-05",
                              now=utc(2026, 10, 1))
    assert out["ok"] and out["complete"] is False
    assert any("Work" in e for e in out["unreadable"]) and "warning" in out

    world["cals"]["p1"].fb_errors = [{"reason": "notFound"}]
    out = sch.find_free_slots(window_start="2026-10-05", window_end="2026-10-05",
                              now=utc(2026, 10, 1))
    assert "error" in out and not out.get("slots")


# ── slot generation ─────────────────────────────────────────────────────────

def _gen(busy, **kw):
    args = dict(window_start=datetime(2026, 10, 5, tzinfo=CHI),
                window_end=datetime(2026, 10, 10, tzinfo=CHI),
                earliest=datetime(2026, 10, 1, tzinfo=CHI), duration_minutes=60,
                count=3, buffer_minutes=0, working_start=dtime(9),
                working_end=dtime(17), working_days=[0, 1, 2, 3, 4], zone=CHI)
    args.update(kw)
    return [(s.astimezone(CHI), e.astimezone(CHI)) for s, e in sch.generate_slots(busy, **args)]


def test_slots_spread_across_days_inside_working_hours():
    slots = _gen([])
    assert [s.date().isoformat() for s, _ in slots] == ["2026-10-05", "2026-10-06", "2026-10-07"]
    assert all(s.hour == 9 and (e - s).seconds == 3600 for s, e in slots)


def test_busy_buffer_and_notice_are_respected():
    busy = [(datetime(2026, 10, 5, 9, tzinfo=CHI), datetime(2026, 10, 5, 10, tzinfo=CHI))]
    s, _ = _gen(busy, buffer_minutes=15, count=1)[0]
    assert s == datetime(2026, 10, 5, 10, 30, tzinfo=CHI)
    s, _ = _gen([], count=1, earliest=datetime(2026, 10, 5, 15, 10, tzinfo=CHI))[0]
    assert s == datetime(2026, 10, 5, 15, 30, tzinfo=CHI)
    # 16:30 would end after working hours, so the next is the next morning.
    s, _ = _gen([], count=1, earliest=datetime(2026, 10, 5, 16, 1, tzinfo=CHI))[0]
    assert s == datetime(2026, 10, 6, 9, tzinfo=CHI)


def test_weekends_are_skipped_and_one_day_can_hold_several():
    slots = _gen([], window_start=datetime(2026, 10, 10, tzinfo=CHI),   # Saturday
                 window_end=datetime(2026, 10, 12, tzinfo=CHI))          # Monday 00:00
    assert slots == []
    slots = _gen([], window_start=datetime(2026, 10, 12, tzinfo=CHI),
                 window_end=datetime(2026, 10, 12, 23, tzinfo=CHI))
    assert [s.hour for s, _ in slots] == [9, 10, 11]


def test_working_hours_follow_the_wall_clock_across_a_dst_change():
    # America/Chicago leaves daylight time on Sunday 2026-11-01.
    busy = [(utc(2026, 10, 30, 14), utc(2026, 10, 30, 15)),    # 09:00 CDT
            (utc(2026, 11, 2, 15), utc(2026, 11, 2, 16))]      # 09:00 CST
    slots = sch.generate_slots(
        busy, window_start=datetime(2026, 10, 30, tzinfo=CHI),
        window_end=datetime(2026, 11, 3, tzinfo=CHI), earliest=utc(2026, 10, 1),
        duration_minutes=60, count=2, buffer_minutes=0, working_start=dtime(9),
        working_end=dtime(17), working_days=[0, 1, 2, 3, 4], zone=CHI)
    assert [s.astimezone(CHI).isoformat() for s, _ in slots] == [
        "2026-10-30T10:00:00-05:00", "2026-11-02T10:00:00-06:00"]


def test_find_free_slots_reports_wall_clock_labels(world):
    out = sch.find_free_slots(duration_minutes=30, window_start="2026-11-02",
                              window_end="2026-11-02", count=1, now=utc(2026, 10, 1))
    assert out["slots"][0]["start"] == "2026-11-02T09:00:00-06:00"
    assert "09:00-09:30 CST" in out["slots"][0]["label"]
    assert out["timezone"] == "America/Chicago"


def test_bad_requests_are_refused_in_words(world):
    assert "error" in sch.find_free_slots(duration_minutes=1000)
    assert "error" in sch.find_free_slots(window_start="2026-10-05", window_end="2026-10-01")
    assert "error" in sch.find_free_slots(window_start="soon")


# ── holds ───────────────────────────────────────────────────────────────────

SLOTS = [{"start": "2026-10-05T09:00:00-05:00", "end": "2026-10-05T09:30:00-05:00"},
         {"start": "2026-10-06T09:00:00-05:00", "end": "2026-10-06T09:30:00-05:00"},
         {"start": "2026-10-07T09:00:00-05:00", "end": "2026-10-07T09:30:00-05:00"}]


def test_holds_need_a_named_account_when_two_can_write(world):
    out = sch.hold_slots(title="Call with Dana", slots=SLOTS)
    assert "Personal" in out["error"] and "Work" in out["error"]
    assert not world["cals"]["p1"].events_by_id and not world["cals"]["w1"].events_by_id


def test_holds_go_on_the_named_own_calendar_marked_and_inviting_nobody(world):
    out = sch.hold_slots(title="Call with Dana", slots=SLOTS, account_id="Work")
    assert out["ok"] and out["account_id"] == "w1" and len(out["holds"]) == 3
    cal = world["cals"]["w1"]
    assert not world["cals"]["p1"].events_by_id
    for kind, cal_id, kw in cal.calls:
        assert (kind, cal_id, kw) == ("insert", "primary", {"sendUpdates": "none"})
    for ev in cal.events_by_id.values():
        assert ev["summary"] == "Hold: Call with Dana"
        assert "attendees" not in ev
        assert ev["status"] == "tentative" and ev["visibility"] == "private"
        assert ev["extendedProperties"]["private"] == {
            sch.HOLD_MARK: sch.HOLD_ACTIVE, sch.HOLD_SERIES: out["series_id"]}


def test_hold_requests_are_validated_before_anything_is_written(world):
    for bad in ([], [{"start": "2026-10-05T09:00:00-05:00"}],
                [{"start": "2026-10-05T10:00:00-05:00", "end": "2026-10-05T09:00:00-05:00"}],
                SLOTS * 4):
        out = sch.hold_slots(title="x", slots=bad, account_id="Work")
        assert "error" in out, bad
    assert not world["cals"]["w1"].events_by_id


def _plant_foreign(cal, series):
    """Events a release must never delete, planted in the same calendar."""
    cal.events_by_id.update({
        "mine": {"id": "mine", "summary": "Dentist"},
        "other": {"id": "other", "summary": "Hold: other",
                  "extendedProperties": {"private": {sch.HOLD_MARK: "1",
                                                     sch.HOLD_SERIES: "hsother"}}},
        "invited": {"id": "invited", "summary": "Hold: shared",
                    "attendees": [{"email": "x@example.com"}],
                    "extendedProperties": {"private": {sch.HOLD_MARK: "1",
                                                       sch.HOLD_SERIES: series}}},
        "titled": {"id": "titled", "summary": "Hold: Call with Dana"},
    })


def test_booking_invites_and_releases_only_marked_holds(world):
    held = sch.hold_slots(title="Call with Dana", slots=SLOTS, account_id="Work")
    series, ids = held["series_id"], [h["id"] for h in held["holds"]]
    cal = world["cals"]["w1"]
    _plant_foreign(cal, series)
    # Even if the server-side filter returned everything, the re-check holds.
    cal.list_ignores_filter = True
    out = sch.book_slot(series_id=series, hold_event_id=ids[1], title="Call with Dana",
                        attendees=["dana@example.com"], account_id="Work")
    assert out["ok"], out
    booked = cal.events_by_id[ids[1]]
    assert booked["summary"] == "Call with Dana" and booked["status"] == "confirmed"
    assert booked["attendees"] == [{"email": "dana@example.com"}]
    assert booked["extendedProperties"]["private"][sch.HOLD_MARK] == sch.HOLD_BOOKED
    assert ("patch", ids[1], {"sendUpdates": "all"}) in cal.calls
    deleted = [c[1] for c in cal.calls if c[0] == "delete"]
    assert sorted(deleted) == sorted([ids[0], ids[2]])
    assert all(c[2] == {"sendUpdates": "none"} for c in cal.calls if c[0] == "delete")
    assert {"mine", "other", "invited", "titled", ids[1]} <= set(cal.events_by_id)

    # A later release of the same series cannot touch the booked meeting.
    rel = sch.release_holds(series_id=series, account_id="Work")
    assert rel["ok"] and rel["released"] == [] and ids[1] in cal.events_by_id


def test_booking_by_start_time_and_refusing_a_non_hold(world):
    held = sch.hold_slots(title="Call", slots=SLOTS, account_id="w1")
    series = held["series_id"]
    cal = world["cals"]["w1"]
    _plant_foreign(cal, series)
    out = sch.book_slot(series_id=series, start="2026-10-07T14:00:00Z", title="Call",
                        account_id="w1")
    assert out["ok"] and out["event"]["id"] == held["holds"][2]["id"]
    assert ("patch", held["holds"][2]["id"], {"sendUpdates": "none"}) in cal.calls

    before = copy.deepcopy(cal.events_by_id)
    for eid in ("mine", "other", "invited", "titled"):
        out = sch.book_slot(series_id=series, hold_event_id=eid, title="x",
                            attendees=["a@example.com"], account_id="w1")
        assert "not an active Friday hold" in out["error"], eid
    assert cal.events_by_id == before
    assert "error" in sch.book_slot(series_id=series, hold_event_id=held["holds"][0]["id"],
                                    title="x", attendees=["not an address"], account_id="w1")


def test_release_removes_only_this_series_marked_holds(world):
    held = sch.hold_slots(title="Call", slots=SLOTS[:2], account_id="w1")
    cal = world["cals"]["w1"]
    _plant_foreign(cal, held["series_id"])
    cal.list_ignores_filter = True
    out = sch.release_holds(series_id=held["series_id"], account_id="w1")
    assert sorted(r["id"] for r in out["released"]) == sorted(h["id"] for h in held["holds"])
    assert set(cal.events_by_id) == {"mine", "other", "invited", "titled"}
    assert "error" in sch.release_holds(series_id="../../x", account_id="w1")


# ── governance ──────────────────────────────────────────────────────────────

def test_each_new_tool_has_its_decided_class():
    assert action_gate.classify("find_free_slots", {})[0] == action_gate.INTERNAL
    assert action_gate.classify("release_holds", {})[0] == action_gate.INTERNAL
    assert action_gate.classify("hold_slots", {})[0] == action_gate.OUTWARD
    assert action_gate.classify("book_slot", {})[0] == action_gate.OUTWARD
    for n in ("hold_slots", "book_slot"):
        assert n not in action_gate.SELF_GATED


def test_the_tools_are_registered_and_outward_ones_wait_off_chat(monkeypatch, tmp_path):
    import agent_friday.services.agent as agent
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    names = {t["name"] for t in agent.CLAUDE_TOOLS}
    for n in ("find_free_slots", "hold_slots", "book_slot", "release_holds"):
        assert n in names and n in agent.CLAUDE_TOOL_HANDLERS and n in agent.TOOL_RINGS
    bg = {"authenticated": True, "is_background_task": True, "task_id": "t-sched"}
    assert action_gate.authorize("hold_slots", {"title": "x"}, bg).action == "card"
    assert action_gate.authorize("book_slot", {"title": "x"}, bg).action == "card"
    assert action_gate.authorize("find_free_slots", {}, bg).action == "allow"
    chat = {"authenticated": True, "session_id": "s1"}
    assert action_gate.authorize("hold_slots", {"title": "x"}, chat).action == "confirm"

"""A News routine that finds the local seat parked or down waits, then catches up.

The morning routines are local-only. A run that cannot reach the local seat
(parked for build hours, stopped, or not answering) is neither a failure nor a
silent skip: it is recorded as WAITING with the reason, and the scheduler runs it
again as soon as the seat is back the same day. A day that ends with the run
still waiting is recorded as MISSED, with the reason. A Front Page whose
editorial failed while the seat was serving is DEGRADED, not "complete". A
persisted schedule whose builtin no longer exists is retired once, visibly,
instead of failing every day.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from agent_friday.services import build_hours
from agent_friday.services import scheduler as s

SEAT = "bonsai2:27b"


@pytest.fixture(autouse=True)
def _clean_store(friday_dir, monkeypatch):
    for p in (s.SCHEDULES_FILE, s.RUNS_FILE):
        if p.exists():
            p.unlink()
    s._RUNNING.clear()
    monkeypatch.setattr(s, "_cloud_model_for", lambda rec: None)
    yield
    for _ in range(200):           # let a dispatched run finish writing
        if not s._RUNNING:
            break
        time.sleep(0.05)


class _Seat:
    """The local seat's state as the scheduler sees it."""

    def __init__(self, monkeypatch, serving=False, parked=False):
        self.serving, self.parked = serving, parked
        monkeypatch.setattr(s, "_resolve_local_seat",
                            lambda: SEAT if (self.serving and not self.parked) else None)
        monkeypatch.setattr(build_hours, "is_active", lambda *a, **k: self.parked)
        monkeypatch.setattr(build_hours, "describe",
                            lambda *a, **k: "build hours are active until 07:00")


def _news_rec(ref, sid="sch_news_morning"):
    return s.register_schedule({
        "id": sid, "name": "Morning news briefing", "trigger": "daily",
        "spec": {"hour": 0}, "retry": {"max": 0, "backoff_seconds": 1},
        "task": {"kind": "builtin", "ref": ref, "local_only": True},
    })


def _wait_for_run(sid, n):
    for _ in range(100):
        hist = s.run_history(sid, limit=50)
        if len(hist) >= n:
            return hist
        time.sleep(0.05)
    return s.run_history(sid, limit=50)


def _register(monkeypatch, ref, fn):
    monkeypatch.setitem(s.BUILTIN_TASKS, ref, {"fn": fn, "label": "Front Page"})


def test_a_parked_seat_makes_the_run_wait_with_the_reason(monkeypatch):
    seat = _Seat(monkeypatch, serving=False, parked=True)
    calls = []
    _register(monkeypatch, "t_fp", lambda: calls.append(1) or {"ok": True})
    rec = _news_rec("t_fp")
    s.dispatch(rec, manual=True)
    hist = _wait_for_run("sch_news_morning", 1)
    assert hist[0]["status"] == "waiting", hist[0]
    assert "build hours" in hist[0]["summary"]
    assert calls == [], "the routine ran while the seat was parked"
    live = s.get_schedule("sch_news_morning")
    assert live["last_status"] == "waiting"
    assert (live.get("catch_up") or {}).get("date") == s._now_central().strftime("%Y-%m-%d")
    assert seat.parked


def test_the_catch_up_runs_as_soon_as_the_seat_is_back(monkeypatch):
    seat = _Seat(monkeypatch, serving=False, parked=True)
    calls = []
    _register(monkeypatch, "t_fp2", lambda: calls.append(1) or {"ok": True})
    rec = _news_rec("t_fp2")
    s.dispatch(rec, manual=True)
    _wait_for_run("sch_news_morning", 1)

    later = s._now_central() + timedelta(minutes=5)
    live = s.get_schedule("sch_news_morning")
    assert not s._is_due(live, later), "a catch-up fired while the seat is still parked"

    seat.parked, seat.serving = False, True
    assert s._is_due(live, later), "the seat is back but the waiting run is not due"
    s.dispatch(live)
    hist = _wait_for_run("sch_news_morning", 2)
    assert hist[0]["status"] == "complete", hist[0]
    assert "late" in hist[0]["summary"].lower()
    assert calls == [1]
    assert not s.get_schedule("sch_news_morning").get("catch_up")


def test_a_seat_that_never_returns_is_recorded_missed(monkeypatch):
    _Seat(monkeypatch, serving=False, parked=True)
    _register(monkeypatch, "t_fp3", lambda: {"ok": True})
    rec = _news_rec("t_fp3")
    yesterday = (s._now_central() - timedelta(days=1)).strftime("%Y-%m-%d")
    s.update_schedule("sch_news_morning", {})
    s._patch_record("sch_news_morning", catch_up={"date": yesterday,
                                                   "reason": "parked for build hours",
                                                   "since": time.time() - 86400},
                    last_status="waiting", last_run_date=yesterday)
    s._tick()
    hist = _wait_for_run("sch_news_morning", 1)
    missed = [h for h in hist if h["status"] == "missed"]
    assert missed, hist
    assert "parked for build hours" in missed[0]["summary"]
    # Yesterday's mark is gone; today's run started and, with the seat still
    # parked, is waiting under a fresh mark of its own.
    today = s._now_central().strftime("%Y-%m-%d")
    for _ in range(100):
        cu = s.get_schedule("sch_news_morning").get("catch_up") or {}
        if cu.get("date") == today:
            break
        time.sleep(0.05)
    assert cu.get("date") == today, cu
    assert cu.get("since", 0) > time.time() - 600
    assert rec


def test_a_failed_provider_call_with_the_seat_down_waits(monkeypatch):
    _Seat(monkeypatch, serving=False, parked=False)

    def no_provider():
        raise RuntimeError("No model provider could generate text (tried local: refused)")

    _register(monkeypatch, "t_fp4", no_provider)
    s.dispatch(_news_rec("t_fp4"), manual=True)
    hist = _wait_for_run("sch_news_morning", 1)
    assert hist[0]["status"] == "waiting", hist[0]


def test_a_front_page_without_its_editorial_is_failed_not_complete(monkeypatch):
    _Seat(monkeypatch, serving=True, parked=False)
    _register(monkeypatch, "t_fp5", lambda: {
        "id": "2026-10-02-morning",
        "editorial_status": {"state": "degraded", "reason": "the editorial call failed"}})
    s.dispatch(_news_rec("t_fp5"), manual=True)
    hist = _wait_for_run("sch_news_morning", 1)
    assert hist[0]["status"] == "failed", hist[0]
    assert "no editorial" in hist[0]["summary"]
    assert "editorial call failed" in hist[0]["summary"]
    live = s.get_schedule("sch_news_morning")
    assert live["last_status"] == "failed"
    assert "editorial call failed" in live["last_summary"]


def test_a_degraded_front_page_with_the_seat_down_waits(monkeypatch):
    _Seat(monkeypatch, serving=False, parked=False)
    _register(monkeypatch, "t_fp6", lambda: {
        "id": "2026-10-02-morning",
        "editorial_status": {"state": "degraded", "reason": "the editorial call failed"}})
    s.dispatch(_news_rec("t_fp6"), manual=True)
    hist = _wait_for_run("sch_news_morning", 1)
    assert hist[0]["status"] == "waiting", hist[0]


def test_a_schedule_whose_builtin_is_gone_is_retired_once(monkeypatch):
    rec = s.register_schedule({
        "id": "sch_gone", "name": "The Friday Edition", "trigger": "daily",
        "spec": {"hour": 0}, "task": {"kind": "builtin", "ref": "no_such_builtin"},
    })
    s.dispatch(rec, manual=True)
    hist = _wait_for_run("sch_gone", 1)
    assert hist[0]["status"] == "retired", hist[0]
    assert "no_such_builtin" in hist[0]["summary"]
    live = s.get_schedule("sch_gone")
    assert live["enabled"] is False
    assert not s._is_due(live, datetime.now() + timedelta(days=1))
    # Recorded once: later ticks never run it, so it never fails again.
    s._tick()
    time.sleep(0.3)
    assert [h["status"] for h in s.run_history("sch_gone", limit=50)] == ["retired"]

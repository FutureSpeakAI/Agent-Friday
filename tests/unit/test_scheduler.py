"""Unit tests for Friday's internal scheduler (Part A)."""
import time
from datetime import datetime

import pytest

from agent_friday.services import scheduler as s


@pytest.fixture(autouse=True)
def _clean_store(friday_dir):
    # Each test starts from an empty registry + run history (temp home).
    if s.SCHEDULES_FILE.exists():
        s.SCHEDULES_FILE.unlink()
    if s.RUNS_FILE.exists():
        s.RUNS_FILE.unlink()
    s._RUNNING.clear()
    yield


# ── Trigger math ─────────────────────────────────────────────────────────────
def test_interval_due():
    rec = {"enabled": True, "trigger": "interval", "spec": {"every_minutes": 60},
           "last_run_ts": time.time() - 3600 - 5}
    assert s._is_due(rec, datetime.now())
    rec["last_run_ts"] = time.time() - 100      # not yet 60 min
    assert not s._is_due(rec, datetime.now())


def test_daily_due_after_time_once_per_day():
    rec = {"id": "d1", "enabled": True, "trigger": "daily",
           "spec": {"hour": 8, "minute": 0}}
    morning = datetime(2026, 6, 25, 9, 0)       # past 08:00
    assert s._is_due(rec, morning)
    before = datetime(2026, 6, 25, 7, 0)        # before 08:00
    assert not s._is_due(rec, before)
    # Already ran today → not due again.
    rec["last_run_date"] = "2026-06-25"
    assert not s._is_due(rec, morning)


def test_weekly_due_only_on_weekday():
    rec = {"enabled": True, "trigger": "weekly",
           "spec": {"weekday": 6, "hour": 9, "minute": 0}}   # Sunday
    sunday = datetime(2026, 6, 28, 10, 0)       # 2026-06-28 is a Sunday
    assert sunday.weekday() == 6
    assert s._is_due(rec, sunday)
    monday = datetime(2026, 6, 29, 10, 0)
    assert not s._is_due(rec, monday)


def test_disabled_never_due():
    rec = {"enabled": False, "trigger": "interval", "spec": {"every_minutes": 1},
           "last_run_ts": 0}
    assert not s._is_due(rec, datetime.now())


def test_retry_pending_fires_on_backoff():
    rec = {"enabled": True, "trigger": "daily", "spec": {"hour": 8, "minute": 0},
           "last_run_date": datetime.now().strftime("%Y-%m-%d"),
           "retry_pending": True, "not_before": time.time() - 10}
    # Even though it already ran today, a due retry fires.
    assert s._is_due(rec, datetime.now())
    rec["not_before"] = time.time() + 1000      # backoff not elapsed
    assert not s._is_due(rec, datetime.now())


# ── CRUD ─────────────────────────────────────────────────────────────────────
def test_register_update_delete():
    rec = s.register_schedule({
        "name": "Test interval", "trigger": "interval",
        "spec": {"every_minutes": 30},
        "task": {"kind": "agent_prompt", "prompt": "do a thing"},
    })
    assert rec["id"].startswith("sch_")
    assert rec["source"] == "user"
    got = s.get_schedule(rec["id"])
    assert got["name"] == "Test interval"

    s.update_schedule(rec["id"], {"enabled": False, "name": "Renamed"})
    got = s.get_schedule(rec["id"])
    assert got["enabled"] is False and got["name"] == "Renamed"

    assert s.delete_schedule(rec["id"]) is True
    assert s.get_schedule(rec["id"]) is None


def test_builtin_cannot_be_deleted():
    rec = s._normalize_record({
        "id": "sch_builtinx", "name": "Builtin", "trigger": "daily",
        "spec": {"hour": 6}, "task": {"kind": "builtin", "ref": "x"},
    }, source="builtin")
    s._upsert(rec)
    assert s.delete_schedule("sch_builtinx") is False


# ── Dispatch + run history ───────────────────────────────────────────────────
def test_dispatch_builtin_records_history():
    flag = {"ran": False}

    def _ping():
        flag["ran"] = True
        return {"changed": True, "summary": "pinged"}

    s.register_builtin_task("t_ping", _ping, label="Ping",
                            default_trigger="daily", default_spec={"hour": 0})
    rec = s.register_schedule({
        "id": "sch_t_ping", "name": "Ping", "trigger": "daily",
        "spec": {"hour": 0}, "task": {"kind": "builtin", "ref": "t_ping"},
    })
    run_id = s.dispatch(rec, manual=True)
    assert run_id

    # Wait for the daemon body thread to finish.
    for _ in range(50):
        hist = s.run_history("sch_t_ping", limit=1)
        if hist:
            break
        time.sleep(0.05)
    assert flag["ran"] is True
    hist = s.run_history("sch_t_ping", limit=1)
    assert hist and hist[0]["status"] == "complete"
    assert "pinged" in hist[0]["summary"]


def test_dispatch_builtin_failure_records_failed():
    def _boom():
        raise RuntimeError("kaboom")

    s.register_builtin_task("t_boom", _boom, label="Boom",
                            default_trigger="daily", default_spec={"hour": 0})
    rec = s.register_schedule({
        "id": "sch_t_boom", "name": "Boom", "trigger": "daily",
        "spec": {"hour": 0}, "task": {"kind": "builtin", "ref": "t_boom"},
        "retry": {"max": 0, "backoff_seconds": 1},
    })
    s.dispatch(rec, manual=True)
    for _ in range(50):
        hist = s.run_history("sch_t_boom", limit=1)
        if hist:
            break
        time.sleep(0.05)
    hist = s.run_history("sch_t_boom", limit=1)
    assert hist and hist[0]["status"] == "failed"
    assert "kaboom" in (hist[0]["error"] or "")


# ── Anti-spam: notify modes + orb policy (demo bug fixes) ────────────────────
def test_changed_treats_no_change_sentinel_as_unchanged():
    # The heartbeat replies EXACTLY "NO CHANGE" when nothing is new. A truthy
    # non-empty string used to count as "changed" and spammed a notification.
    assert s._changed("NO CHANGE") is False
    assert s._changed("no change.") is False
    assert s._changed("") is False
    assert s._changed("Found 2 new emails") is True
    assert s._changed({"changed": False}) is False
    assert s._changed({"count": 3}) is True


def test_orb_policy_skips_silent_and_agent_prompt():
    # silent maintenance (content publisher) → no orb; agent_prompt → the spawned
    # agent owns the orb; ordinary builtins → one orb.
    assert s._make_scheduler_orb(
        {"notify": "silent", "task": {"kind": "builtin"}}) is False
    assert s._make_scheduler_orb(
        {"notify": "status", "task": {"kind": "agent_prompt"}}) is False
    assert s._make_scheduler_orb(
        {"notify": "on_complete", "task": {"kind": "builtin"}}) is True


def _run_and_wait(rec, sid):
    s.dispatch(rec, manual=True)
    for _ in range(60):
        if s.run_history(sid, limit=1):
            return
        time.sleep(0.05)


def test_silent_schedule_never_notifies(monkeypatch):
    calls = {"run": 0, "status": 0}
    monkeypatch.setattr(s, "_notify_run", lambda *a, **k: calls.__setitem__("run", calls["run"] + 1))
    monkeypatch.setattr(s, "_notify_status", lambda *a, **k: calls.__setitem__("status", calls["status"] + 1))

    s.register_builtin_task("t_silent", lambda: {"changed": True, "summary": "did work"},
                            label="Silent", default_trigger="interval",
                            default_spec={"every_minutes": 1}, notify="silent")
    rec = s.register_schedule({
        "id": "sch_t_silent", "name": "Silent", "trigger": "interval",
        "spec": {"every_minutes": 1}, "notify": "silent",
        "task": {"kind": "builtin", "ref": "t_silent"}})
    _run_and_wait(rec, "sch_t_silent")
    # A silent (content-publisher-style) tick must push NOTHING on success.
    assert calls["run"] == 0 and calls["status"] == 0


def test_status_schedule_updates_panel_entry_not_badge(monkeypatch):
    seen = {"run": 0, "status": 0}
    monkeypatch.setattr(s, "_notify_run", lambda *a, **k: seen.__setitem__("run", seen["run"] + 1))
    monkeypatch.setattr(s, "_notify_status", lambda *a, **k: seen.__setitem__("status", seen["status"] + 1))

    s.register_builtin_task("t_status", lambda: "NO CHANGE", label="Beat",
                            default_trigger="interval", default_spec={"every_minutes": 60},
                            notify="status")
    rec = s.register_schedule({
        "id": "sch_t_status", "name": "Beat", "trigger": "interval",
        "spec": {"every_minutes": 60}, "notify": "status",
        "task": {"kind": "builtin", "ref": "t_status"}})
    _run_and_wait(rec, "sch_t_status")
    # status mode maintains the self-updating entry, never a badge notification.
    assert seen["status"] == 1 and seen["run"] == 0


def test_on_change_no_delta_is_silent(monkeypatch):
    seen = {"run": 0}
    monkeypatch.setattr(s, "_notify_run", lambda *a, **k: seen.__setitem__("run", seen["run"] + 1))
    s.register_builtin_task("t_nochange", lambda: {"changed": False}, label="NC",
                            default_trigger="daily", default_spec={"hour": 0},
                            notify="on_change")
    rec = s.register_schedule({
        "id": "sch_t_nochange", "name": "NC", "trigger": "daily",
        "spec": {"hour": 0}, "notify": "on_change",
        "task": {"kind": "builtin", "ref": "t_nochange"}})
    _run_and_wait(rec, "sch_t_nochange")
    assert seen["run"] == 0

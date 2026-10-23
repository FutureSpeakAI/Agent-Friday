"""A full disk pauses background work, visibly (services/disk_guard; North Star 32.13, BD-16).

The drive reading is replaced; nothing here fills a disk. On a tree without the module each
test fails at the fixture with the reason.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def dg(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    try:
        mod = importlib.import_module("agent_friday.services.disk_guard")
    except ImportError:
        pytest.fail("a full disk pauses nothing: services/disk_guard does not exist")
    mod._state.update(low=False, checked=0.0, free_mib=None, since=None)
    free = {"mib": 50_000.0}
    monkeypatch.setattr(mod, "_free_mib", lambda: free["mib"])
    from agent_friday import notifications_engine as ne
    cards, closed = [], []
    monkeypatch.setattr(ne, "push", lambda **k: cards.append(k) or {"ok": True})
    monkeypatch.setattr(ne, "resolve", lambda key: closed.append(key) or 1)
    mod.free, mod.cards, mod.closed = free, cards, closed
    yield mod
    mod._state.update(low=False, checked=0.0, free_mib=None, since=None)


def test_plenty_of_space_pauses_nothing(dg):
    assert dg.check(force=True)["low"] is False
    assert dg.holds({"id": "memory_dreaming"}) is False
    assert dg.cards == []


def test_a_full_disk_holds_background_work_but_not_what_keeps_the_owner_safe(dg):
    dg.free["mib"] = 300.0
    dg.check(force=True)
    for job in ("memory_dreaming", "knowledge_graph_reindex", "news_morning", "user-schedule-1"):
        assert dg.holds({"id": job}) is True, job
    for job in ("approvals_expiry_sweep", "calendar_reminders", "context_log_retention"):
        assert dg.holds({"id": job}) is False, job


def test_one_card_says_what_paused_and_that_nothing_is_deleted(dg):
    dg.free["mib"] = 300.0
    dg.check(force=True)
    dg.check(force=True)
    assert len(dg.cards) == 1
    card = dg.cards[0]
    assert card["emitter"] == "DF1" and card["dedupe_key"] == "disk-full"
    assert "300 MB" in card["body"] and "paused" in card["body"]
    assert "deletes nothing" in card["body"] and "Settings > Models" in card["body"]


def test_it_resumes_only_once_enough_is_free_again(dg):
    dg.free["mib"] = 300.0
    dg.check(force=True)
    dg.free["mib"] = 1500.0                      # above the pause line, below the resume line
    assert dg.check(force=True)["low"] is True and dg.closed == []
    dg.free["mib"] = 2500.0
    assert dg.check(force=True)["low"] is False
    assert dg.closed == ["disk-full"]
    assert dg.holds({"id": "memory_dreaming"}) is False


def test_a_reading_that_fails_pauses_nothing(dg):
    dg.free["mib"] = None
    s = dg.check(force=True)
    assert s["low"] is False and dg.status()["state"] == "unknown"


def test_the_drive_is_read_at_most_once_a_minute(dg, monkeypatch):
    reads = []
    monkeypatch.setattr(dg, "_free_mib", lambda: reads.append(1) or 50_000.0)
    dg.check(force=True, now=1000.0)
    dg.check(now=1010.0)
    dg.check(now=1070.0)
    assert len(reads) == 2


def test_the_scheduler_leaves_a_held_job_unmarked_and_runs_the_essential_ones(dg, monkeypatch):
    from agent_friday.services import scheduler as sch
    recs = [{"id": "memory_dreaming"}, {"id": "calendar_reminders"}]
    monkeypatch.setattr(sch, "_read_store", lambda: [dict(r) for r in recs])
    monkeypatch.setattr(sch, "_is_due", lambda rec, now: True)
    ran = []
    monkeypatch.setattr(sch, "dispatch", lambda rec, **k: ran.append(rec["id"]))
    for name in ("_reclaim_expired_lease", "_away_drain_tick", "_meeting_retention_tick"):
        monkeypatch.setattr(sch, name, lambda: None)
    dg.free["mib"] = 300.0
    dg.check(force=True)
    sch._tick()
    assert ran == ["calendar_reminders"]
    dg.free["mib"] = 5000.0
    dg.check(force=True)
    sch._tick()
    assert ran == ["calendar_reminders", "memory_dreaming", "calendar_reminders"]


def test_the_doctor_names_the_pause(dg):
    from agent_friday.services import doctor
    dg.free["mib"] = 300.0
    dg.check(force=True)
    assert doctor._c_disk() == ("degraded", "disk_full_background_paused")


def test_the_pause_is_recorded_without_paths(dg, monkeypatch):
    from agent_friday.services import activity_ledger
    rows = []
    monkeypatch.setattr(activity_ledger, "record", lambda kind, **f: rows.append((kind, f)) or True)
    dg.free["mib"] = 300.0
    dg.check(force=True)
    assert rows == [("disk_full", {"free_mib": 300, "state": "paused"})]
    assert "disk_full" in activity_ledger._ALLOWED_FIELDS

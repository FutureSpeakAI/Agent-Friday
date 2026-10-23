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
    mod._state.update(low=False, checked=0.0, free_mib=None, since=None, carded=False, card_busy=False)
    monkeypatch.setattr(mod, "SYNC_CARD", True, raising=False)
    free = {"mib": 50_000.0}
    monkeypatch.setattr(mod, "_free_mib", lambda: free["mib"])
    from agent_friday import notifications_engine as ne
    cards, closed = [], []
    monkeypatch.setattr(ne, "push", lambda **k: cards.append(k) or {"ok": True})
    monkeypatch.setattr(ne, "resolve", lambda key: closed.append(key) or 1)
    # The store as the guard reads it back: what push really saved.
    monkeypatch.setattr(ne, "_load", lambda: [{"dedupe_key": c.get("dedupe_key")} for c in cards])
    mod.free, mod.cards, mod.closed = free, cards, closed
    yield mod
    mod._state.update(low=False, checked=0.0, free_mib=None, since=None, carded=False, card_busy=False)


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


def _seeded(sch, ref):
    """The record a built-in really has (id 'sch_<ref>', task {kind: builtin, ref})."""
    meta = sch.BUILTIN_TASKS.get(ref) or {"label": ref, "default_trigger": "daily",
                                          "default_spec": {"hour": 3, "minute": 0}, "notify": "on_complete"}
    return sch._seed_record(ref, meta)


def test_built_in_jobs_are_matched_by_their_registered_name(dg):
    from agent_friday.services import scheduler as sch
    dg.free["mib"] = 300.0
    dg.check(force=True)
    for ref in ("approvals_expiry_sweep", "calendar_reminders", "context_log_retention"):
        rec = _seeded(sch, ref)
        assert rec["id"] == "sch_" + ref and dg.holds(rec) is False, ref
    assert dg.holds(_seeded(sch, "memory_dreaming")) is True


@pytest.fixture
def quiet_tick(monkeypatch):
    """_tick with its side calls stubbed: nothing real is released, leased, drained or kept."""
    from agent_friday.services import scheduler as sch
    from agent_friday.services import approvals, residency_arbiter
    monkeypatch.setattr(approvals, "release_held", lambda *a, **k: None)
    monkeypatch.setattr(residency_arbiter, "exclusive_lease", lambda *a, **k: None)
    calls = {"drain": 0}
    monkeypatch.setattr(sch, "_reclaim_expired_lease", lambda: None)
    monkeypatch.setattr(sch, "_meeting_retention_tick", lambda: None)
    monkeypatch.setattr(sch, "_away_drain_tick", lambda: calls.__setitem__("drain", calls["drain"] + 1))
    sch._calls = calls
    return sch


def test_the_scheduler_leaves_a_held_job_unmarked_and_runs_the_essential_ones(dg, quiet_tick, monkeypatch):
    sch = quiet_tick
    recs = [_seeded(sch, "memory_dreaming"), _seeded(sch, "calendar_reminders")]
    monkeypatch.setattr(sch, "_read_store", lambda: [dict(r) for r in recs])
    judged = []
    monkeypatch.setattr(sch, "_is_due", lambda rec, now: judged.append(rec["id"]) or True)
    ran = []
    monkeypatch.setattr(sch, "dispatch", lambda rec, **k: ran.append(rec["id"]))
    dg.free["mib"] = 300.0
    dg.check(force=True)
    sch._tick()
    assert ran == ["sch_calendar_reminders"]
    assert judged == ["sch_calendar_reminders"]       # a held job is not even judged (no missed rows)
    assert sch._calls["drain"] == 0                   # the away-time drain waits too
    dg.free["mib"] = 5000.0
    dg.check(force=True)
    sch._tick()
    assert ran == ["sch_calendar_reminders", "sch_memory_dreaming", "sch_calendar_reminders"]
    assert sch._calls["drain"] == 1


def test_a_card_that_could_not_be_written_is_tried_again(dg, monkeypatch):
    from agent_friday import notifications_engine as ne
    tries = []

    def flaky(**k):
        tries.append(1)
        if len(tries) == 1:
            raise OSError(28, "No space left on device")
        if len(tries) == 2:
            return {"ok": True}                  # the store swallowed a failed save: not there
        dg.cards.append(k)
        return {"ok": True}
    monkeypatch.setattr(ne, "push", flaky)
    dg.free["mib"] = 300.0
    for _ in range(5):
        dg.check(force=True)
    assert len(tries) == 3 and len(dg.cards) == 1


def test_the_card_is_retried_only_when_the_drive_is_read(dg, monkeypatch):
    from agent_friday import notifications_engine as ne
    tries = []

    def failing(**k):
        tries.append(1)
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(ne, "push", failing)
    dg.free["mib"] = 300.0
    dg.check(force=True, now=1000.0)
    for t in range(1001, 1050):
        dg.check(now=float(t))                   # cached readings: no new try
    assert len(tries) == 1
    dg.check(now=1061.0)
    assert len(tries) == 2


def test_a_failed_save_keeps_the_cards_already_saved(tmp_path, monkeypatch):
    from agent_friday import notifications_engine as ne
    path = tmp_path / "notifications.json"
    path.write_text('{"items": [{"id": "keep"}], "dismissed_jobs": {}}', encoding="utf-8")
    monkeypatch.setattr(ne, "NOTIF_FILE", path)
    real_write = type(path).write_text

    def full(self, *a, **k):
        if self.name.endswith(".tmp"):
            raise OSError(28, "No space left on device")
        return real_write(self, *a, **k)
    monkeypatch.setattr(type(path), "write_text", full)
    ne._save([{"id": "new"}], {})
    assert '"keep"' in path.read_text(encoding="utf-8")


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

"""Timer-driven work must not fight an exclusive GPU lease.

Stephen, 2026-08-18: "An hourly heartbeat launched while I was running my last
image job and the whole computer slowed to a crawl."

The lease was exclusive on the way in — acquiring one evicts every seat but the
R10-retained ones, exactly so an image job owns the card — and unenforced
afterwards. Nothing on the dispatch path asked whether a lease existed, so any
timer that woke and called a local model loaded ~7 GB back onto a card an image
job believed it owned.

This was harmless while scheduled work went to the cloud. Moving it to local
models (correctly: it was costing a million tokens a day) turned every timer
into a GPU competitor, and nothing taught the scheduler about leases. That is a
class, not an incident, so these tests cover the predicate as well as the gate.
"""
import types

import pytest

from agent_friday.services import scheduler as sch


# ── which timers can take the card ───────────────────────────────────────────

@pytest.mark.parametrize("kind", [
    "agent_prompt", "agent", "task", "prompt", "briefing", "digest",
    "consolidation", "dreaming",
])
def test_model_driven_schedules_count_as_gpu_work(kind):
    assert sch._uses_gpu({"id": "s", "kind": kind}) is True


@pytest.mark.parametrize("kind", [
    "file_move", "cleanup_temp", "prune_logs", "publish_queue",
])
def test_chores_do_not_count(kind):
    assert sch._uses_gpu({"id": "s", "kind": kind}) is False


def test_an_unknown_kind_defers_rather_than_guessing_it_is_safe():
    """Guessing wrong this way delays a chore. Guessing wrong the other way
    took his machine down."""
    assert sch._uses_gpu({"id": "s"}) is True
    assert sch._uses_gpu({"id": "s", "kind": "something_new"}) is True


# ── the gate itself ──────────────────────────────────────────────────────────

@pytest.fixture
def due_heartbeat(monkeypatch):
    """One due, GPU-using schedule, with dispatch recorded rather than run."""
    rec = {"id": "sch_heartbeat", "kind": "agent_prompt", "enabled": True}
    monkeypatch.setattr(sch, "_read_store", lambda: [rec])
    monkeypatch.setattr(sch, "_is_due", lambda r, now: True)
    monkeypatch.setattr(sch, "_reclaim_expired_lease", lambda: None)
    monkeypatch.setattr(sch, "_away_drain_tick", lambda: None)
    calls = []
    monkeypatch.setattr(sch, "dispatch", lambda r, **kw: calls.append(r["id"]))
    return calls


def _lease(monkeypatch, value):
    mod = types.ModuleType("agent_friday.services.residency_arbiter")
    mod.exclusive_lease = lambda: value
    monkeypatch.setitem(
        __import__("sys").modules, "agent_friday.services.residency_arbiter", mod)


def test_a_due_heartbeat_is_held_while_an_image_job_owns_the_card(
        due_heartbeat, monkeypatch):
    _lease(monkeypatch, {"kind": "image", "role": "image"})
    sch._tick()
    assert due_heartbeat == [], (
        "the heartbeat ran during an exclusive image lease — this is the "
        "collision that slowed his whole machine")


def test_the_held_heartbeat_runs_once_the_lease_releases(
        due_heartbeat, monkeypatch):
    """Queue-then-run, not skip.

    The record is left unmarked while held, so the first tick after the lease
    releases runs it. A delayed hourly heartbeat costs him nothing; a dropped
    one is a silent gap.
    """
    _lease(monkeypatch, {"kind": "image"})
    sch._tick()
    assert due_heartbeat == []

    _lease(monkeypatch, None)
    sch._tick()
    assert due_heartbeat == ["sch_heartbeat"]


def test_nothing_is_held_when_the_card_is_free(due_heartbeat, monkeypatch):
    _lease(monkeypatch, None)
    sch._tick()
    assert due_heartbeat == ["sch_heartbeat"]


def test_a_chore_still_runs_under_a_lease(monkeypatch):
    """Deferring must be narrow. A lease is not a reason to stop tidying up."""
    rec = {"id": "sch_cleanup", "kind": "cleanup_temp", "enabled": True}
    monkeypatch.setattr(sch, "_read_store", lambda: [rec])
    monkeypatch.setattr(sch, "_is_due", lambda r, now: True)
    monkeypatch.setattr(sch, "_reclaim_expired_lease", lambda: None)
    monkeypatch.setattr(sch, "_away_drain_tick", lambda: None)
    calls = []
    monkeypatch.setattr(sch, "dispatch", lambda r, **kw: calls.append(r["id"]))
    _lease(monkeypatch, {"kind": "image"})
    sch._tick()
    assert calls == ["sch_cleanup"]

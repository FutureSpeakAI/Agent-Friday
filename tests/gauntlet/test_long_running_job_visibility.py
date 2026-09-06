"""Gauntlet finding Q21: no signal existed for "a background job has been
running unusually long." ``routes/knowledge_graph.py``'s
``_TIER_B_STATE = {"running": False, "last": None}`` was the ENTIRE state
``/api/knowledge-graph/reindex/status`` exposed -- no ``started_at`` field,
so a caller polling it saw ``running: true`` identically whether the job
started 30 seconds or 7 hours ago (the same blind spot that let F31, a live
cost incident, run undetected for 7+ hours). Separately,
``services/scheduler.py``'s ``dispatch()`` marked a schedule's
``last_run_ts`` before running, but that field is overloaded -- start of
the run in progress WHILE running, start of the *last completed* run once
it's done -- rather than an explicit, dedicated "this run started at"
field a consumer could read without knowing that overload.

This is a pure visibility addition (no timeout, no watchdog, no change to
retry/failure semantics -- that is F25/Q24, handled separately).

This probe must be RED before the fix (``_TIER_B_STATE`` has no
``started_at`` key and a real Tier B run through it never sets one; a
dispatched schedule's record has no ``started_at`` field either) and GREEN
after.
"""
from __future__ import annotations

import time

import pytest

import agent_friday.services.knowledge_graph.indexer as idx
import agent_friday.services.knowledge_graph.wiki_graph as wg
from agent_friday.routes import knowledge_graph as kgr
from agent_friday.services import scheduler as s
from agent_friday.services.knowledge_graph import integration as kgi


@pytest.fixture(autouse=True)
def _restore_tier_b_state():
    prev = dict(kgr._TIER_B_STATE)
    yield
    kgr._TIER_B_STATE.clear()
    kgr._TIER_B_STATE.update(prev)


@pytest.fixture(autouse=True)
def _clean_scheduler_store(friday_dir):
    if s.SCHEDULES_FILE.exists():
        s.SCHEDULES_FILE.unlink()
    if s.RUNS_FILE.exists():
        s.RUNS_FILE.unlink()
    s._RUNNING.clear()
    yield
    s._RUNNING.clear()


class TestTierBStateExposesStartedAt:
    def test_tier_b_state_declares_a_started_at_key(self):
        assert "started_at" in kgr._TIER_B_STATE, (
            "_TIER_B_STATE has no started_at field -- "
            "/api/knowledge-graph/reindex/status cannot report elapsed "
            "time for an in-progress run, only a boolean"
        )

    def test_a_nightly_tier_b_run_sets_a_real_started_at_timestamp(
            self, monkeypatch):
        monkeypatch.setattr(kgi, "kg_settings",
                            lambda: {"enabled": True, "nightly_reindex": True})
        monkeypatch.setattr(kgi, "mark_wiki_dirty", lambda *a, **k: None)
        monkeypatch.setattr(wg, "rebuild_tier_a", lambda *a, **k: {})

        seen_during_run = {}

        def _fake_tier_b(*a, **k):
            seen_during_run["started_at"] = kgr._TIER_B_STATE.get("started_at")
            seen_during_run["running"] = kgr._TIER_B_STATE.get("running")
            return {"entities": 1}

        monkeypatch.setattr(idx, "reindex_tier_b", _fake_tier_b)
        kgr._TIER_B_STATE.clear()
        kgr._TIER_B_STATE.update(
            {"running": False, "last": None, "started_at": None})

        before = time.time()
        kgi.run_nightly_reindex()
        after = time.time()

        assert seen_during_run["running"] is True
        started = seen_during_run["started_at"]
        assert started is not None, (
            "a Tier B run in progress never populated started_at -- "
            "polling the status endpoint mid-run would still show only "
            "running:true with no timestamp to compute elapsed time from"
        )
        assert before - 1 <= started <= after + 1

    def test_status_route_response_shape_includes_started_at(self):
        """The status route (routes/knowledge_graph.py's kg_reindex_status)
        is a thin passthrough of _TIER_B_STATE -- confirm the exact keys it
        forwards include started_at, since that's the one-line change that
        makes the field reach an HTTP caller at all."""
        import inspect
        src = inspect.getsource(kgr.kg_reindex_status)
        assert "started_at" in src, (
            "kg_reindex_status() builds its response without forwarding "
            "_TIER_B_STATE's started_at field, so even though the state "
            "dict now carries it, a caller of the HTTP endpoint still "
            "can't see it"
        )


class TestScheduleRecordExposesStartedAt:
    def test_dispatch_patches_an_explicit_started_at_onto_the_record(self):
        def _slow_enough_to_observe():
            time.sleep(0.1)
            return {"summary": "ok"}

        ref = "t_started_at_q21"
        s.BUILTIN_TASKS.pop(ref, None)
        s.register_builtin_task(ref, _slow_enough_to_observe, label="X",
                                default_trigger="daily",
                                default_spec={"hour": 0, "minute": 0})
        rec = s.register_schedule({
            "id": "sch_t_started_at_q21", "name": "X", "trigger": "daily",
            "spec": {"hour": 0, "minute": 0},
            "task": {"kind": "builtin", "ref": ref},
        })

        before = time.time()
        s.dispatch(rec, manual=True)

        # While the task is still in flight, the schedule record should
        # already carry an explicit started_at (mark-before-run).
        found_while_running = None
        for _ in range(50):
            live = s.get_schedule("sch_t_started_at_q21")
            if live and live.get("started_at") is not None:
                found_while_running = live.get("started_at")
                break
            time.sleep(0.02)
        assert found_while_running is not None, (
            "dispatch() never recorded an explicit started_at on the "
            "schedule record -- a consumer of list_schedules()/"
            "get_schedule() can't cheaply answer 'how long has this "
            "already-running task been going' without it"
        )
        assert found_while_running >= before - 1

        # Wait for completion so the daemon thread doesn't leak into the
        # next test.
        for _ in range(100):
            if "sch_t_started_at_q21" not in s._RUNNING:
                break
            time.sleep(0.05)

    def test_list_schedules_surfaces_started_at_alongside_running(self):
        def _slow():
            time.sleep(0.15)
            return {}

        ref = "t_started_at_q21_list"
        s.BUILTIN_TASKS.pop(ref, None)
        s.register_builtin_task(ref, _slow, label="Y",
                                default_trigger="daily",
                                default_spec={"hour": 0, "minute": 0})
        s.register_schedule({
            "id": "sch_t_started_at_q21_list", "name": "Y", "trigger": "daily",
            "spec": {"hour": 0, "minute": 0},
            "task": {"kind": "builtin", "ref": ref},
        })
        rec = s.get_schedule("sch_t_started_at_q21_list")
        s.dispatch(rec, manual=True)

        found = None
        for _ in range(50):
            rows = s.list_schedules()
            row = next((r for r in rows
                       if r["id"] == "sch_t_started_at_q21_list"), None)
            if row and row.get("running") and row.get("started_at"):
                found = row
                break
            time.sleep(0.02)
        assert found is not None, (
            "list_schedules() (what the Settings UI polls) never showed a "
            "running row with a started_at timestamp attached"
        )

        for _ in range(100):
            if "sch_t_started_at_q21_list" not in s._RUNNING:
                break
            time.sleep(0.05)

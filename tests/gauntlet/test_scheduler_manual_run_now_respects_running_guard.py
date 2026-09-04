"""Gauntlet finding Q24 (part A): dispatch()'s concurrency guard was
``if sid in _RUNNING and not manual: return None`` -- the ``and not manual``
meant a manual "Run Now" dispatch deliberately skipped the ONLY guard
preventing a schedule from double-firing. If a schedule's normal tick had
already dispatched it, or a user clicked Run Now twice, a second
``_body()`` closure started concurrently, calling the same builtin function
a second time while the first was still running, with both threads then
calling ``_patch_record()`` on the same record with no ordering guarantee
(duplicate LLM spend for a cost-incurring builtin, or a corrupted
concurrent write for a file-writing job).

This probe must be RED before the fix (a manual dispatch while ``sid`` is
already in ``_RUNNING`` starts a second concurrent run anyway) and GREEN
after (a manual dispatch now returns None, exactly like an automatic one,
when the schedule is already running).
"""
from __future__ import annotations

import threading
import time

import pytest

from agent_friday.services import scheduler as s


@pytest.fixture(autouse=True)
def _clean_store(friday_dir):
    if s.SCHEDULES_FILE.exists():
        s.SCHEDULES_FILE.unlink()
    if s.RUNS_FILE.exists():
        s.RUNS_FILE.unlink()
    s._RUNNING.clear()
    yield
    s._RUNNING.clear()


class TestManualRunNowRespectsRunningGuard:
    def test_manual_dispatch_refused_while_already_running(self):
        gate = threading.Event()
        call_count = {"n": 0}

        def _slow():
            call_count["n"] += 1
            gate.wait(timeout=5)
            return {"summary": "done"}

        ref = "t_slow_q24a"
        s.BUILTIN_TASKS.pop(ref, None)
        s.register_builtin_task(ref, _slow, label="Slow",
                                default_trigger="daily",
                                default_spec={"hour": 0, "minute": 0})
        rec = s.register_schedule({
            "id": "sch_t_slow_q24a", "name": "Slow", "trigger": "daily",
            "spec": {"hour": 0, "minute": 0},
            "task": {"kind": "builtin", "ref": ref},
        })

        try:
            first_run_id = s.dispatch(rec, manual=True)
            assert first_run_id, "the first manual dispatch should start"

            # dispatch() adds sid to _RUNNING synchronously before starting
            # the daemon thread, so this should already be true -- poll
            # briefly anyway rather than assume zero scheduling delay.
            for _ in range(50):
                if rec["id"] in s._RUNNING:
                    break
                time.sleep(0.02)
            assert rec["id"] in s._RUNNING

            second_run_id = s.dispatch(rec, manual=True)
            assert second_run_id is None, (
                "a second manual dispatch was allowed to start while the "
                "first was still running -- this is the exact double-fire "
                "Q24 describes"
            )
            assert call_count["n"] == 1, (
                "the underlying task function was invoked more than once "
                "concurrently"
            )
        finally:
            gate.set()
            for _ in range(100):
                if rec["id"] not in s._RUNNING:
                    break
                time.sleep(0.02)

    def test_automatic_dispatch_guard_is_unchanged(self):
        """Sanity check: the non-manual path's behavior (already correct
        before this fix) still works the same way."""
        gate = threading.Event()

        def _slow():
            gate.wait(timeout=5)
            return {}

        ref = "t_slow_q24a_auto"
        s.BUILTIN_TASKS.pop(ref, None)
        s.register_builtin_task(ref, _slow, label="Slow2",
                                default_trigger="daily",
                                default_spec={"hour": 0, "minute": 0})
        rec = s.register_schedule({
            "id": "sch_t_slow_q24a_auto", "name": "Slow2", "trigger": "daily",
            "spec": {"hour": 0, "minute": 0},
            "task": {"kind": "builtin", "ref": ref},
        })
        try:
            s.dispatch(rec, manual=True)
            for _ in range(50):
                if rec["id"] in s._RUNNING:
                    break
                time.sleep(0.02)
            assert s.dispatch(rec, manual=False) is None
        finally:
            gate.set()
            for _ in range(100):
                if rec["id"] not in s._RUNNING:
                    break
                time.sleep(0.02)

    def test_a_completed_run_can_be_manually_re_triggered(self):
        """The fix must not make a schedule permanently un-runnable -- once
        _RUNNING clears (the run finished), a fresh manual dispatch is
        allowed again."""
        def _fast():
            return {"summary": "done"}

        ref = "t_fast_q24a"
        s.BUILTIN_TASKS.pop(ref, None)
        s.register_builtin_task(ref, _fast, label="Fast",
                                default_trigger="daily",
                                default_spec={"hour": 0, "minute": 0})
        rec = s.register_schedule({
            "id": "sch_t_fast_q24a", "name": "Fast", "trigger": "daily",
            "spec": {"hour": 0, "minute": 0},
            "task": {"kind": "builtin", "ref": ref},
        })
        first = s.dispatch(rec, manual=True)
        assert first
        for _ in range(50):
            if rec["id"] not in s._RUNNING:
                break
            time.sleep(0.02)
        assert rec["id"] not in s._RUNNING

        second = s.dispatch(rec, manual=True)
        assert second, "a manual re-trigger after completion must be allowed"

        # Wait for the second run's daemon thread to fully finish (including
        # its own _write_store() call) before the test returns -- otherwise
        # a lingering writer can still hold schedules.json open on Windows
        # when the NEXT test file's fixture tries to unlink it.
        for _ in range(100):
            if rec["id"] not in s._RUNNING:
                break
            time.sleep(0.02)
        assert rec["id"] not in s._RUNNING

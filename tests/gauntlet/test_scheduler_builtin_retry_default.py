"""Gauntlet finding F25: register_builtin_task() has no retry parameter, and
_normalize_record()'s fallback used ``{"max": 0, "backoff_seconds": 300}``
for every schedule regardless of kind -- so every builtin task (news,
weekly digest, memory dreaming, KG reindex, etc.) shipped with zero retry
tolerance. dispatch()'s failure branch (``attempts(1) <= maxr(0)`` is false
on the very first failure) then marked the job failed immediately instead
of retrying, and it silently waited until its next normal slot -- which can
be a full day/week away.

This probe must be RED before the fix (a freshly-normalized builtin
schedule's retry.max is 0, and a real seeded builtin task that fails once
never gets a retry_scheduled outcome) and GREEN after (builtin schedules
get a conservative non-zero default; an explicit retry config, or a
non-builtin schedule, is untouched).
"""
from __future__ import annotations

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


class TestBuiltinTaskRetryDefault:
    def test_seeded_builtin_schedule_gets_nonzero_retry_default(self):
        rec = s._normalize_record({
            "id": "sch_kg_reindex_test", "name": "KG reindex",
            "trigger": "daily", "spec": {"hour": 3, "minute": 30},
            "task": {"kind": "builtin", "ref": "knowledge_graph_reindex"},
        }, source="builtin")
        assert rec["retry"]["max"] > 0, (
            "a builtin schedule was normalized with zero retry tolerance -- "
            "any transient failure marks it failed on the very first "
            "attempt instead of retrying"
        )
        assert rec["retry"]["backoff_seconds"] > 0

    def test_user_schedule_still_defaults_to_zero_retry(self):
        # Behavior for non-builtin (user-authored agent_prompt) schedules is
        # unchanged -- this fix is scoped to builtin tasks only.
        rec = s._normalize_record({
            "name": "My prompt", "trigger": "daily",
            "spec": {"hour": 9, "minute": 0},
            "task": {"kind": "agent_prompt", "prompt": "do a thing"},
        }, source="user")
        assert rec["retry"] == {"max": 0, "backoff_seconds": 300}

    def test_explicit_retry_config_always_wins_over_the_builtin_default(self):
        rec = s._normalize_record({
            "id": "sch_x25", "name": "X", "trigger": "daily",
            "spec": {"hour": 1, "minute": 0},
            "task": {"kind": "builtin", "ref": "x"},
            "retry": {"max": 9, "backoff_seconds": 42},
        }, source="builtin")
        assert rec["retry"] == {"max": 9, "backoff_seconds": 42}

    def test_a_real_seeded_builtin_task_actually_retries_on_first_failure(self):
        """Behavioral, not just a normalization-shape check: register a
        builtin task that fails exactly once, seed it via the real
        _seed_and_reconcile() path (the path every real builtin task goes
        through at startup), dispatch it, and confirm dispatch()'s own
        retry math (attempts <= maxr) schedules a retry instead of marking
        it permanently failed."""
        attempts = {"n": 0}

        def _flaky():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient blip")
            return {"summary": "ok on retry"}

        ref = "t_flaky_f25"
        s.BUILTIN_TASKS.pop(ref, None)
        s.register_builtin_task(ref, _flaky, label="Flaky",
                                default_trigger="daily",
                                default_spec={"hour": 0, "minute": 0})
        s._seed_and_reconcile()
        rec = s.get_schedule(f"sch_{ref}")
        assert rec is not None
        assert rec["retry"]["max"] > 0

        s.dispatch(rec, manual=True)
        hist = []
        for _ in range(50):
            hist = s.run_history(f"sch_{ref}", limit=1)
            if hist:
                break
            time.sleep(0.05)
        assert hist and hist[0]["status"] == "retry_scheduled", (
            "a builtin task's first transient failure did not schedule a "
            "retry -- it should not be marked permanently failed on attempt 1"
        )

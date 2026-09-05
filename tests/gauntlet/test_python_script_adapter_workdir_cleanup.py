"""Gauntlet finding F71 (2026-09-04): PythonScriptAdapter._run() created a
fresh tempfile.mkdtemp(prefix="friday_worker_") per worker job and never
removed it -- not on success, not on failure, not on timeout, not on any
exception. Unconditional, permanent, in production code (not test
infrastructure), on every single invocation, forever.

Found investigating "stop patching instances, find the owner" for the
temp-directory leak F47/F51/F65 each independently fixed a DIFFERENT,
much smaller leak class for (friday_test_home_*, 4 directories currently).
A full accounting of %TEMP%'s friday_*-prefixed directories found
friday_worker_* responsible for 1,533 of ~1,569 -- the actual dominant
leak, never previously named, sitting in the codebase's dual-role
orchestration worker adapter, not its test isolation.

This probe proves the fix behaviorally: a real worker run's own workdir
survives immediately after (so a caller can still read `artifacts`), but
an ARTIFICIALLY AGED workdir from a prior run gets swept the next time
any worker runs -- the same "sweep anything past the retention window"
shape as tests/conftest.py's own already-reviewed _sweep_stale_test_homes.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from agent_friday.services.worker_adapters.python_script_adapter import (
    PythonScriptAdapter, _sweep_stale_workdirs, _JOBS,
)


class _FakeTask:
    def __init__(self, prompt, deadline_seconds=10):
        self.task_id = "t-" + prompt[:8]
        self.prompt = prompt
        self.deadline_seconds = deadline_seconds


def _run_and_wait(adapter, prompt, timeout_s=10):
    from agent_friday.services.worker_adapters.base import WorkerStatus
    aid = adapter.start(_FakeTask(prompt))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if adapter.poll(aid) in (WorkerStatus.COMPLETED, WorkerStatus.FAILED,
                                  WorkerStatus.TIMEOUT):
            break
        time.sleep(0.05)
    return aid


class TestWorkerWorkdirCleanup:
    def setup_method(self, method):
        _JOBS.clear()

    def teardown_method(self, method):
        _JOBS.clear()

    def test_a_freshly_completed_jobs_workdir_is_not_swept(self):
        """A caller reading .artifacts right after completion must still
        find the files -- cleanup must not be immediate."""
        adapter = PythonScriptAdapter()
        aid = _run_and_wait(adapter, "print('hello')\n")
        result = adapter.result(aid)
        assert result.error is None, f"worker script failed: {result.error}"
        # The workdir itself (parent of any artifact, or at least its own
        # directory) must still exist -- fresh completion, well inside the
        # retention window.
        workdirs_now = list(Path(tempfile.gettempdir()).glob("friday_worker_*"))
        assert workdirs_now, (
            "no friday_worker_* directory exists immediately after a "
            "completed job -- either nothing ran, or cleanup fired too "
            "eagerly and would break artifact access"
        )

    def test_an_aged_workdir_is_swept_on_the_next_job(self):
        """The actual regression guard: plant a fake, artificially-aged
        workdir (simulating one left over from a much earlier job) and
        confirm the NEXT worker invocation's sweep removes it."""
        stale = Path(tempfile.mkdtemp(prefix="friday_worker_"))
        (stale / "leftover.txt").write_text("stale artifact", encoding="utf-8")
        old_time = time.time() - 7200  # 2 hours ago, past the 1-hour retention
        os.utime(stale, (old_time, old_time))
        assert stale.exists()

        _sweep_stale_workdirs()

        assert not stale.exists(), (
            "a friday_worker_* directory older than the retention window "
            "survived a sweep -- F71 regressed (the unconditional leak is "
            "back, or the sweep's age check is broken)"
        )

    def test_a_recent_workdir_is_not_swept_by_a_conservative_retention(self):
        recent = Path(tempfile.mkdtemp(prefix="friday_worker_"))
        try:
            _sweep_stale_workdirs()
            assert recent.exists(), (
                "sweep removed a workdir well inside the retention window -- "
                "over-aggressive cleanup would delete a concurrently-running "
                "job's own files"
            )
        finally:
            import shutil
            shutil.rmtree(recent, ignore_errors=True)

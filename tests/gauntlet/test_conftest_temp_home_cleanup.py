"""Gauntlet: tests/conftest.py's temp-home leak.

tests/conftest.py's `_TEST_HOME` is created fresh by every pytest process
via `tempfile.mkdtemp()`. Left unremoved, these directories (up to ~780MB
each) accumulate by the thousand, fill the disk with the live app running,
and crash it -- while every suite run stays green, because the leak is a side
effect of the run, not a test outcome.

Two mechanisms close this:
  * `pytest_sessionfinish` removes the CURRENT run's own temp home on a
    normal exit -- a suite that runs to completion leaks nothing.
  * `_sweep_stale_test_homes` removes any left behind by a run that never
    reached that hook (Ctrl+C, OOM kill, a hard crash) the next time any
    suite starts -- self-healing, bounded to at most one run's worth of
    leaked directories at a time.

This probe exercises both functions directly, against throwaway `tmp_path`
directories, WITHOUT touching the real `_TEST_HOME` this very test session
is running out of (deleting that mid-session would break every other test).
"""
from __future__ import annotations

import os
import time

import tests.conftest as root_conftest


def test_sweep_stale_test_homes_removes_old_orphans(tmp_path):
    stale = tmp_path / "friday_test_home_stale123"
    stale.mkdir()
    old_time = time.time() - 7200  # 2 hours old
    os.utime(stale, (old_time, old_time))

    root_conftest._sweep_stale_test_homes(tmp_path, max_age_seconds=3600)

    assert not stale.exists(), (
        "a temp home older than max_age_seconds must be swept -- this is "
        "the mechanism that reclaims disk from a run that never reached "
        "pytest_sessionfinish (Ctrl+C, OOM kill, crash)"
    )


def test_sweep_stale_test_homes_leaves_fresh_ones_alone(tmp_path):
    fresh = tmp_path / "friday_test_home_fresh456"
    fresh.mkdir()

    root_conftest._sweep_stale_test_homes(tmp_path, max_age_seconds=3600)

    assert fresh.exists(), (
        "the sweep must not delete a temp home still in active use by a "
        "currently-running test session"
    )


def test_sweep_ignores_unrelated_directories(tmp_path):
    unrelated = tmp_path / "some_other_temp_dir"
    unrelated.mkdir()
    old_time = time.time() - 7200
    os.utime(unrelated, (old_time, old_time))

    root_conftest._sweep_stale_test_homes(tmp_path, max_age_seconds=3600)

    assert unrelated.exists(), (
        "the sweep must only ever touch its own friday_test_home_* "
        "directories, never arbitrary temp-dir siblings"
    )


def test_session_finish_removes_this_runs_temp_home(monkeypatch, tmp_path):
    fake_home = tmp_path / "friday_test_home_fake_for_probe"
    fake_home.mkdir()
    (fake_home / "marker.txt").write_text("x")
    monkeypatch.setattr(root_conftest, "_TEST_HOME", fake_home)

    root_conftest.pytest_sessionfinish(session=None, exitstatus=0)

    assert not fake_home.exists(), (
        "pytest_sessionfinish must remove the run's own temp home on a "
        "normal exit -- this is what makes a suite run to completion leak "
        "nothing at all, with the sweep only needed for abnormal exits"
    )

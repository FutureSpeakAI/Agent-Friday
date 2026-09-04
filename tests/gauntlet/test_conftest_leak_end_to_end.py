"""Gauntlet finding F47, corrected end-to-end (2026-09-04): tests/conftest.py
creates one throwaway temp home per pytest PROCESS via tempfile.mkdtemp()
and, before the first fix, never removed any of them -- confirmed to have
accumulated 3,858 such directories since June (up to 781MB each), which
drove the real C: drive to 0 bytes free and crashed the live Friday app
with a stack overflow (2026-09-03 08:13).

Stephen's own re-check of the first fix (test_conftest_temp_home_cleanup.py)
found the leak WAS STILL HAPPENING in real runs -- ~60GB/hour, refilling the
drive toward another crash within about two hours of the verifier's cleanup
-- despite that probe reporting green. The reason: that probe calls
pytest_sessionfinish() and _sweep_stale_test_homes() directly, as plain
Python functions, against isolated tmp_path fixtures. It never runs an
actual pytest PROCESS end-to-end, so it could not see the real failure
mode: on Windows, shutil.rmtree(ignore_errors=True) silently leaves a
directory behind (with no error, no trace) whenever ANY file inside it is
still briefly locked at session-end -- a logging.FileHandler, an open
sqlite connection, a thread that hasn't finished closing a handle before
pytest's sessionfinish hook fires. A single-shot rmtree with no retry hits
this often enough in real (non-trivial) test runs to leak steadily.

This probe is the proof Stephen specifically asked for: "a run that leaves
nothing behind, verified by counting the directories before and after
rather than by reading the fixture." It spawns a REAL pytest subprocess
against a real, moderately heavy test file (one that actually opens
files -- sqlite-backed run history, JSON schedule files -- inside the
isolated home, the exact shape of thing that was racing the original
single-shot rmtree) and asserts no `friday_test_home_*` directory is left
behind that didn't already exist before the subprocess ran.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def _temp_homes() -> set[str]:
    base = Path(tempfile.gettempdir())
    return {p.name for p in base.glob("friday_test_home_*")}


class TestConftestTempHomeLeakEndToEnd:
    def test_a_real_pytest_run_leaves_no_new_temp_home_behind(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        before = _temp_homes()

        # tests/unit/test_scheduler.py: real file I/O inside the isolated
        # home (SCHEDULES_FILE/RUNS_FILE, sqlite-adjacent run history) --
        # the exact shape of test that raced the original single-shot
        # rmtree(ignore_errors=True) and left directories behind in real
        # runs despite the isolated-function probe reporting green.
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit/test_scheduler.py", "-q"],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, (
            f"the subprocess test run itself failed (not what this probe is "
            f"about, but its leak-freedom can't be assessed if it didn't "
            f"run):\n{result.stdout}\n{result.stderr}"
        )

        after = _temp_homes()
        leaked = after - before
        assert not leaked, (
            f"a real pytest process run left {len(leaked)} new "
            f"friday_test_home_* director{'y' if len(leaked) == 1 else 'ies'} "
            f"behind in {tempfile.gettempdir()} that didn't exist before it "
            f"ran: {sorted(leaked)} -- this is the actual disk-filling "
            f"defect (findings.jsonl F47): every test run that hits this "
            f"leaks real disk space, and a passing suite cannot see it "
            f"because the leak is a side effect of the run, not a test "
            f"outcome"
        )

"""Gauntlet finding F49: tests/test_judgment_gate.py minted its OWN isolated
home directory (tempfile.mkdtemp(prefix="friday_judgment_")) instead of
using the shared one tests/conftest.py already provides for every test
under tests/ -- and, unlike conftest.py's fixture, had NO cleanup at all,
not even pytest_sessionfinish for a clean exit. Every run of this file left
one directory behind forever, each containing its own copy of the
sentence-transformers HF cache (~850MB-2.8GB depending on what got
downloaded into it). Found 97 of them while investigating an unrelated
disk-pressure report on 2026-09-04, dating back to 2026-08-17, totaling
~110GB -- a second, independent instance of the exact leak class F47 fixed
in conftest.py, undetected by F47's own proof (which only exercises
conftest.py's fixture, not this file's separate one).

This probe proves the fix behaviorally: running the real test file leaves
no friday_judgment_* directory behind, using the exact "count real
directories before and after a real run" methodology F47 itself
established -- not a read of the source.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _judgment_dirs():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "friday_judgment_*")))


def test_source_no_longer_mints_its_own_unmanaged_home():
    text = (_REPO_ROOT / "tests" / "test_judgment_gate.py").read_text(encoding="utf-8")
    assert "import tempfile" not in text, (
        "tests/test_judgment_gate.py imports tempfile again -- if that's for "
        "a new, legitimate reason, fine, but check it isn't the old "
        "mkdtemp(prefix='friday_judgment_') pattern back (F49 regressed)"
    )
    assert "_TEST_HOME = Path(tempfile.mkdtemp" not in text, (
        "tests/test_judgment_gate.py still mints its own isolated home "
        "instead of relying on tests/conftest.py's shared, crash-safe one "
        "(F49 regressed)"
    )


def test_a_real_run_of_the_file_leaves_no_new_temp_home_behind():
    before = _judgment_dirs()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_judgment_gate.py", "-q"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=180,
        env={**os.environ, "FRIDAY_TESTING": "1"},
    )
    assert result.returncode == 0, (
        f"tests/test_judgment_gate.py itself failed (not what this probe is "
        f"about, but its leak-freedom can't be assessed if it didn't pass):"
        f"\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
    )
    after = _judgment_dirs()
    leaked = after - before
    assert not leaked, (
        f"running tests/test_judgment_gate.py left {len(leaked)} new "
        f"friday_judgment_* director{'y' if len(leaked) == 1 else 'ies'} "
        f"behind in {tempfile.gettempdir()}: {sorted(leaked)} -- this is "
        f"the F49 leak; a passing suite cannot see it because the leak is "
        f"a side effect of the run, not a test outcome"
    )

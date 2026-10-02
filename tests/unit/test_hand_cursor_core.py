"""The hand cursor core is pure JavaScript; node runs its table of cases.

The invariants under test (see static/hand_cursor_core.js):
  * snap hysteresis: a lock engages inside engageRadius, survives to releaseRadius, and never
    flickers between neighbours while held;
  * pinch freeze: a click is reported at the pinch's ONSET point, never where the fingers
    parted; past the slop a pinch is a drag; a guarded target needs the hold to complete;
  * dwell fires once per visit; the One Euro filter removes jitter without lagging intent.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "static" / "hand_cursor_core.js"
CASES = ROOT / "tests" / "unit" / "hand_cursor_core.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_hand_cursor_core_cases_pass():
    assert CORE.exists(), CORE
    proc = subprocess.run(
        [shutil.which("node"), str(CASES)], capture_output=True, text=True, timeout=60, cwd=str(ROOT)
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " 0 failed" in proc.stdout, proc.stdout


def test_core_is_loadable_by_the_page_and_by_node():
    src = CORE.read_text(encoding="utf-8")
    assert "root.FridayHandCore = api" in src
    assert "module.exports = api" in src
    # No DOM in the core: it must stay testable without a browser.
    for forbidden in ("document.", "window.", "requestAnimationFrame"):
        assert forbidden not in src, forbidden

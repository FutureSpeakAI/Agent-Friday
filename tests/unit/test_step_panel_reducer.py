"""The step list reducer is pure JavaScript; node runs the cases (static/friday_stage.js reduceSteps)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "unit" / "step_panel.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_step_reducer_cases_pass():
    proc = subprocess.run([shutil.which("node"), str(CASES)], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " 0 failed" in proc.stdout, proc.stdout

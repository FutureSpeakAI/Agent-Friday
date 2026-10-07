"""domList (static/friday_stage.js): a workspace whose rows only carry data-fr-ref reports a stage, can be pointed
at and, where it is selectable, ticked by Friday, by Ctrl-click and by a pinch. Node runs the cases."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "unit" / "friday_stage_domlist.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_domlist_cases_pass():
    proc = subprocess.run([shutil.which("node"), str(CASES)], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " 0 failed" in proc.stdout, proc.stdout

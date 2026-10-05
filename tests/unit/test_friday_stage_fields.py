"""The fields Friday may write into are pure JavaScript at the core; node runs the cases.

static/friday_stage.js registerField / fillField / undoFill / fillChip: a fill writes through the field and
nothing else, says "Friday wrote this" with an Undo until the owner edits it, shows what in it came from something
Friday read, refuses a field nobody registered, and has no way to send, save or create.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "unit" / "friday_stage_fields.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_field_cases_pass():
    proc = subprocess.run([shutil.which("node"), str(CASES)], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " 0 failed" in proc.stdout, proc.stdout

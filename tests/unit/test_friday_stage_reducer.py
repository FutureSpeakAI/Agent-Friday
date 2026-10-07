"""The See & Touch page layer is pure JavaScript; node runs its table of cases.

static/friday_stage.js holds the selection reducer every list shares (replace, add, remove, owner
toggle, the owner's navigation clearing ticks while Friday's reveal does not, held / done / declined),
the sweep timing (the whole sweep ends inside 350 ms) and the 334 ms limiter.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "static" / "friday_stage.js"
CASES = ROOT / "tests" / "unit" / "friday_stage_reducer.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_stage_reducer_cases_pass():
    assert SRC.exists(), SRC
    proc = subprocess.run([shutil.which("node"), str(CASES)], capture_output=True, text=True, timeout=60,
                          cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " 0 failed" in proc.stdout, proc.stdout


def test_the_layer_loads_in_the_page_and_in_node_and_names_no_host():
    src = SRC.read_text(encoding="utf-8")
    assert "root.fridayStage = factory()" in src and "module.exports = factory()" in src
    for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "http://", "https://"):
        assert forbidden not in src, forbidden

"""The hand cursor tells the stage which row it is on, so "archive this" means that row (phase 3).

static/friday_stage.js keeps what the reticle was last on for three seconds and reports it with its age;
a guarded control and an orb are never reported (hand_cursor.js passes an empty ref for them). Node runs
the cases; the page wiring is checked in the source.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "unit" / "hand_cursor_stage.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_hand_cursor_stage_cases_pass():
    proc = subprocess.run([shutil.which("node"), str(CASES)], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " 0 failed" in proc.stdout, proc.stdout


def test_the_cursor_reports_every_frame_and_a_held_row_can_open_on_a_double_click():
    hc = (ROOT / "static" / "hand_cursor.js").read_text(encoding="utf-8")
    assert "reportCursor(res.target, pinch.pinched);" in hc
    assert "data-fr-open-event" in hc and "new MouseEvent('dblclick'" in hc
    assert "fridayStage.cursor" in hc.replace("FS.cursor", "fridayStage.cursor") or "FS.cursor(ref" in hc

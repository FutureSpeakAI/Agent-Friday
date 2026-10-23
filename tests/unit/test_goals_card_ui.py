"""System > Goals, run under node so the suite runs it (tests/ui/goals_card.test.cjs),
and present in both UI sources (BD-12 slice G3)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
node = shutil.which("node")


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_goals_card_passes_under_node():
    cp = subprocess.run([node, "--test", str(REPO / "tests" / "ui" / "goals_card.test.cjs")],
                        capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(REPO))
    assert cp.returncode == 0, (cp.stdout[-4000:] + cp.stderr[-2000:])


@pytest.mark.parametrize("source", ["index.html", "ui_parts/app.html"])
def test_the_goals_card_sits_in_system_before_task_records(source):
    text = (REPO / source).read_text(encoding="utf-8")
    assert "function GoalsCard(" in text
    i = text.index("function SystemWS(")
    body = text[i:]
    g = body.find("GoalsCard")
    t = body.find("TaskRecordsCard")
    assert 0 <= g < t, "the Goals card is not rendered in System"

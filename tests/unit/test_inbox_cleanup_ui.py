"""Messages > Clean up, run under node so the suite runs it (tests/ui/inbox_cleanup.test.cjs)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
node = shutil.which("node")


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_clean_up_board_passes_under_node():
    cp = subprocess.run([node, "--test", str(REPO / "tests" / "ui" / "inbox_cleanup.test.cjs")],
                        capture_output=True, text=True, timeout=120, cwd=str(REPO))
    assert cp.returncode == 0, (cp.stdout[-4000:] + cp.stderr[-2000:])

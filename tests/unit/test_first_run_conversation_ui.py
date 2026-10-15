"""The first-run conversation's step cards, run under node so the suite runs them.

tests/ui/setup_conversation.test.cjs renders the served UI's cards with a
minimal React stand-in; this wrapper is what makes the suite (and CI) run it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
node = shutil.which("node")


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_setup_step_cards_pass_under_node():
    cp = subprocess.run([node, "--test", str(REPO / "tests" / "ui" / "setup_conversation.test.cjs")],
                        capture_output=True, text=True, timeout=120, cwd=str(REPO))
    assert cp.returncode == 0, (cp.stdout[-4000:] + cp.stderr[-2000:])

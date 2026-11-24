"""The start screen's countdowns: the hide control and the cluster's place beside the chat tray, run under node
(tests/ui/start_countdowns_hide.test.cjs) so the suite runs it, and present in both page sources."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node is not on PATH")
def test_the_countdown_hide_control_and_tray_place_pass_under_node():
    done = subprocess.run([NODE, "--test", str(REPO / "tests" / "ui" / "start_countdowns_hide.test.cjs")],
                          capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(REPO))
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


@pytest.mark.parametrize("page", ["index.html", "ui_parts/head.html"])
def test_the_hide_control_has_its_style_in_both_style_sources(page):
    assert ".cd-hide" in (REPO / page).read_text(encoding="utf-8")

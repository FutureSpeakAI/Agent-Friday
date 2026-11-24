"""A Local AI queue job one test file leaves behind is gone before the next file.

Every test in one pytest process shares one seat supervisor (services/agent._SEAT_SUPERVISOR). A file that starts a
local task and never waits for it left that job counted on the top bar ("1 task in Activity; 1 local-queue job"),
and tests/api/test_workspace_job_card_ui.py errored at setup whenever such a file shared its xdist worker: the b60
gate (bouncing #1057 and #1070, neither of which caused it) and CI on #652 #810 #828 #974 #1041. tests/conftest.py
takes every job a file added off the queue when the file ends. This runs a leaker, then a clean-queue probe, in ONE
process, the way a worker would.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEAKER = "tests/api/leaky_local_queue_probe.py"
PROBE = "tests/api/test_local_queue_starts_clean.py"


def test_a_job_one_file_left_on_the_local_queue_is_gone_in_the_next():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST") and k != "PYTEST_CURRENT_TEST"}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", LEAKER, PROBE,
         "-n", "0", "-p", "no:randomly", "-p", "no:cacheprovider", "-rA", "-o", "addopts="],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)
    out = r.stdout + r.stderr
    passed = set(re.findall(r"^PASSED (\S+)", out, flags=re.M))
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, flags=re.M)
    tail = "\n".join(out.strip().splitlines()[-30:])
    assert any(n.startswith(LEAKER + "::") for n in passed), "the leaker did not run and pass:\n%s" % tail
    assert r.returncode == 0 and not failed, "a job left by one file was still queued in the next:\n%s\n\n%s" % (
        "\n".join(failed), tail)
    assert any(n.startswith(PROBE + "::") for n in passed), "the probe did not run:\n%s" % tail


def test_the_top_bar_guard_names_what_was_left():
    text = (ROOT / "tests" / "api" / "conftest.py").read_text(encoding="utf-8")
    at = text.index("def top_bar_leftovers")
    body = text[at:text.index("\ndef ", at + 10)]
    assert "label" in body, "the guard counts leftover jobs but does not name them"

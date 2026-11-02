"""A notice written on another thread after its test file ended does not reach the next file's top bar.

On Windows CI the clean-top-bar probe (tests/api/test_top_bar_starts_clean.py)
and the job-card guard after it found "29 updates (info x27, warning x1,
laya_learning x1)" on PRs that post none of those kinds. Two writers outlived
the per-file put-back of notifications.json (tests/conftest.py
_notices_and_approvals_do_not_outlive_their_file):

* laya_personal's first-use notice runs on its own thread
  (laya-personal-notice), kicked by a routed chat turn, and nothing waited
  for it at the end of the test;
* a push reads every card, adds its own and writes them all back. A push
  that read before the put-back and wrote after it wrote back every card the
  file had raised (the pre-approved-send notices), with its own.

Each case runs a late writer from tests/unit/late_notice_writers.py, then the
probe, in ONE child process, the way an xdist worker runs files one after
another. tests/late_notice_wait.py waits for the writer before the probe's
file starts, so the outcome does not depend on timing.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRITERS = "tests/unit/late_notice_writers.py"
PROBE = "tests/api/test_top_bar_starts_clean.py"


def _run_writer_then_probe(writer: str) -> None:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST") and k != "PYTEST_CURRENT_TEST"}
    node = "%s::%s" % (WRITERS, writer)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", node, PROBE, "-p", "tests.late_notice_wait",
         "-n", "0", "-p", "no:randomly", "-p", "no:cacheprovider", "-rA"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)
    out = r.stdout + r.stderr
    passed = set(re.findall(r"^PASSED (\S+)", out, flags=re.M))
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+.*)$", out, flags=re.M)
    tail = "\n".join(out.strip().splitlines()[-30:])
    assert node in passed, "%s did not run and pass:\n%s" % (node, tail)
    assert r.returncode == 0 and not failed, (
        "a notice written after %s ended reached the next file's top bar:\n%s\n\n%s"
        % (writer, "\n".join(failed), tail))
    assert any(n.startswith(PROBE + "::") for n in passed), "the probe did not run:\n%s" % tail


def test_laya_first_use_notice_thread_finishes_inside_its_test():
    _run_writer_then_probe("test_laya_first_use_notice_is_posted_after_the_turn_returns")


def test_a_push_split_by_the_put_back_does_not_bring_the_files_cards_back():
    _run_writer_then_probe("test_a_push_that_read_before_the_put_back_writes_after_it")
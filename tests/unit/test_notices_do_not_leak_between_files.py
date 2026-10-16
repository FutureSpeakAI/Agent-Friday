"""Approval cards and notices raised in one test file do not crowd the next file's top bar.

Every test in one pytest process shares one temp home. Seat changes push a
"seat_change" needs-you notice, governed actions raise approval cards, and
some flows post FYIs; all of it lands in notifications.json / approvals.json,
which the top bar's Activity chip counts. On the sky gate (2026-10-15) the
61 API files before tests/api/test_workspace_job_card_ui.py left 34 of them on
the release PC (9 on a CI runner); the chip grew to its 300 px cap, the
Windows top bar folded it into its hidden overflow pull-out, and the job-card
test timed out clicking a chip that was drawn but not visible.

tests/conftest.py puts both stores back after every file. This runs files that
are known to leave cards and notices, then a clean-top-bar probe, in ONE
process, in that order, the way a worker would.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LEAVERS = [
    # an approved governed action: leaves a pending governed_action card + 2 updates
    "tests/api/test_mail_gmail_parity.py::test_an_approved_proposal_runs_the_same_change_once",
    # a substituted seat: leaves a seat_change needs-you notice
    "tests/api/test_seat_control_is_binding.py::test_a_substituted_seat_is_announced_in_the_turn",
    # a local-context share: leaves a pending local_context_share card
    "tests/api/test_local_context_route.py::test_edit_asks_once_more_when_the_check_would_change_it",
]
PROBE = "tests/api/test_top_bar_starts_clean.py"


def test_cards_and_notices_from_one_file_are_gone_in_the_next():
    # A clean child: its own temp home (tests/conftest.py keys it by PID) and
    # not mistaken for an xdist worker of this run.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST") and k != "PYTEST_CURRENT_TEST"}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *LEAVERS, PROBE,
         "-n", "0", "-p", "no:randomly", "-p", "no:cacheprovider", "-rA"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)
    out = r.stdout + r.stderr
    passed = set(re.findall(r"^PASSED (\S+)", out, flags=re.M))
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, flags=re.M)
    tail = "\n".join(out.strip().splitlines()[-30:])
    assert r.returncode == 0 and not failed, (
        "files that raise cards and notices, then the clean-top-bar probe, in "
        "one process, failed:\n%s\n\n%s" % ("\n".join(failed), tail))
    for node in LEAVERS:
        assert node in passed, "%s did not run and pass:\n%s" % (node, tail)
    assert any(n.startswith(PROBE + "::") for n in passed), "the probe did not run:\n%s" % tail

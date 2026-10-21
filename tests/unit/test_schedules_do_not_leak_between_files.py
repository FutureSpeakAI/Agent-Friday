"""A test file that arms a post does not leave the Content publisher on for the next file.

Every test in one pytest process shares one temp home. Arming a post through
the content routes switches the Content publisher job on in schedules.json
when the job is registered (services/publisher.ensure_publisher_running), and
a file that registers the builtins (publisher.start()) leaves them registered
for every later file on the worker. Without a restore, the next file on that
worker finds the job switched on.

tests/conftest.py puts schedules.json back after every file. This runs a file
that registers the builtins, a file that arms a post for the connected mock
platform, then a probe that the job is not on, in ONE process, in that order,
the way a worker would.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LEAVERS = [
    # registers content_publisher in scheduler.BUILTIN_TASKS for the process
    "tests/unit/test_content_publisher.py::test_start_registers_builtins",
    # posts now to the connected mock platform: switches the job on
    "tests/api/test_content_routes.py::test_analytics_refresh_polls_adapter",
]
PROBE = "tests/unit/test_content_publisher_off_in_a_fresh_file.py"


def test_a_publisher_switched_on_in_one_file_is_off_in_the_next():
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
        "a file that registers the builtins, a file that arms a post, then the "
        "publisher-off probe, in one process, failed:\n%s\n\n%s" % ("\n".join(failed), tail))
    for node in LEAVERS:
        assert node in passed, "%s did not run and pass:\n%s" % (node, tail)
    assert any(n.startswith(PROBE + "::") for n in passed), "the probe did not run:\n%s" % tail

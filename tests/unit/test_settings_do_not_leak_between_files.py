"""A seat picked in one test file does not route the next file's turns.

Every test in one pytest process shares one temp home, and the router reads
capability_routing.reasoning from its settings.json on every route(), taking
any model other than the factory one as the user's binding. So a file that
seats a model through the app and leaves it there routes every later file on
the same xdist worker: after the two files below, the resolver tests were
answered by claude-opus-5-5 (or the gateway id anthropic/claude-opus-5.5)
instead of the model each one asked for. With --dist loadfile, whether two
files share a worker depends on how many files there are, so the failure came
and went as files were added.

tests/conftest.py restores settings.json after every file. This runs the
pair in ONE process, in that order, the way a worker would.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SEAT_WRITERS = [
    # seats claude-opus-5-5 on `reasoning`
    "tests/api/test_local_only_seats_refuse_cloud.py::test_other_seats_are_not_restricted",
    # leaves `reasoning` on the gateway id anthropic/claude-opus-5.5
    "tests/api/test_seat_pick_takes_effect.py::"
    "test_a_cloud_gateway_id_is_not_asked_to_prove_it_is_installed",
]
RESOLVER = "tests/unit/test_routing_resolver.py"


def test_a_seat_bound_in_one_file_does_not_route_the_next():
    # A clean child: its own temp home (tests/conftest.py keys it by PID) and
    # not mistaken for an xdist worker of this run.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_XDIST") and k != "PYTEST_CURRENT_TEST"}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *SEAT_WRITERS, RESOLVER,
         "-n", "0", "-p", "no:randomly", "-p", "no:cacheprovider", "-rA"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)
    out = r.stdout + r.stderr
    passed = set(re.findall(r"^PASSED (\S+)", out, flags=re.M))
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, flags=re.M)
    tail = "\n".join(out.strip().splitlines()[-30:])
    assert r.returncode == 0 and not failed, (
        "a seat-binding test followed by the resolver tests, in one process, "
        "failed:\n%s\n\n%s" % ("\n".join(failed), tail))
    for node in SEAT_WRITERS:
        assert node in passed, "%s did not run and pass:\n%s" % (node, tail)
    assert any(n.startswith(RESOLVER + "::") for n in passed), \
        "the resolver tests did not run:\n%s" % tail

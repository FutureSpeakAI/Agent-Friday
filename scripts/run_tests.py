#!/usr/bin/env python
"""Run the test suite so that a failure is impossible to miss.

Why this exists (2026-08-19): sessions verify their work with

    pytest tests/ -q | tail -5

A shell pipeline exits with the status of its LAST command, so `tail`'s 0
replaces pytest's 1 and a suite with failures in it reports success. Several
sessions leaned on "suite green" claims produced exactly that way. The output
was right there; only the status lied.

Running through this script removes the trap three ways:

  * pytest's exit code is returned verbatim - no pipeline sits between it and
    the caller, so `run_tests.py; echo $?` is always the truth;
  * the verdict is printed as a LAST LINE that says PASS or FAIL in words, so
    a human or an agent reading `| tail -3` still sees it;
  * the failing test ids are re-printed after the summary, so piping to `tail`
    shows what broke rather than scrollback.

Usage:
    venv/Scripts/python.exe scripts/run_tests.py            # whole offline suite
    venv/Scripts/python.exe scripts/run_tests.py tests/unit # any pytest args
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv):
    env = dict(os.environ)
    env.setdefault("FRIDAY_TESTING", "1")          # inert imports, sandboxed home
    env.setdefault("PYTHONIOENCODING", "utf-8")

    cmd = [sys.executable, "-m", "pytest", *argv]
    print("$ " + " ".join(cmd), flush=True)

    proc = subprocess.run(cmd, cwd=str(ROOT), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace")
    out = proc.stdout or ""
    print(out, end="", flush=True)

    failures = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M)

    print("\n" + "=" * 68, flush=True)
    if failures:
        print(f"{len(failures)} FAILING TEST(S):", flush=True)
        for f in failures:
            print("   " + f, flush=True)
    # The verdict is the last line on purpose: `| tail -1` must still show it.
    if proc.returncode == 0 and not failures:
        print(f"RESULT: PASS  (pytest exit {proc.returncode})", flush=True)
    else:
        # Belt and braces: a non-zero code OR a FAILED line means failure. If
        # they ever disagree, trust the output and say so rather than pick one.
        if proc.returncode == 0 and failures:
            print("RESULT: FAIL  (pytest exited 0 but reported failures above "
                  "— trusting the output, not the status)", flush=True)
            return 1
        print(f"RESULT: FAIL  (pytest exit {proc.returncode})", flush=True)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

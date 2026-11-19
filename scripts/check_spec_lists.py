#!/usr/bin/env python3
"""Every app spec runs in CI, or says on its first lines why it does not.

The app-spec job runs the spec files it names (.github/workflows/tests.yml), so a spec that is
written but not named there never runs and cannot fail. #834's cap spec was exactly that.

A spec under tests/app/specs/ passes this check when the workflow names it, or when one of its
first 10 lines reads

    // ci-list: exempt <reason> until YYYY-MM-DD

(an exception lives on the line with its reason and an expiry; an expired one fails like a
missing one). Warn-only: exits 0 and prints each offender. `--enforce` exits 1 on any.

Stdlib only.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC_DIR = Path("tests/app/specs")
WORKFLOW = Path(".github/workflows/tests.yml")
EXEMPT = re.compile(r"//\s*ci-list:\s*exempt\s+(.+?)\s+until\s+(\d{4}-\d{2}-\d{2})\s*$")
SPEC_NAME = re.compile(r"[A-Za-z0-9_.-]+\.spec\.ts")


def named_by_workflow(text: str) -> set[str]:
    """The spec file names the workflow's commands name: whole names, and not those that appear only in a
    YAML comment (a line's text after its first ``#``)."""
    return {name for line in text.splitlines() for name in SPEC_NAME.findall(line.split("#", 1)[0])}


def offenders(root: Path, today: dt.date) -> list[str]:
    workflow = named_by_workflow((root / WORKFLOW).read_text(encoding="utf-8"))
    out = []
    for spec in sorted((root / SPEC_DIR).glob("*.spec.ts")):
        if spec.name in workflow:
            continue
        head = spec.read_text(encoding="utf-8", errors="replace").splitlines()[:10]
        found = next((m for m in map(EXEMPT.search, head) if m), None)
        if not found:
            out.append("%s is not run by CI and says nothing about why" % spec.name)
            continue
        try:
            expires = dt.date.fromisoformat(found.group(2))
        except ValueError:
            out.append("%s: its CI exemption has a date that is not a date (%s)" % (spec.name, found.group(2)))
            continue
        if expires < today:
            out.append("%s: its CI exemption (%s) expired %s" % (spec.name, found.group(1), found.group(2)))
    return out


def main(argv: list[str], root: Path = REPO, today: dt.date | None = None) -> int:
    bad = offenders(root, today or dt.date.today())
    for line in bad:
        print("[spec-list] WARN  " + line)
    if not bad:
        print("[spec-list] OK - every app spec is run by CI or exempt with a reason and an expiry")
    return 1 if (bad and "--enforce" in argv) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

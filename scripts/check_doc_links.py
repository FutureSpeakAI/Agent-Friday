#!/usr/bin/env python3
"""Static check: every relative Markdown link in the tracked tree resolves.

Scans every tracked ``*.md`` file for ``[text](target)`` links and fails when
a relative target (anything that is not ``http(s)://`` or ``mailto:``) does not
exist on disk. Anchors (``#section``) are stripped before the existence check;
anchor validity is not verified.

Exit 0 = every relative link resolves. Exit 1 = at least one does not.
Runs in well under a second and needs no imports from the application.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TAG = "[doc-links-check]"
LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(#[^)]*)?\)")


def tracked_markdown() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f.strip()]


def check() -> list[str]:
    missing: list[str] = []
    for rel in tracked_markdown():
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in LINK.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve() if not target.startswith("/") \
                else (ROOT / target.lstrip("/")).resolve()
            if not resolved.exists():
                missing.append(f"{rel}: {target}")
    return missing


def main() -> int:
    missing = check()
    if missing:
        print(f"{TAG} FAIL - {len(missing)} relative link(s) do not resolve:")
        for m in missing:
            print(f"  {m}")
        return 1
    print(f"{TAG} OK - every relative Markdown link resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())

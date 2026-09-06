#!/usr/bin/env python3
"""Static check: a built wheel carries the non-code files the package needs.

The bundled seed skills ship inside the package (``agent_friday/seed/``); the
``.py`` modules travel automatically, but their ``config.yaml`` and
``SKILL.md`` files only ship because ``[tool.setuptools.package-data]`` names
them. If that entry is lost, a wheel installs a career pipeline that cannot
work and nothing else notices. The same applies to the self-knowledge
documents the server loads at runtime.

Usage: python scripts/check_package_data.py dist/agent_friday-*.whl
Exit 0 = every required entry is present. Exit 1 = something is missing.
"""
from __future__ import annotations

import glob
import sys
import zipfile

TAG = "[package-data-check]"

REQUIRED_PATTERNS = (
    "agent_friday/seed/skills/application_engine/config.yaml",
    "agent_friday/seed/skills/application_engine/SKILL.md",
    "agent_friday/seed/skills/job_scanner/config.yaml",
    "agent_friday/seed/skills/job_scanner/SKILL.md",
    "agent_friday/SELF.md",
    "agent_friday/VOICE_DEMO.md",
)


def main(argv: list[str]) -> int:
    paths = [p for a in (argv or ["dist/*.whl"]) for p in glob.glob(a)]
    if not paths:
        print(f"{TAG} FAIL - no wheel found for {argv or ['dist/*.whl']}")
        return 1
    rc = 0
    for wheel in paths:
        with zipfile.ZipFile(wheel) as zf:
            names = set(zf.namelist())
        missing = [p for p in REQUIRED_PATTERNS if p not in names]
        if missing:
            rc = 1
            print(f"{TAG} FAIL - {wheel} is missing:")
            for m in missing:
                print(f"  {m}")
        else:
            print(f"{TAG} OK - {wheel} carries every required data file")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

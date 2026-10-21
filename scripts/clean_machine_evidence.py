#!/usr/bin/env python3
"""One evidence record for every clean-machine leg (north star §34.3-8).

Each leg of .github/workflows/clean-machine.yml uploads its RESULT.json
(packaging/windows/tests/clean-machine/verify-clean-machine.ps1) as an
artifact named ``clean-machine-<leg>``. The evidence job downloads them all into
one folder and runs this script, which writes one JSON record: the commit, each
leg's image, OS and checks, and for each §34.3 row the legs that prove it and
whether they passed. It exits 1 when an expected leg is missing, unreadable or
failed, so the record can never read "passed" over a leg that did not run.

Usage:
    python scripts/clean_machine_evidence.py --dir DIR --sha SHA --out FILE
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Every leg the workflow runs, and the §34.3 row each one proves.
LEGS = {
    "windows-2022": "NS-34.3-1",
    "windows-2025": "NS-34.3-1",
    "memory-cap": "NS-34.3-2",
    "locale": "NS-34.3-4",
    "standard-user": "NS-34.3-5",
}

ROW_TEXT = {
    "NS-34.3-1": "Clean-machine install on supported Windows versions",
    "NS-34.3-2": "Clean-machine: minimum RAM",
    "NS-34.3-4": "Clean-machine: non-English locale",
    "NS-34.3-5": "Clean-machine: restricted user account",
}


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def collect(folder: Path, sha: str) -> dict:
    """The record for the legs found under ``folder`` (one subfolder per leg)."""
    legs: dict = {}
    for name in LEGS:
        result = _read(folder / ("clean-machine-" + name) / "RESULT.json")
        if result is None:
            legs[name] = {"ok": False, "missing": True, "checks": {}}
            continue
        checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
        failed = sorted(k for k, v in checks.items() if not (isinstance(v, dict) and v.get("ok") is True))
        legs[name] = {"ok": bool(checks) and not failed, "missing": False,
                      "variant": result.get("variant"), "image": result.get("image"),
                      "os": result.get("os"), "build": result.get("build"),
                      "failed_checks": failed, "checks": checks}
    rows: dict = {}
    for name, row in LEGS.items():
        entry = rows.setdefault(row, {"requirement": ROW_TEXT[row], "legs": [], "ok": True})
        entry["legs"].append(name)
        entry["ok"] = entry["ok"] and legs[name]["ok"]
    return {"commit": sha, "ok": all(l["ok"] for l in legs.values()), "legs": legs, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)
    record = collect(a.dir, a.sha)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, leg in record["legs"].items():
        state = "missing" if leg.get("missing") else ("passed" if leg["ok"] else
                                                      "FAILED: " + ", ".join(leg["failed_checks"]))
        print("%-14s %s" % (name, state))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""One evidence record for every clean-machine leg (north star §34.3-8).

Each leg of .github/workflows/clean-machine.yml uploads an artifact named
``clean-machine-<leg>`` holding the check script's RESULT.json and LEG.json
(written by the workflow step: the leg, the runner image and the SHA-256 of the
setup program the leg installed). The evidence job downloads them all into one
folder and runs this script, which writes one JSON record: the commit, the
setup program, each leg's image, OS and checks, and for each §34.3 row the legs
that prove it and whether they passed.

A leg passes only when its RESULT.json and LEG.json are readable, it ran the
variant and the image it was meant to, every check it must run is present and
every check it ran passed, and it installed the same setup program as every
other leg (and the one named by --installer-sha256 when given). So the record
can never read "passed" over a leg that did not run, ran something else, or
skipped the check that proves its row.

The script exits 1 when the record does not pass, unless --report-only (a beta
tag is exempt from §34, amendment A19; the record still says what failed).

Usage:
    python scripts/clean_machine_evidence.py --dir DIR --sha SHA --out FILE
        [--installer-sha256 HEX] [--report-only]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Every leg the workflow runs: the row it proves, the variant it must run, the
#: runner image it must report (ImageOS), and the checks it must have run.
_FULL_INSTALL = ("install", "shortcuts", "apps", "first-run", "localhost", "health", "consent",
                 "setup-chat", "boot-errors", "uninstall", "reinstall")
_CLEAN_MACHINE = ("install", "health", "setup-chat", "boot-errors", "uninstall")
LEGS = {
    "windows-2022": {"row": "NS-34.3-1", "variant": "image", "image": "win22", "checks": _FULL_INSTALL},
    "windows-2025": {"row": "NS-34.3-1", "variant": "image", "image": "win25", "checks": _FULL_INSTALL},
    "memory-cap": {"row": "NS-34.3-2", "variant": "memory-cap", "image": None,
                   "checks": _CLEAN_MACHINE + ("memory",)},
    "locale": {"row": "NS-34.3-4", "variant": "locale", "image": None, "checks": _CLEAN_MACHINE + ("locale",)},
    "standard-user": {"row": "NS-34.3-5", "variant": "standard-user", "image": None,
                      "checks": _CLEAN_MACHINE + ("account",)},
}

ROW_TEXT = {
    "NS-34.3-1": "Clean-machine install on supported Windows versions (two Windows Server images; "
                 "client SKUs are not available as hosted runners)",
    "NS-34.3-2": "Clean-machine: minimum RAM (first boot, memory tier skipped, within a 4 GiB "
                 "commit budget with headroom; no cloud-use floor is documented)",
    "NS-34.3-4": "Clean-machine: non-English locale (Turkish user locale; the ANSI code page "
                 "stays 1252, which needs a reboot to change)",
    "NS-34.3-5": "Clean-machine: restricted user account (standard account, non-ASCII profile path)",
}


def _read(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _checks(result: dict) -> dict:
    """verify-clean-machine.ps1 nests its checks under "checks";
    verify-clean-install.ps1 writes them at the top level."""
    if isinstance(result.get("checks"), dict):
        return result["checks"]
    return {k: v for k, v in result.items() if isinstance(v, dict) and "ok" in v}


def collect(folder: Path, sha: str, installer_sha256: str = "") -> dict:
    legs: dict = {}
    hashes = set()
    for name, want in LEGS.items():
        base = folder / ("clean-machine-" + name)
        result, leg = _read(base / "RESULT.json"), _read(base / "LEG.json")
        if result is None or leg is None:
            legs[name] = {"ok": False, "missing": True, "problems": ["no RESULT.json or LEG.json"]}
            continue
        checks = _checks(result)
        problems = []
        if leg.get("leg") != name:
            problems.append("LEG.json names %r" % leg.get("leg"))
        if want["variant"] != "image" and result.get("variant") != want["variant"]:
            problems.append("ran variant %r, not %r" % (result.get("variant"), want["variant"]))
        # GitHub names image variants by suffix (win25-vs2026 is Windows
        # Server 2025 with a newer Visual Studio); the base must match exactly.
        img = str(leg.get("image") or "")
        if want["image"] and img.split("-", 1)[0] != want["image"]:
            problems.append("ran on image %r, not %r" % (leg.get("image"), want["image"]))
        missing = [c for c in want["checks"] if c not in checks]
        if missing:
            problems.append("checks not run: " + ", ".join(missing))
        failed = sorted(k for k, v in checks.items() if not (isinstance(v, dict) and v.get("ok") is True))
        if failed:
            problems.append("checks failed: " + ", ".join(failed))
        digest = str(leg.get("installer_sha256") or "").lower()
        if not digest:
            problems.append("no installer SHA-256")
        hashes.add(digest)
        legs[name] = {"ok": not problems, "missing": False, "problems": problems,
                      "variant": result.get("variant") or want["variant"], "image": leg.get("image"),
                      "os": result.get("os"), "installer_sha256": digest, "checks": checks}
    same = len({h for h in hashes if h}) <= 1
    expected = (installer_sha256 or "").lower()
    if not same or (expected and hashes - {expected}):
        for leg in legs.values():
            if not leg.get("missing"):
                leg["ok"] = False
                leg["problems"].append("the legs did not all install the same setup program"
                                       if not same else "installed a different setup program than the release build")
    rows: dict = {}
    for name, want in LEGS.items():
        entry = rows.setdefault(want["row"], {"requirement": ROW_TEXT[want["row"]], "legs": [], "ok": True})
        entry["legs"].append(name)
        entry["ok"] = entry["ok"] and legs[name]["ok"]
    return {"commit": sha, "installer_sha256": expected or (next(iter(hashes)) if same and hashes else ""),
            "ok": all(l["ok"] for l in legs.values()), "legs": legs, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--installer-sha256", default="")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    record = collect(a.dir, a.sha, a.installer_sha256)
    record["report_only"] = a.report_only
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, leg in record["legs"].items():
        print("%-14s %s" % (name, "passed" if leg["ok"] else "FAILED: " + "; ".join(leg["problems"])))
    if record["ok"]:
        return 0
    if a.report_only:
        print("REPORT ONLY (a beta: exempt from §34 by amendment A19): recorded, not failing")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

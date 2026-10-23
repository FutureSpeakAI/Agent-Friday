#!/usr/bin/env python3
"""The section 34 acceptance gate: no public non-beta release until the checklist is green.

North-star amendment A26 (the owner's ruling, 2026-10-23): the Astra roadmap is a hard
gate on any public 1.0. A version tag that is not a beta is refused unless every row of
the section 34 release-acceptance checklist (``docs/release/acceptance-34.json``) is
green. Betas are exempt (A19): ``vX.Y.Z-beta.N`` passes this gate whatever the
checklist says, and still goes through every other check of the release workflow.

Green means all of:

  * the checklist names exactly the section 34 rows below, each once;
  * ``verified_for`` is the version being released (results recorded for 1.0.0 do not
    carry over to 1.0.1);
  * every row is ``pass`` with its evidence named, or carries the exemption an amendment
    makes for that very row (A10 defers the signing rows, A11 moves the
    principal-isolation rows to the household release, A12 removes the pilot). The
    exemptions live here, in code, so editing the checklist alone cannot exempt a row.

Anything else refuses: a missing or unreadable file, a missing, repeated or unknown row,
an unknown status, a pass without evidence. Every refusal names its rows.

Exit codes: 0 green (or a beta), 1 refused, 3 usage error.
Standard library only: the release job runs on a bare ubuntu runner.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CHECKLIST = Path(__file__).resolve().parents[1] / "docs" / "release" / "acceptance-34.json"

#: How many rows each subsection of section 34 has (NS-34.<section>-1 .. -<count>).
SECTIONS = {1: 1, 2: 18, 3: 8, 4: 10, 5: 11, 6: 14, 7: 5, 8: 1, 9: 10, 10: 8,
            11: 3, 12: 2, 13: 4, 14: 2, 15: 4, 16: 4, 17: 8, 18: 1}
ROWS = tuple(f"NS-34.{s}-{n}" for s, count in SECTIONS.items() for n in range(1, count + 1))

#: The only exemptions: amendment -> (the status it gives, the rows it covers).
EXEMPTIONS = {
    "A10": ("deferred", frozenset({"NS-34.4-1", "NS-34.17-4"})),
    "A11": ("moved", frozenset({"NS-34.2-5", "NS-34.5-10", "NS-34.8-1",
                                "NS-34.9-10", "NS-34.17-2"})),
    "A12": ("removed", frozenset({"NS-34.18-1"})),
}
STATUSES = frozenset({"pass", "open", "fail"} | {s for s, _ in EXEMPTIONS.values()})

_TAG = re.compile(r"^v(\d+\.\d+\.\d+)(?:-([0-9A-Za-z.-]+))?$")
_BETA = re.compile(r"^beta\.\d+$")


def parse_tag(tag: str) -> tuple[str, bool]:
    """(version, is_beta) for a version tag. Only ``vX.Y.Z-beta.N`` is a beta; a release
    candidate, an alpha or any other suffix is not. Raises ValueError for a non-version tag."""
    tag = (tag or "").strip()
    if tag.startswith("refs/tags/"):
        tag = tag[len("refs/tags/"):]
    m = _TAG.match(tag)
    if not m:
        raise ValueError(f"not a version tag: {tag!r}")
    suffix = m.group(2) or ""
    return tag[1:], bool(_BETA.match(suffix))


def verdict(doc, version: str) -> tuple[bool, list[str]]:
    """(green, reasons) for releasing ``version`` on the checklist ``doc``."""
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return False, ["the checklist is not an object with a list of rows"]
    reasons: list[str] = []
    if str(doc.get("verified_for") or "").strip() != version:
        reasons.append(f"verified_for is {doc.get('verified_for')!r}, not {version!r}: "
                       "the results were not recorded for this version")
    seen: dict[str, int] = {}
    not_green: list[str] = []
    for row in doc["rows"]:
        rid = str((row or {}).get("id") or "") if isinstance(row, dict) else ""
        seen[rid] = seen.get(rid, 0) + 1
        if rid not in ROWS:
            reasons.append(f"unknown row {rid!r}")
            continue
        status = row.get("status")
        if status not in STATUSES:
            reasons.append(f"{rid}: unknown status {status!r}")
        elif status == "pass":
            if not str(row.get("evidence") or "").strip():
                reasons.append(f"{rid}: pass without evidence")
        elif status in ("open", "fail"):
            not_green.append(f"{rid} ({status})")
        else:
            amendment = row.get("amendment")
            allowed = EXEMPTIONS.get(amendment)
            if not allowed or allowed[0] != status or rid not in allowed[1]:
                reasons.append(f"{rid}: {status} under {amendment!r}, which no amendment allows")
    for rid in ROWS:
        if seen.get(rid, 0) == 0:
            reasons.append(f"{rid}: missing from the checklist")
        elif seen[rid] > 1:
            reasons.append(f"{rid}: listed {seen[rid]} times")
    if not_green:
        reasons.append(f"{len(not_green)} row(s) not passed: " + ", ".join(not_green))
    return not reasons, reasons


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag", required=True, help="the version tag being released (vX.Y.Z[-suffix])")
    ap.add_argument("--checklist", default=str(CHECKLIST))
    a = ap.parse_args(argv)
    try:
        version, beta = parse_tag(a.tag)
    except ValueError as e:
        print(f"release acceptance: {e}", file=sys.stderr)
        return 3
    if beta:
        print(f"release acceptance: {a.tag} is a beta, exempt from the section 34 checklist (A19)")
        return 0
    try:
        doc = json.loads(Path(a.checklist).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"release acceptance: REFUSED {a.tag}: the checklist cannot be read ({type(e).__name__})")
        return 1
    green, reasons = verdict(doc, version)
    if green:
        print(f"release acceptance: {a.tag}: every section 34 row passed or is exempted by its amendment")
        return 0
    print(f"release acceptance: REFUSED {a.tag}: a public non-beta release needs the section 34 "
          "checklist all green (A26)")
    for r in reasons:
        print(f"  - {r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

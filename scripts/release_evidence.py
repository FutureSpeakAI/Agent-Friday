#!/usr/bin/env python3
"""One record of what a release was proven on, attached to the release itself.

The installer workflow's release job downloads every leg's evidence (the fresh
install, the clean-machine legs, each upgrade base), the release gate's record of
the green test and CodeQL runs on the tag's commit, and the built setup program,
and writes ``release-evidence.json``. It refuses (exit 1, nothing written) when a
required leg left no result, when a leg's result records a failed check, when the
gate record is missing or not green for this commit, or when the setup program's
SHA-256 does not match its ``.sha256`` file. ``gh release create`` uploads the
record beside the installer, so anyone holding the release can see what it was
tested on without access to the Actions logs (which expire).

The record also says what was NOT covered, in words, so a reader does not take a
silence for a pass.

Layout read (``actions/download-artifact`` with a pattern, one folder per artifact)::

    <dir>/<artifact name>/RESULT.json     the leg's checks
    <dir>/<artifact name>/MACHINE.json    the machine it ran on (optional)

A check-shaped RESULT.json maps each check to ``{"ok": bool, "detail": str}`` and may
mark ``"skipped": true`` (a check the leg cannot honestly run, named in the record).
An upgrade rehearsal's RESULT.json is a summary; its own script judges it, and here it
must at least record a vault that still decrypted.

Standard library only: the release job runs on a bare ubuntu runner.
Exit codes: 0 written, 1 refused, 3 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{40}$")

#: Said in every record, so a gap is read as a gap.
NOT_COVERED = (
    "Minimum RAM: GitHub's Windows runners have about 16 GB and cannot be given less; "
    "no leg emulates a small machine. The runner's RAM is recorded per leg.",
    "System locale and display language: changing them needs a reboot a hosted runner "
    "cannot take. The locale leg changes the user's formats (culture) and puts the "
    "temporary folder under a non-ASCII name; the system code page stays English.",
    "Elevation prompts: the standard-user leg skips the local address (hosts file) step, "
    "which on a real PC asks an administrator; it is recorded as skipped, not passed.",
)


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        return {"__unreadable__": str(e)}


def judge_leg(result) -> tuple[bool | None, list[str], list[str], str]:
    """(passed, failed check names, skipped check names, shape) for one RESULT.json.
    passed is None when there is nothing here to judge (another job's evidence in a
    shape of its own: that job's exit code already gated the release)."""
    if result is None:
        return None, ["no RESULT.json"], [], "missing"
    if not isinstance(result, dict) or "__unreadable__" in result:
        return False, ["RESULT.json could not be read"], [], "unreadable"
    values = list(result.values())
    if values and all(isinstance(v, dict) and "ok" in v for v in values):
        failed = [k for k, v in result.items() if v.get("ok") is not True]
        skipped = [k for k, v in result.items() if v.get("skipped") is True]
        return (not failed), failed, skipped, "checks"
    # An upgrade rehearsal's summary: its own script judged it; the vault must still open.
    if "vault_decrypted" in result:
        ok = result.get("vault_decrypted") is True
        return ok, ([] if ok else ["vault_decrypted"]), [], "upgrade"
    return None, ["RESULT.json has no checks this record understands"], [], "unknown"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def assemble(evidence_dir: Path, gate: dict | None, sha: str, tag: str,
             installer: Path | None, require: list[str], require_prefix: list[str]):
    """(record, problems). The record is only to be written when problems is empty."""
    problems: list[str] = []
    sha = (sha or "").strip().lower()
    if not _SHA.match(sha):
        problems.append(f"not a full commit id: {sha!r}")

    if not isinstance(gate, dict):
        problems.append("no release gate record (the gate must write one on green)")
    else:
        if gate.get("verdict") != "green":
            problems.append(f"the gate record is not green: {gate.get('verdict')!r}")
        if str(gate.get("sha", "")).lower() != sha:
            problems.append(f"the gate record is for {gate.get('sha')!r}, not {sha}")

    inst = None
    if installer is not None:
        if not installer.is_file():
            problems.append(f"no setup program at {installer.name}")
        else:
            got = sha256_of(installer)
            side = installer.with_name(installer.name + ".sha256")
            stated = ""
            if side.is_file():
                words = side.read_text(encoding="utf-8-sig").split()
                stated = words[0].strip().lower() if words else ""
            if stated != got:
                problems.append(f"the setup program's SHA-256 {got} does not match its .sha256 file ({stated or 'missing'})")
            inst = {"file": installer.name, "sha256": got, "bytes": installer.stat().st_size}

    legs: dict[str, dict] = {}
    if evidence_dir.is_dir():
        for d in sorted(p for p in evidence_dir.iterdir() if p.is_dir()):
            result = _load(d / "RESULT.json")
            ok, failed, skipped, shape = judge_leg(result)
            machine = _load(d / "MACHINE.json")
            legs[d.name] = {"passed": ok, "shape": shape, "failed": failed, "skipped": skipped,
                            "machine": machine if isinstance(machine, dict) else None,
                            "result": result if isinstance(result, dict) else None}
            if ok is False:
                problems.append(f"{d.name}: " + "; ".join(failed))
    else:
        problems.append(f"no evidence folder at {evidence_dir}")

    for name in require:
        if name not in legs:
            problems.append(f"required leg {name} left no evidence")
        elif legs[name]["passed"] is None:
            problems.append(f"required leg {name}: " + "; ".join(legs[name]["failed"]))
    for prefix in require_prefix:
        mine = [n for n in legs if n.startswith(prefix)]
        if not mine:
            problems.append(f"no leg named {prefix}* left evidence")
        for n in mine:
            if legs[n]["passed"] is None:
                problems.append(f"required leg {n}: " + "; ".join(legs[n]["failed"]))

    record = {
        "kind": "agent-friday release evidence",
        "tag": tag,
        "commit": sha,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installer": inst,
        "release_gate": gate,
        "legs": legs,
        "skipped_checks": {n: l["skipped"] for n, l in legs.items() if l["skipped"]},
        "not_covered": list(NOT_COVERED),
    }
    return record, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="folder holding one sub-folder per evidence artifact")
    ap.add_argument("--gate", required=True, help="the release gate's --record file")
    ap.add_argument("--sha", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--installer", default="", help="the setup program (its .sha256 beside it)")
    ap.add_argument("--require", action="append", default=[], help="an artifact that must be present")
    ap.add_argument("--require-prefix", action="append", default=[],
                    help="at least one artifact whose name starts with this")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    gate = _load(Path(a.gate))
    record, problems = assemble(Path(a.dir), gate if isinstance(gate, dict) else None, a.sha, a.tag,
                                Path(a.installer) if a.installer else None,
                                a.require, a.require_prefix)
    for name, leg in record["legs"].items():
        mark = {True: "PASS", False: "FAIL", None: "NOTE"}[leg["passed"]]
        extra = f" (skipped: {', '.join(leg['skipped'])})" if leg["skipped"] else ""
        print(f"  [{mark}] {name}{extra}", flush=True)
    if problems:
        for p in problems:
            print(f"  {p}", flush=True)
        print("release evidence: REFUSED, nothing written", flush=True)
        return 1
    Path(a.out).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"release evidence: written to {a.out} ({len(record['legs'])} legs)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build or check a release's manifest: the file the update check verifies.

    python scripts/release_manifest.py build --version 1.2.0-beta.1 --out dist/release-manifest.json FILE...
    python scripts/release_manifest.py verify dist/release-manifest.json --key minisign.pub

`build` writes the version, the build sequence (src/agent_friday/release.py),
the release date, an expiry a year out and the SHA-256 of every file given.
It does not sign: the manifest is signed on an offline machine with
``minisign -Sm release-manifest.json -t "name=agent-friday-release version=<v>"``
and the .minisig is uploaded beside it (docs/development/release-signing.md).
`verify` checks a manifest against its .minisig and a public key, the same
check Friday's update check makes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPIRY_DAYS = 365


def build_sequence() -> int:
    text = (ROOT / "src" / "agent_friday" / "release.py").read_text(encoding="utf-8")
    m = re.search(r"^BUILD_SEQUENCE\s*=\s*([0-9_]+)", text, re.M)
    if not m:
        raise SystemExit("BUILD_SEQUENCE not found in src/agent_friday/release.py")
    return int(m.group(1).replace("_", ""))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(version: str, files: list, today: date | None = None) -> dict:
    today = today or date.today()
    return {"name": "agent-friday-release", "version": version.lstrip("vV"),
            "build_sequence": build_sequence(), "released": today.isoformat(),
            "expires": (today + timedelta(days=EXPIRY_DAYS)).isoformat(),
            "files": {Path(f).name: sha256(Path(f)) for f in files}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--version", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("files", nargs="+")
    v = sub.add_parser("verify")
    v.add_argument("manifest")
    v.add_argument("--key", required=True, help="a minisign public key file")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        manifest = build(a.version, a.files)
        Path(a.out).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print("wrote %s (%d file(s), build sequence %d, expires %s)" % (
            a.out, len(manifest["files"]), manifest["build_sequence"], manifest["expires"]))
        return 0
    sys.path.insert(0, str(ROOT / "src"))
    from agent_friday.services import minisign
    body = Path(a.manifest).read_bytes()
    sig = Path(a.manifest + ".minisig").read_text(encoding="utf-8")
    try:
        out = minisign.verify(body, sig, [Path(a.key).read_text(encoding="utf-8")])
    except minisign.SignatureError as e:
        print("NOT VERIFIED: %s" % e)
        return 1
    print("verified: key %s, trusted comment %r" % (out["key_id"], out["trusted_comment"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

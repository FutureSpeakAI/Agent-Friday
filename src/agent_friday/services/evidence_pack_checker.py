"""Check an evidence pack with the standard library only (no Friday, no owner key).

Run: python check_pack.py <pack.zip or unpacked folder> [--public-key <64 hex characters>]

It recomputes the SHA-256 of every file the manifest lists, then walks the hash chain over
ledger.jsonl (each entry's `prev` is the hash before it, each `hash` is the SHA-256 of the
entry without `hash` and `sig`), starting from the manifest's stated `prev`. When the optional
'cryptography' package is installed it also checks manifest.sig (Ed25519) against the key in
the manifest, or against the key you pass with --public-key, which is the check that does not
trust the pack's own word about who signed it. It says what it
checked and what it did not: the per-entry `sig` is an HMAC under a key that stays on the
owner's PC, so it is not checked here. Rules and the tests that hold them are in
tests/unit/test_evidence_pack.py.
"""
import hashlib
import importlib
import json
import sys
import zipfile
from pathlib import Path

MANIFEST = "manifest.json"
SIGNATURE = "manifest.sig"
LEDGER = "ledger.jsonl"
MANIFEST_PREFIX = b"asimov-evidence-pack/v1/manifest\x00"


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def entry_hash(entry):
    body = {k: v for k, v in entry.items() if k not in ("hash", "sig")}
    return hashlib.sha256(canon(body)).hexdigest()


def _read_all(source):
    path = Path(source)
    if path.is_dir():
        return {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def check(source, public_key=None):
    """(problems, notes): problems is empty when everything checked holds."""
    return check_files(_read_all(source), public_key)


def check_files(files, public_key=None):
    """The same check over {file name: bytes}; `public_key` pins who must have signed."""
    problems, notes = [], []
    if MANIFEST not in files:
        return [f"{MANIFEST} is missing"], notes
    manifest = json.loads(files[MANIFEST])
    for name, digest in sorted(manifest["files"].items()):
        if name not in files:
            problems.append(f"{name}: listed in the manifest but missing")
        elif hashlib.sha256(files[name]).hexdigest() != digest:
            problems.append(f"{name}: content does not match its recorded hash")
    for name in sorted(set(files) - set(manifest["files"]) - {MANIFEST, SIGNATURE}):
        problems.append(f"{name}: present but not listed in the manifest")
    notes.append(f"checked the SHA-256 of {len(manifest['files'])} files")
    problems += _walk_chain(files.get(LEDGER, b""), manifest)
    notes.append("walked the hash chain over ledger.jsonl from the stated starting hash")
    notes.append("did not check per-entry signatures: they use a key that stays on the owner's PC")
    problems += _check_signature(files, manifest, public_key, notes)
    return problems, notes


def _check_signature(files, manifest, pinned, notes):
    if SIGNATURE not in files:
        notes.append("the manifest is not signed in this pack")
        return ["manifest.sig: the pack is not signed but a signer was required"] if pinned else []
    key = pinned or manifest.get("public_key")
    if pinned and manifest.get("public_key") != pinned:
        return ["manifest.sig: the manifest names a different signing key than the one you gave"]
    try:
        ed = importlib.import_module("cryptography.hazmat.primitives.asymmetric.ed25519")
        failure = importlib.import_module("cryptography.exceptions").InvalidSignature
    except ImportError:
        notes.append("did not check manifest.sig: the 'cryptography' package is not installed")
        return []
    try:
        ed.Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
            bytes.fromhex(files[SIGNATURE].decode("ascii")), MANIFEST_PREFIX + files[MANIFEST])
    except (failure, ValueError, TypeError):
        return ["manifest.sig: the signature does not match manifest.json and the signing key"]
    notes.append("checked manifest.sig against " + ("the key you gave" if pinned
                                                    else "the key named in the manifest"))
    return []


def _walk_chain(raw, manifest):
    problems, prev, count = [], manifest["prev_hash"], 0
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    for number, line in enumerate(lines, start=1):
        try:
            entry = json.loads(line)
        except ValueError:
            problems.append(f"{LEDGER} line {number}: not valid JSON")
            continue
        count += 1
        if entry.get("prev") != prev:
            problems.append(f"{LEDGER} line {number}: does not follow the line before it")
        if entry_hash(entry) != entry.get("hash"):
            problems.append(f"{LEDGER} line {number}: content does not match its hash")
        prev = entry.get("hash")
    if count != manifest["entries"]:
        problems.append(f"{LEDGER}: holds {count} entries, the manifest says {manifest['entries']}")
    elif count and prev != manifest["head_hash"]:
        problems.append(f"{LEDGER}: last hash is not the manifest's head hash")
    return problems


def main(argv):
    if len(argv) not in (2, 4):
        print(__doc__)
        return 2
    pinned = argv[3] if len(argv) == 4 and argv[2] == "--public-key" else None
    problems, notes = check(argv[1], pinned)
    for note in notes:
        print("checked:", note)
    for problem in problems:
        print("PROBLEM:", problem)
    print("RESULT:", "FAILED" if problems else "everything checked holds")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

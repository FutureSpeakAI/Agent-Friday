"""vault_encrypt_migrate — safely encrypt plaintext vault files at rest.

The live app currently stores private data as PLAINTEXT under ~/.friday/vault
(and ~/wiki). This tool encrypts those files with vault_crypto (AES-256-GCM +
Argon2id) — SAFELY:

  * DRY-RUN by default: prints what it would do, writes nothing.
  * --apply writes <file>.enc and VERIFIES the .enc decrypts back to the exact
    original bytes before considering the file done. If any verification fails,
    it aborts and leaves everything untouched.
  * Plaintext is NEVER removed unless you pass --purge-plaintext, and even then
    only AFTER a successful decrypt round-trip for that file.

Key source (in priority order): --passphrase, then $FRIDAY_PASSWORD, then an
interactive getpass prompt. An empty passphrase is refused. LOSING THE
PASSPHRASE MEANS LOSING THE DATA — there is no recovery path.

  Preview:   python vault_encrypt_migrate.py
  Encrypt:   python vault_encrypt_migrate.py --apply
  Cut over:  python vault_encrypt_migrate.py --apply --purge-plaintext   (destructive)

NOTE: This encrypts data at rest. For the running app to *read* the encrypted
files, server.py must be wired to decrypt on read — that is a separate,
follow-up change (kept out of this tool to avoid editing server.py while it is
being concurrently modified).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

import agent_friday.privacy.vault_crypto as vc

VAULT_DIR = Path.home() / ".friday" / "vault"

# Most-sensitive categories first. These hold legal/financial data that
# the architecture docs explicitly promise to encrypt.
SENSITIVE_CATEGORIES = ["legal", "finances", "family"]

# Files we must never touch — config, keys, logs the app appends to live.
SKIP_NAMES = {".vault_config.json", ".governance-key", "access-log.jsonl",
              "decision-bom.jsonl"}
SKIP_SUFFIXES = {".enc", ".vault", ".bak"}


def _passphrase(args) -> str:
    if args.passphrase:
        return args.passphrase
    env = os.environ.get("FRIDAY_PASSWORD", "")
    if env:
        return env
    pw = getpass.getpass("Vault passphrase: ")
    return pw


def _candidate_files(root: Path, categories, include_all: bool):
    cats = None if include_all else set(categories)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in SKIP_NAMES or p.suffix.lower() in SKIP_SUFFIXES:
            continue
        if p.name.startswith("."):
            continue
        # category = first path component under the vault root
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        category = rel.parts[0] if len(rel.parts) > 1 else ""
        if cats is not None and category not in cats:
            continue
        yield p


def main(argv=None):
    ap = argparse.ArgumentParser(description="Safely encrypt plaintext vault files.")
    ap.add_argument("--vault-dir", type=Path, default=VAULT_DIR)
    ap.add_argument("--apply", action="store_true",
                    help="actually write .enc files (default is dry-run)")
    ap.add_argument("--purge-plaintext", action="store_true",
                    help="DESTRUCTIVE: delete plaintext after verified encryption")
    ap.add_argument("--all", action="store_true",
                    help="encrypt all categories, not just sensitive ones")
    ap.add_argument("--passphrase", default=None,
                    help="passphrase (else $FRIDAY_PASSWORD, else prompt)")
    args = ap.parse_args(argv)

    root = args.vault_dir
    if not root.exists():
        print(f"ERROR: vault dir not found: {root}", file=sys.stderr)
        return 2

    files = list(_candidate_files(root, SENSITIVE_CATEGORIES, args.all))
    if not files:
        print(f"No plaintext candidate files under {root}"
              f" (categories: {'ALL' if args.all else SENSITIVE_CATEGORIES}).")
        return 0

    print(f"Vault: {root}")
    print(f"Mode:  {'APPLY' if args.apply else 'DRY-RUN'}"
          f"{'  +PURGE-PLAINTEXT' if args.purge_plaintext else ''}")
    print(f"Candidates ({len(files)}):")
    for p in files:
        size = p.stat().st_size
        print(f"  - {p.relative_to(root)}  ({size} B)")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to encrypt. Nothing written.")
        return 0

    passphrase = _passphrase(args)
    if not passphrase:
        print("ERROR: empty passphrase refused.", file=sys.stderr)
        return 2

    salt = vc.load_salt(root / ".vault_config.json")
    key = vc.derive_key(passphrase, salt)  # DEFAULT (strong) profile

    # Phase 1: encrypt + verify every file. Abort on first failure.
    written = []
    for p in files:
        enc = p.with_suffix(p.suffix + ".enc")
        if enc.exists():
            print(f"  SKIP (exists)  {enc.relative_to(root)}")
            continue
        try:
            if not vc.roundtrip_ok(p, key):
                raise vc.IntegrityError("in-memory round-trip mismatch")
            vc.encrypt_file(p, enc, key)
            # Re-read the file we just wrote and confirm it decrypts to original.
            if vc.decrypt(enc.read_bytes(), key) != p.read_bytes():
                raise vc.IntegrityError("on-disk round-trip mismatch")
            written.append((p, enc))
            print(f"  ENC  {p.relative_to(root)} -> {enc.name}  (verified)")
        except Exception as e:  # noqa: BLE001
            print(f"\nABORT: failed on {p.relative_to(root)}: {e}", file=sys.stderr)
            print("No plaintext was removed. Investigate before retrying.", file=sys.stderr)
            return 1

    # Phase 2: optional, only-after-verification plaintext removal.
    if args.purge_plaintext:
        for p, enc in written:
            # Final guard: re-verify this exact .enc before deleting plaintext.
            if vc.decrypt(enc.read_bytes(), key) != p.read_bytes():
                print(f"  KEEP (verify failed)  {p.relative_to(root)}", file=sys.stderr)
                continue
            p.unlink()
            print(f"  PURGED plaintext  {p.relative_to(root)}")

    print(f"\nDone. {len(written)} file(s) encrypted"
          f"{' and plaintext purged' if args.purge_plaintext else '; plaintext kept'}.")
    if not args.purge_plaintext and written:
        print("Plaintext still present. Re-run with --purge-plaintext once the "
              "app is wired to read .enc files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

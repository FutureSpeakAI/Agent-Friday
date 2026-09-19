"""Move vault-encrypted files off the passphrase and onto Friday's keystore.

WHY THIS EXISTS. `vault_crypto` derives its key from a passphrase, and on
2026-09-19 this machine held two different ones - one in friday_startup.bat,
another in the Windows keychain - with the resolver preferring the keychain.
Eleven genuinely encrypted files were sealed under the launcher one and opened
with neither the current key nor anything a running Friday would try:

    co-parenting context, legal co-parenting context, Janet's profile,
    Stephen's profile, the family files, the VW knowledge base, finances,
    the job applications package, the 25-roles job search

Those are court material, a child, and a job hunt. They are not regenerable,
and the passphrase in that .bat file was the only thing in the world that
opened them.

WHAT THIS DOES. Re-seals each one under the keystore root key
(services/keystore.py): still encrypted at rest, no longer dependent on a
passphrase living in a batch file that half the launchers do not set. After
this runs the passphrase can be removed without losing anything.

THE RULES ARE THE MIGRATION RULES, because this is the most expensive data on
the machine:

  * decrypt with ANY key Friday knows - an older key is precisely what a
    stranded file is sealed with
  * round-trip the new ciphertext and compare BEFORE replacing anything
  * copy the original aside first
  * a file that will not open is left exactly as it is and reported, never
    deleted and never overwritten
  * already-migrated files are skipped, so a second run is free

Nothing here prints or returns file contents.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import agent_friday.privacy.vault_crypto as vc
from agent_friday.core import FRIDAY_DIR


def _backup_dir() -> Path:
    return Path(FRIDAY_DIR) / "security" / "pre-keystore-vault-backup"


def encrypted_files(root: Path | None = None) -> list:
    """Every file under the vault carrying the passphrase-key envelope.

    Found by reading the ENVELOPE, not by trusting the `.vault` extension -
    33 of the 44 files with that extension are not encrypted at all, and a
    migration that trusted the name would have rewritten plaintext as
    ciphertext and called it a fix.
    """
    root = Path(root or (Path(FRIDAY_DIR) / "vault"))
    out = []
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file() or _backup_dir() in p.parents:
            continue
        try:
            if p.open("rb").read(len(vc.MAGIC)).startswith(vc.MAGIC):
                out.append(p)
        except Exception:
            continue
    return out


def rekey_to_keystore(dry_run: bool = True, root: Path | None = None) -> dict:
    """Re-seal passphrase-encrypted vault files under the keystore root key."""
    from agent_friday.services import credential_store as cs
    from agent_friday.services import keystore as ks

    report = {"examined": 0, "rekeyed": 0, "recovered": [], "unreadable": [],
              "failed": [], "dry_run": bool(dry_run),
              "backup_dir": str(_backup_dir())}

    try:
        ks.root_key()
    except Exception as e:
        report["failed"].append({"path": "<keystore>",
                                 "error": "%s: %s" % (type(e).__name__, e)})
        return report

    keys = []
    try:
        cur = cs._vault_key()
        if cur:
            keys.append(("current", cur))
    except Exception:
        pass
    keys.extend(cs._legacy_keys())

    for path in encrypted_files(root):
        report["examined"] += 1
        try:
            blob = path.read_bytes()
        except Exception as e:
            report["failed"].append({"path": str(path), "error": str(e)})
            continue

        plain, via = None, None
        for name, key in keys:
            try:
                plain = vc.decrypt(blob, key)
                via = name
                break
            except Exception:
                continue
        if plain is None:
            report["unreadable"].append({"path": str(path)})
            continue
        if via != "current":
            report["recovered"].append({"path": str(path), "via": via})
        if dry_run:
            report["rekeyed"] += 1
            del plain
            continue
        try:
            fresh = ks.encrypt(plain)
            if ks.decrypt(fresh) != plain:
                raise RuntimeError("round-trip mismatch")
            bdir = _backup_dir()
            bdir.mkdir(parents=True, exist_ok=True)
            rel = path.relative_to(Path(root or (Path(FRIDAY_DIR) / "vault")))
            dest = bdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(fresh)
            tmp.replace(path)
            report["rekeyed"] += 1
        except Exception as e:
            report["failed"].append({"path": str(path),
                                     "error": "%s: %s" % (type(e).__name__, e)})
        finally:
            del plain
    return report

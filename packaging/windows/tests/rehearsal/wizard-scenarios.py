#!/usr/bin/env python3
"""The two upgrade scenarios the 5.6.6 wizard has to get right.

`wizard-enter-only.py` shows the behaviour changing across the fix. This script
shows that the fixed wizard distinguishes the two situations an upgrade can
actually land in, which is the part that matters once the fix is in:

    start.bat preserved (5.6.6 installer)   -> keeps the ORIGINAL passphrase.
                                               Verified against real ciphertext
                                               before being accepted. Vault opens.

    start.bat already destroyed (5.6.5)     -> returns nothing, and stops to
                                               explain. Does NOT mint a
                                               replacement, so the ciphertext
                                               stays recoverable if the
                                               passphrase turns up later.

The second row is the one worth staring at. "Return nothing" looks like a
failure and is the correct answer: the alternative -- generating a passphrase so
setup can report success -- is what made the data unrecoverable in the first
place. A wizard that always produces a passphrase is a wizard that can always
destroy a vault.

Both rows are driven with every prompt answered by pressing Enter, for the same
reason as the other script: that is the path a real upgrading user takes.

Requires 5.6.6 or later (it sets `w.VAULT_DIR`, which earlier versions lack).

    python packaging/windows/tests/rehearsal/wizard-scenarios.py

See README.md in this directory.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# packaging/windows/tests/rehearsal/ -> repo root
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

for _var in ("FRIDAY_PASSWORD", "FRIDAY_VAULT_PASSPHRASE"):
    os.environ.pop(_var, None)

from agent_friday import setup_wizard as w                     # noqa: E402
from agent_friday.privacy import vault_crypto as vc            # noqa: E402

ORIGINAL = "the-users-original-passphrase"   # pragma: allowlist secret

if not hasattr(w, "VAULT_DIR"):
    print("This script needs setup_wizard from v5.6.6 or later "
          "(no VAULT_DIR in the checked-out wizard).")
    raise SystemExit(2)

w.Prompt.ask = staticmethod(lambda *a, **kw: kw.get("default", ""))
w.Confirm.ask = staticmethod(lambda *a, **kw: kw.get("default", False))
w.console.print = lambda *a, **kw: None
w._clear = lambda *a, **kw: None


def scenario(name: str, start_bat_survives: bool) -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="friday-wizard-scen-"))
    vdir = tmp / ".friday" / "vault"
    vdir.mkdir(parents=True)
    app = tmp / "app"
    app.mkdir()
    w.VAULT_DIR = vdir
    w.VAULT_CONFIG = vdir / ".vault_config.json"
    w.PROJ_ROOT = app

    salt = os.urandom(32)
    (vdir / ".vault_config.json").write_text(
        json.dumps({"salt_hex": salt.hex()}), encoding="utf-8")
    (vdir / "note.enc").write_bytes(
        vc.encrypt(b"private note", vc.derive_key(ORIGINAL, salt)))

    if start_bat_survives:
        (app / "start.bat").write_text(
            "SET FRIDAY_PASSWORD=%s\r\n" % ORIGINAL,   # pragma: allowlist secret
            encoding="utf-8")

    got = w.step_vault_password(12, "")

    if got == ORIGINAL:
        returned, ok = "ORIGINAL", "YES"
        passed = True
    elif not got:
        returned = "<nothing>"
        ok = "n/a - old vault untouched, still recoverable"
        passed = True
    else:
        returned = "NEW RANDOM"
        try:
            vc.decrypt((vdir / "note.enc").read_bytes(), vc.derive_key(got, salt))
            ok = "YES"
        except Exception:
            ok = "NO - DATA LOST"
        passed = False

    print("  %-42s returned=%-11s vault opens=%s" % (name, returned, ok))
    return passed


def main() -> int:
    print()
    print("  Existing encrypted vault; every prompt answered with Enter.")
    print()
    results = [
        scenario("upgrade, start.bat preserved (5.6.6)", True),
        scenario("upgrade, start.bat already destroyed", False),
    ]
    print()
    if all(results):
        print("  >> Both correct. Neither path minted a passphrase over the vault.")
        return 0
    print("  >> FAILED: a passphrase was minted over an existing vault.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

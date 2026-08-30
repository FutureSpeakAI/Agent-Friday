#!/usr/bin/env python3
"""What pressing Enter through the setup wizard does to an EXISTING vault.

This is the demonstration half of the 5.6.6 vault-passphrase defect. It runs
against whatever `setup_wizard.py` is checked out, so it shows the behaviour
CHANGING across the fix rather than merely asserting the current behaviour:

    on v5.6.5 and earlier   ->  returns a NEW random passphrase.
                                The vault then fails to decrypt (IntegrityError).
                                That is silent, permanent data loss.

    on v5.6.6 and later     ->  returns '' (nothing).
                                The wizard stops and explains instead of minting
                                a passphrase over data it cannot open, so the old
                                ciphertext stays recoverable if the passphrase
                                turns up later.

WHY ENTER-ONLY. `step_vault_password` used to open with "Generate a random
passphrase for me?" defaulting to YES, and the Windows installer runs the wizard
on EVERY run, including every upgrade. So the input that destroyed vaults was not
a mistake anyone made -- it was the default, reached by pressing Enter. A demo
that typed careful answers would prove nothing about the case that actually hurt
people, which is why every prompt here returns its own default.

The vault here is real: a real Argon2id-derived key over real AES-256-GCM
ciphertext, written with the product's own `vault_crypto`. Only the location is
fake -- everything lives in a fresh temp directory.

    python packaging/windows/tests/rehearsal/wizard-enter-only.py

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

# Must be cleared BEFORE importing the wizard: 5.6.6's resolver reads them, and
# a developer machine very often has one set.
for _var in ("FRIDAY_PASSWORD", "FRIDAY_VAULT_PASSPHRASE"):
    os.environ.pop(_var, None)

from agent_friday import setup_wizard as w                     # noqa: E402
from agent_friday.privacy import vault_crypto as vc            # noqa: E402

ORIGINAL = "the-users-original-passphrase"   # pragma: allowlist secret


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="friday-wizard-demo-"))
    vdir = tmp / ".friday" / "vault"
    vdir.mkdir(parents=True)
    app = tmp / "app"
    app.mkdir()

    # 5.6.6+ resolves these; on 5.6.5 they do not exist and setattr is harmless.
    for name, value in (("VAULT_DIR", vdir),
                        ("VAULT_CONFIG", vdir / ".vault_config.json"),
                        ("PROJ_ROOT", app)):
        setattr(w, name, value)

    # A real vault, encrypted under a real passphrase, with the DEFAULT (strong)
    # Argon2id profile -- the one production uses (services/agent.py).
    salt = os.urandom(32)
    (vdir / ".vault_config.json").write_text(
        json.dumps({"salt_hex": salt.hex()}), encoding="utf-8")
    (vdir / "note.enc").write_bytes(
        vc.encrypt(b"private note", vc.derive_key(ORIGINAL, salt)))

    # The user presses Enter at every prompt.
    w.Prompt.ask = staticmethod(lambda *a, **kw: kw.get("default", ""))
    w.Confirm.ask = staticmethod(lambda *a, **kw: kw.get("default", False))
    w.console.print = lambda *a, **kw: None
    w._clear = lambda *a, **kw: None

    returned = w.step_vault_password(12, "")

    if not returned:
        verdict = "nothing returned - the wizard stopped instead of minting one"
        decrypts = "n/a (old ciphertext untouched and still recoverable)"
        destroyed = False
    else:
        try:
            vc.decrypt((vdir / "note.enc").read_bytes(),
                       vc.derive_key(returned, salt))
            decrypts = "YES"
            destroyed = False
        except Exception as e:
            decrypts = "NO - %s" % type(e).__name__
            destroyed = True
        verdict = ("the original passphrase, kept"
                   if returned == ORIGINAL else "a NEW passphrase")

    print()
    print("  wizard under test : %s" % (REPO / "src/agent_friday/setup_wizard.py"))
    print("  vault existed     : yes, encrypted under a known passphrase")
    print("  every prompt      : Enter (the default)")
    print()
    print("  wizard returned   : %s" % verdict)
    print("  vault decrypts    : %s" % decrypts)
    print()
    if destroyed:
        print("  >> DATA LOSS. A new passphrase was minted over a vault encrypted")
        print("     under a different one. This is the 5.6.5 behaviour.")
        return 1
    print("  >> No data loss.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

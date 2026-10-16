"""keystore — Friday's own root key for credentials at rest.

WHY THIS EXISTS. Credentials used to be encrypted with a key derived from
whatever `vault_passphrase.resolve()` happened to return, and that resolver
walks five sources in priority order: a human env var, the OS keychain, a
DPAPI-protected file, a launcher env var, and finally a scrape of start.bat.
Which source wins decides the key. So two processes on the same machine, started
two different ways, derive two different keys, and the one that did not write
the token cannot read it.

That is not a theory: an audit log can show thousands of "GCM auth tag
mismatch" failures a day (11,508 in one day is what this produces) and a
Google account that needs reconnecting every morning to clear something that
was never broken at Google. FRIDAY_PASSWORD is declared in friday_startup.bat
and in neither of the other two launchers.

TWO PROBLEMS, ONE ANSWER. The second problem is that two of those five sources
are the Windows credential store (keyring, and DPAPI), which makes Friday's
credentials depend on a platform Friday is not supposed to need. Friday has
its own credential store and does not depend on the Windows one.

So:

  * ONE LOCATION. `~/.friday/security/keystore.json`. Not an env var, not a
    keychain, not a file scraped out of a .bat. Every process finds the same
    key because there is only one place to look and it does not depend on how
    the process was started, what its working directory is, or which user-level
    service happens to be reachable.

  * A RANDOM ROOT KEY, NOT A DERIVED ONE. 32 bytes from `os.urandom`,
    generated once. Credentials are encrypted with the root key; the root key
    is what a passphrase protects. That indirection is what makes adding,
    changing or removing the passphrase cost one small rewrap instead of
    re-encrypting every credential on the machine - and re-encrypting every
    credential is exactly the operation you do not want to be halfway through
    when something goes wrong.

  * WRAPPED, OR NOT, AND IT SAYS WHICH. `wrap: "none"` keeps the root key in
    the file, protected by file permissions alone, which is what makes
    unattended start-up work. `wrap: "passphrase"` encrypts it with
    Argon2id(passphrase) so the file is useless on its own. Both are real
    positions and the user picks; what is not on offer is a store that claims
    one and does the other. `describe()` reports the truth for the UI.

  * NO SILENT DOWNGRADE. If the file says the key is wrapped and no passphrase
    is available, this raises. The old code fell through from vault to DPAPI to
    plaintext, and a credential store that quietly drops a tier is one you
    cannot reason about.

THE ROOT KEY IS NOT THE SOVEREIGN VAULT KEY. The vault keeps the user's own
documents and derives its key from their passphrase on purpose - that is a
different threat model with a human in the loop. This is machine-to-machine
credential material that has to survive an unattended boot. Sharing one key
between them is what forced credentials to inherit the vault's passphrase
problem in the first place.

Nothing here ever logs, prints or returns key material.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import agent_friday.core as core

try:
    import agent_friday.privacy.vault_crypto as _vc
    _HAS_VC = True
except Exception:                                  # pragma: no cover
    _vc = None
    _HAS_VC = False

KEYSTORE_PATH = core.FRIDAY_DIR / "security" / "keystore.json"

#: Envelope for blobs encrypted with the keystore root key. Distinct from
#: vault_crypto's own MAGIC so `unprotect` can tell "encrypted with the root
#: key" from "encrypted with the old passphrase-derived key" and read both.
MAGIC = b"FRIDAYKS\x01"

#: Written encrypted into the file so a passphrase can be checked without
#: decrypting anything that matters. A wrong passphrase must be a clean "wrong
#: passphrase", not a mystery failure three layers down inside a token read.
_CHECK_PLAINTEXT = b"friday-keystore-v1"

_LOCK = threading.RLock()
_CACHED_KEY: bytes | None = None


class KeystoreLocked(RuntimeError):
    """The root key is passphrase-wrapped and no correct passphrase is available."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _read_file() -> dict | None:
    if not KEYSTORE_PATH.exists():
        return None
    try:
        d = json.loads(KEYSTORE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _write_file(doc: dict) -> None:
    """Atomic write + hardened permissions.

    A half-written keystore is every credential on the machine, so the temp
    file is fsynced before it replaces the real one - the same pattern the
    conversation store uses, for much smaller stakes.
    """
    from agent_friday.services.credential_store import harden_permissions
    KEYSTORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEYSTORE_PATH.with_name(KEYSTORE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    try:
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception:
        pass
    harden_permissions(tmp)
    tmp.replace(KEYSTORE_PATH)
    harden_permissions(KEYSTORE_PATH)


def exists() -> bool:
    return KEYSTORE_PATH.exists()


def _passphrase() -> str:
    """The passphrase, for unwrapping only.

    Still routed through the existing resolver, because a user who already has
    a passphrase somewhere should not have to move it to keep working. The
    difference is that it is no longer what the credentials are encrypted
    with - it only unwraps the root key, and only when the store says it is
    wrapped. A resolver that returns different answers in different processes
    can now cost at most a clear "locked", never a silent wrong key.
    """
    try:
        from agent_friday.services import vault_passphrase as _vp
        return _vp.resolve()[0] or ""
    except Exception:
        return ""


def _derive(passphrase: str, salt: bytes) -> bytes:
    if not _HAS_VC:
        raise RuntimeError("cryptography is unavailable, so the keystore "
                           "cannot wrap or unwrap a root key")
    return _vc.derive_key(passphrase, salt)


def create(wrap: str = "none", passphrase: str = "") -> dict:
    """Create the keystore. Refuses to overwrite an existing one.

    Overwriting would mint a new root key and orphan every credential encrypted
    under the old one - unrecoverably, since the old key existed nowhere else.
    """
    with _LOCK:
        if KEYSTORE_PATH.exists():
            raise FileExistsError("a keystore already exists at %s" % KEYSTORE_PATH)
        root = os.urandom(32)
        doc = {"version": 1, "created": _now(), "cipher": "aes-256-gcm"}
        if wrap == "passphrase":
            if not passphrase:
                raise ValueError("a passphrase wrap needs a passphrase")
            salt = os.urandom(16)
            key = _derive(passphrase, salt)
            doc.update({
                "wrap": "passphrase",
                "kdf": "argon2id",
                "salt_hex": salt.hex(),
                "wrapped_key": _b64(_vc.encrypt(root, key)),
                "check": _b64(_vc.encrypt(_CHECK_PLAINTEXT, key)),
            })
        else:
            doc.update({"wrap": "none", "key": _b64(root)})
        _write_file(doc)
        global _CACHED_KEY
        _CACHED_KEY = root
        return describe()


def root_key(use_cache: bool = True) -> bytes:
    """The 32-byte root key, creating an unwrapped keystore on first use.

    CREATES ON FIRST USE, deliberately. The alternative is that a fresh install
    cannot write a credential until someone has run a setup step, which is the
    kind of gate that gets worked around with a plaintext fallback.
    """
    global _CACHED_KEY
    with _LOCK:
        if use_cache and _CACHED_KEY is not None:
            return _CACHED_KEY
        doc = _read_file()
        if doc is None:
            create(wrap="none")
            return _CACHED_KEY                      # set by create()
        wrap = doc.get("wrap") or "none"
        if wrap == "none":
            key = _unb64(doc.get("key") or "")
            if len(key) != 32:
                raise RuntimeError("the keystore is present but its root key is "
                                   "malformed; refusing to guess")
            _CACHED_KEY = key
            return key
        if wrap != "passphrase":
            raise RuntimeError("unknown keystore wrap %r" % wrap)
        pw = _passphrase()
        if not pw:
            raise KeystoreLocked(
                "Friday's keystore is passphrase-protected and no passphrase "
                "is available to this process. Credentials cannot be read or "
                "written until it is unlocked.")
        try:
            key = _derive(pw, bytes.fromhex(doc["salt_hex"]))
            if _vc.decrypt(_unb64(doc["check"]), key) != _CHECK_PLAINTEXT:
                raise ValueError("check block did not match")
            root = _vc.decrypt(_unb64(doc["wrapped_key"]), key)
        except KeystoreLocked:
            raise
        except Exception as e:
            # A WRONG PASSPHRASE SAYS SO. The whole failure this module exists
            # to end was a key mismatch surfacing as an unexplained crypto
            # error deep inside a token read, which then got filed as the
            # user's Google account being revoked.
            raise KeystoreLocked(
                "the passphrase available to this process does not unlock "
                "Friday's keystore (%s)" % type(e).__name__) from e
        if len(root) != 32:
            raise RuntimeError("the unwrapped root key is malformed")
        _CACHED_KEY = root
        return root


def set_wrap(wrap: str, passphrase: str = "") -> dict:
    """Add, change or remove the passphrase around the SAME root key.

    Credentials are untouched, because they are encrypted with the root key and
    the root key does not change here. That is the entire reason the root key
    is random rather than derived.
    """
    with _LOCK:
        root = root_key()
        doc = _read_file() or {}
        doc.update({"version": 1, "cipher": "aes-256-gcm",
                    "created": doc.get("created") or _now(),
                    "rewrapped": _now()})
        for k in ("key", "wrapped_key", "salt_hex", "kdf", "check"):
            doc.pop(k, None)
        if wrap == "passphrase":
            if not passphrase:
                raise ValueError("a passphrase wrap needs a passphrase")
            salt = os.urandom(16)
            key = _derive(passphrase, salt)
            doc.update({"wrap": "passphrase", "kdf": "argon2id",
                        "salt_hex": salt.hex(),
                        "wrapped_key": _b64(_vc.encrypt(root, key)),
                        "check": _b64(_vc.encrypt(_CHECK_PLAINTEXT, key))})
        elif wrap == "none":
            doc.update({"wrap": "none", "key": _b64(root)})
        else:
            raise ValueError("wrap must be 'none' or 'passphrase'")
        _write_file(doc)
        return describe()


def encrypt(data: bytes) -> bytes:
    return MAGIC + _vc.encrypt(data, root_key())


def decrypt(blob: bytes) -> bytes:
    if not is_keystore_blob(blob):
        raise ValueError("not a keystore blob")
    return _vc.decrypt(blob[len(MAGIC):], root_key())


def is_keystore_blob(blob: bytes) -> bool:
    try:
        return bytes(blob[:len(MAGIC)]) == MAGIC
    except Exception:
        return False


def describe() -> dict:
    """What the UI may say about the keystore. No key material."""
    doc = _read_file()
    if doc is None:
        return {"exists": False, "wrap": None, "unlocked": False,
                "path": str(KEYSTORE_PATH),
                "summary": "No keystore yet - one is created the first time a "
                           "credential is stored."}
    wrap = doc.get("wrap") or "none"
    unlocked = True
    detail = ""
    if wrap == "passphrase":
        try:
            root_key()
        except KeystoreLocked as e:
            unlocked, detail = False, str(e)
        except Exception as e:                      # pragma: no cover
            unlocked, detail = False, "%s: %s" % (type(e).__name__, e)
    return {
        "exists": True,
        "wrap": wrap,
        "unlocked": unlocked,
        "created": doc.get("created"),
        "rewrapped": doc.get("rewrapped"),
        "path": str(KEYSTORE_PATH),
        "detail": detail,
        "summary": (
            "Locked - " + detail if not unlocked else
            "Protected by your passphrase" if wrap == "passphrase" else
            "Protected by file permissions on this machine"),
    }


def _reset_cache_for_tests() -> None:
    global _CACHED_KEY
    _CACHED_KEY = None

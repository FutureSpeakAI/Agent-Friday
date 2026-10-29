"""
credential_store — encryption-at-rest for OAuth tokens and other secrets.

Multi-account Google support stores live access/refresh tokens on disk. Plaintext
JSON (the way the legacy single-account `google_token.json` is written) is the
exact thing an attacker who gets read access to the home directory would harvest
first. This module makes that impossible by protecting every credential blob with
Friday's own keystore (services/keystore.py), and nothing else:

  * **Writes: the keystore or nothing.** Every new secret is encrypted with the
    keystore's root key (AES-256-GCM). When the keystore cannot be used, for any
    reason (locked, its file unreadable or malformed, the cipher library
    missing), `protect()` fails closed: nothing is written, and the error says
    what happened and what to do. It never drops to another key. The older
    mechanisms (a key derived from the vault passphrase, Windows DPAPI) wrote
    each secret under a key that depended on how the process started, which is
    the defect the keystore exists to remove, and a write that quietly changes
    key is a secret the next process cannot read. There is no plaintext tier on
    any host; under FRIDAY_OS_MODE (the sealed kiosk image) the refusal carries
    the kiosk's own wording.
  * **Reads: every generation.** `unprotect()` still opens blobs the older
    mechanisms wrote (vault key, DPAPI) and a blob with no envelope (a credential
    an earlier version wrote in plain text), so the keystore migration can read
    them and rewrite them under the keystore.

Every blob is self-describing, so `unprotect()` always knows how it was written:
    FRIDAYVAULT\\x01 ...   -> vault AES-256-GCM   (vault_crypto magic)
    FRIDAYDPAPI\\x01 ...   -> Windows DPAPI
    {  (or anything else)  -> plaintext JSON

Nothing here ever logs, prints, or returns token material.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import agent_friday.core as core
from agent_friday.core.os_mode import is_os_mode
from agent_friday.user_errors import UserFacingRuntimeError

try:
    import agent_friday.privacy.vault_crypto as _vc
    _HAS_VC = True
except Exception:  # pragma: no cover - cryptography missing
    _vc = None
    _HAS_VC = False

# ── on-disk locations ────────────────────────────────────────────────────────
_VAULT_CONFIG_FILE = core.FRIDAY_DIR / "vault" / ".vault_config.json"
_SECURITY_DIR = core.FRIDAY_DIR / "security"
_CRED_AUDIT_LOG = _SECURITY_DIR / "credential_audit.jsonl"

# ── self-describing blob markers ─────────────────────────────────────────────
_DPAPI_MAGIC = b"FRIDAYDPAPI\x01"

# Derive the vault key lazily, exactly once.
_VAULT_KEY: bytes | None = None
_VAULT_KEY_READY = False
_WARNED_PLAINTEXT = False   # kept for callers and tests that reset it; nothing is written in plain text now

_PLAIN_REFUSAL = (
    "Friday could not lock this secret away, so it did not save it. Nothing was written to disk. "
    "Why: Friday's own key store (security/keystore.json in Friday's data folder) could not be "
    "used: {why}. Friday does not save a secret under any other key instead. To fix it, make sure "
    "Friday's data folder can be read and written, and if you protected the key store with a "
    "passphrase, unlock it (Settings > Privacy & Data > Security); then try again. "
    "`friday doctor` checks the key store."
)

#: Why the keystore could not be used, in words for the person (never its exception text).
_KEYSTORE_WHY = {
    "OSError": "its file could not be read or written",
    "PermissionError": "Friday is not allowed to read or write its file",
    "FileNotFoundError": "its folder is missing",
}


def _refusal(e: BaseException) -> "CredentialProtectionUnavailable":
    """The fail-closed refusal for a keystore that could not be used (anything but locked)."""
    why = _KEYSTORE_WHY.get(type(e).__name__, "its file is not one Friday can use")
    detail = ("keystore unavailable (%s); the credential was refused, not written under "
              "another key" % type(e).__name__)
    if is_os_mode():
        return CredentialProtectionUnavailable(
            _PLAIN_REFUSAL.format(why=why), detail=(
                "refusing to write a credential under FRIDAY_OS_MODE=1: Friday's key store could "
                "not be used (%s); nothing was written as PLAINTEXT or under any other key."
                % type(e).__name__))
    return CredentialProtectionUnavailable(_PLAIN_REFUSAL.format(why=why), detail=detail)


class CredentialProtectionUnavailable(UserFacingRuntimeError):
    """No encryption is available, so the secret was refused rather than stored in plain text.

    `user_message` is worded for the person and names the fix; `str(exc)` is the account for the
    log (under FRIDAY_OS_MODE, the kiosk's own wording). A RuntimeError, so every caller that
    already handles the OS-mode refusal handles this."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _friday_password() -> str:
    """The vault passphrase, from the ONE resolver.

    This used to read FRIDAY_PASSWORD from the environment and nothing else,
    which meant it could not see a passphrase stored by `friday vault-setup`
    (keychain only). The result was a working Sovereign Vault beside a
    credential store that had silently dropped a tier to DPAPI — invisible
    unless you inspected which cipher a blob had been written with.

    See services/vault_passphrase.py for the resolution order and why it is
    that order. Imported inside the function: vault_passphrase is the lower
    layer (it imports core and nothing from here), and a module-level import
    would still be evaluated before `core` finishes for some import paths.
    """
    from agent_friday.services import vault_passphrase as _vp
    return _vp.resolve()[0]


def _vault_key() -> bytes | None:
    """Derive the 32-byte AES key from FRIDAY_PASSWORD + the vault master salt.

    Mirrors services.agent._get_vault_key so credential blobs use the same key
    material as the rest of the Sovereign Vault. Returns None when encryption via
    a passphrase is unavailable (no password, or cryptography missing).
    """
    global _VAULT_KEY, _VAULT_KEY_READY
    if _VAULT_KEY_READY:
        return _VAULT_KEY
    _VAULT_KEY_READY = True
    pw = _friday_password()
    if not _HAS_VC or not pw:
        _VAULT_KEY = None
        return None
    try:
        salt_hex = vault_salt_hex(_VAULT_CONFIG_FILE)
        _VAULT_KEY = _vc.derive_key(pw, bytes.fromhex(salt_hex))
    except Exception as e:  # pragma: no cover - defensive
        print(f"[credstore] vault key derivation failed ({type(e).__name__}); blobs written "
              f"under the vault key cannot be read until it is.")
        _VAULT_KEY = None
    return _VAULT_KEY


# ── Windows DPAPI (per-user) ── the implementation now lives in one place ─────
# These were a second, byte-identical copy of the ctypes calls in
# services/vault_passphrase.py, which needs the same primitive to wrap the vault
# passphrase. Two copies of a crypto primitive is two things to get wrong, so
# vault_passphrase is the canonical home and these are thin aliases. The names
# are kept because callers and tests reference them.
from agent_friday.services.vault_passphrase import (  # noqa: E402
    dpapi as _dpapi,
    dpapi_available as _dpapi_available,
)


def protection_method() -> str:
    """What a write would do right now: "keystore" (encrypted with Friday's own key store),
    "locked" (the key store is wrapped and no passphrase opens it: writes are refused until it
    is unlocked) or "unavailable" (the key store cannot be used: writes are refused). Never an
    older tier: `protect` no longer writes with one."""
    from agent_friday.services import keystore as _ks
    try:
        _ks.root_key()
        return "keystore"
    except _ks.KeystoreLocked:
        return "locked"
    except Exception:
        return "unavailable"


def protect(data: bytes) -> tuple[bytes, str]:
    """Encrypt `data` with Friday's own keystore.

    Returns (blob, method). `method` is recorded in metadata for auditing; the
    blob itself is also self-describing so unprotect() never needs it.

    FRIDAY'S OWN STORE, FIRST AND NORMALLY ONLY. Everything new is
    written through `keystore`, which keeps one random root key in one
    file that every process finds the same way. The three mechanisms below it
    survive only so that blobs written by earlier versions can still be READ; see
    `unprotect`. Nothing new is written with them, because the whole defect was
    a store that picked a different key depending on how the process started
    and then reported the result as the user's Google account being revoked.

    The keystore raising is not a reason to drop a tier, whatever the reason
    (BD-08: fail closed on keystore errors). Locked, the honest outcome is a
    failure the user can act on; unreadable or malformed, the same: nothing is
    written, and the refusal says why in words. Writing under the vault key or
    DPAPI instead would put the secret under a key the next process may not
    derive, which is the original defect.
    """
    from agent_friday.services import keystore as _ks
    try:
        return _ks.encrypt(data), "keystore"
    except _ks.KeystoreLocked:
        # Fail closed and say why. Falling through here would write the
        # credential under a key the unlocked process cannot read, which is
        # the original bug with the sign flipped.
        raise
    except Exception as e:
        refusal = _refusal(e)
        print("[credstore] REFUSED: %s; nothing was written." % refusal, file=sys.stderr)
        raise refusal from None


def looks_protected(blob: bytes) -> str | None:
    """Which mechanism wrote this blob: 'vault' | 'dpapi' | None.

    None means the bytes carry no envelope — which `unprotect` treats as
    plaintext, correctly, because that is how a plaintext-host credential is
    stored. Callers that KNOW a value was encrypted need to tell those two
    cases apart: a blob that should be protected and is not is either corrupt
    or came from another machine, and passing it through as if it were the
    secret hands garbage to whatever consumes it.
    """
    try:
        from agent_friday.services import keystore as _ks
        if _ks.is_keystore_blob(blob):
            return "keystore"
        if _HAS_VC and _vc.is_encrypted(blob):
            return "vault"
        if blob[:len(_DPAPI_MAGIC)] == _DPAPI_MAGIC:
            return "dpapi"
    except Exception:
        pass
    return None


def unprotect(blob: bytes) -> bytes:
    """Inverse of protect(). Auto-detects the protection method from the blob.

    READS EVERY GENERATION. A credential written under DPAPI in August has to
    keep opening today, or "Friday has its own credential store now" would mean
    "reconnect everything" - which is the ritual this work exists to end. The
    migration in `migrate_to_keystore` rewrites them at leisure; until it runs,
    or for anything it could not touch, this reads them where they lie.
    """
    from agent_friday.services import keystore as _ks
    if _ks.is_keystore_blob(blob):
        return _ks.decrypt(blob)
    if _HAS_VC and _vc.is_encrypted(blob):
        key = _vault_key()
        if key is None:
            raise RuntimeError("credential is vault-encrypted but FRIDAY_PASSWORD is not set")
        return _vc.decrypt(blob, key)
    if blob[:len(_DPAPI_MAGIC)] == _DPAPI_MAGIC:
        out = _dpapi(blob[len(_DPAPI_MAGIC):], encrypt=False)
        if out is None:
            raise RuntimeError("DPAPI unprotect failed (wrong user/machine, or DPAPI unavailable)")
        return out
    # Plaintext (legacy / no-encryption host).
    return blob


def write_secret(path: Path, data: bytes, *, harden: bool = True) -> str:
    """Atomically write an encrypted secret to `path`, hardening its permissions.

    Returns the protection method used. The plaintext never touches disk
    unencrypted (the temp file holds the already-protected blob).

    ``harden=False`` skips the permission change, which on Windows is two
    ``icacls`` launches. It is for a file that exists for the length of one
    round trip and holds nothing (the health check's canary), never a secret.

    `protect()` is called before anything touches `path` (including creating
    its parent directory) so that a fail-closed raise (nothing can encrypt)
    leaves the filesystem completely untouched — not even an empty directory
    left behind.
    """
    path = Path(path)
    blob, method = protect(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp(path)
    tmp.write_bytes(blob)
    if harden:
        harden_permissions(tmp)
    tmp.replace(path)
    if harden:
        harden_permissions(path)
    return method


def vault_salt_hex(config_file: Path) -> str:
    """The vault's key-derivation salt, created once. Every reader derives the
    vault key from the salt that is on disk afterwards, so two first uses at
    once cannot leave things sealed under a salt nobody kept."""
    config_file = Path(config_file)

    def _read() -> dict:
        if not config_file.exists():
            return {}
        d = json.loads(config_file.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}

    cfg = _read()
    if cfg.get("salt_hex"):
        return str(cfg["salt_hex"])
    fresh = {"salt_hex": os.urandom(16).hex(), "kdf": "argon2id", "cipher": "aes-256-gcm"}
    if not config_file.exists():
        create_exclusive(config_file, json.dumps(fresh, indent=2).encode("utf-8"), harden=False)
    else:
        # a config without a salt (written by something else): add one in place
        cfg.update(fresh)
        tmp = _unique_tmp(config_file)
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(config_file)
    salt = _read().get("salt_hex")
    if not salt:
        raise RuntimeError("the vault configuration has no salt after creating one")
    return str(salt)


def write_secret_once(path: Path, data: bytes) -> bytes:
    """Mint-once for a secret: write `data` protected at `path` unless one is
    already there, and return the secret that is in force afterwards (ours,
    or the one another process or thread wrote first)."""
    path = Path(path)
    blob, _method = protect(data)
    if create_exclusive(path, blob):
        return data
    return read_secret(path, use=False)


def read_secret(path: Path, *, use: bool = True) -> bytes:
    """Read and decrypt a secret written by write_secret(). A successful read is noted as
    the credential's last use (its name and the time, never its value: `note_use`); a read
    that is not a use (a status check, the boot-time load, a re-encryption) passes use=False."""
    out = unprotect(Path(path).read_bytes())
    if use:
        note_use(path)
    return out


# ── when each credential was last used (BD-08, NS-26.5-4) ────────────────────
# Names and times only. Kept in security/credential_use.json, written at most once every
# _USE_FLUSH_S per credential, so a key read on every model call costs one disk write per
# ten minutes, not one per call.
_USE_FILE_NAME = "credential_use.json"
_USE_FLUSH_S = 600
_USE: dict = {}
_USE_FLUSHED: dict = {}


def _credential_name(path) -> str:
    """A credential's name: its path under Friday's data folder, with forward slashes."""
    # String work only (no filesystem lookup): the name is a label, never opened.
    here = os.path.abspath(os.fspath(path))
    home = os.fspath(core.FRIDAY_DIR)
    # the folder as written and as resolved (a path a caller resolved, e.g. Windows short names)
    for base in (os.path.abspath(home), os.path.realpath(home)):
        try:
            rel = os.path.relpath(here, base)
        except Exception:
            continue
        if not (rel.startswith("..") or os.path.isabs(rel)):
            return rel.replace(os.sep, "/")
    return os.path.basename(here)


def _use_file() -> Path:
    return _SECURITY_DIR / _USE_FILE_NAME


def _read_use() -> dict:
    try:
        doc = json.loads(_use_file().read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in (doc.get("last_used") or {}).items()}
    except Exception:
        return {}


_USE_LOCK = __import__("threading").Lock()


def note_use(path) -> None:
    """Record that the credential at `path` was just used. Never raises; never the value.
    Only files that are credentials count (not a health-check canary or other sealed data)."""
    try:
        import time as _t
        name = _credential_name(path)
        if _credential_kind(name) == "credential":
            return
        _USE[name] = _now_iso()
        now = _t.monotonic()
        with _USE_LOCK:
            if now - _USE_FLUSHED.get(name, -1e9) < _USE_FLUSH_S:
                return
            doc = {"last_used": {**_read_use(), **_USE}}
            _SECURITY_DIR.mkdir(parents=True, exist_ok=True)
            # one writer at a time in this process, and a name of its own across processes
            tmp = _use_file().with_name("%s.%d.tmp" % (_USE_FILE_NAME, os.getpid()))
            tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(_use_file())
            _USE_FLUSHED[name] = now
    except Exception:
        pass


def _credential_kind(name: str) -> str:
    if name.startswith("providers/keys/"):
        return "AI provider key"
    if name.startswith("google_accounts/"):
        return "Google account sign-in"
    if name.startswith("mcp_oauth/"):
        return "tool server sign-in"
    if name.startswith("platforms/"):
        return "publishing platform sign-in"
    if name == "google_token.json":
        return "Google sign-in (older format)"
    return "credential"


def inventory() -> list:
    """Every credential Friday holds, without a single value: what it is, whose it is, how it
    is protected, when it was last set or changed, and when it was last used. For Settings >
    Privacy & Data > Security (GET /api/security/credentials)."""
    used = {**_read_use(), **_USE}
    sets = {}
    for e in read_audit(category="provider_key", limit=5000):
        if e.get("event") == "set" and e.get("provider"):
            sets["providers/keys/%s.key" % e["provider"]] = e.get("ts")
    out = []
    for p in _credential_files():
        name = _credential_name(p)
        try:
            with open(p, "rb") as fh:
                blob = fh.read(64)                 # the envelope's mark is all that is needed
            st = p.stat()
        except OSError:
            continue
        changed = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()
        out.append({
            "name": name,
            "kind": _credential_kind(name),
            "owner": "you",
            "protection": looks_protected(blob) or "not encrypted (written by an older version)",
            "last_changed": sets.get(name) or changed,
            "last_used": used.get(name),
        })
    return out


def harden_permissions(path: Path) -> bool:
    """Restrict a file to the current user only. Best-effort, cross-platform.

    POSIX: chmod 0600. Windows: drop inheritance and grant only the current user
    via icacls (chmod alone can't express an ACL on NTFS). Returns whether the
    restriction was applied; a failure is logged (never the path's contents).
    """
    path = Path(path)
    ok = True
    try:
        os.chmod(path, 0o600)
    except Exception:
        ok = os.name == "nt"           # on Windows the ACL below is what counts
    if os.name == "nt":
        try:
            user = os.environ.get("USERNAME") or ""
            if not user:
                ok = False
            else:
                res = subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    capture_output=True, timeout=10, check=False,
                )
                ok = getattr(res, "returncode", 1) == 0
        except Exception:
            ok = False
    if not ok:
        import logging
        logging.getLogger("friday.credential_store").warning(
            "could not restrict a protected file to this user (%s)", path.name)
    return ok


def _unique_tmp(path: Path) -> Path:
    """A temp name beside `path` that no other process or thread will pick."""
    from agent_friday.secure_file import unique_tmp
    return unique_tmp(path)


def create_exclusive(path: Path, data: bytes, *, harden: bool = True) -> bool:
    """agent_friday.secure_file.create_exclusive, hardening with this module's
    harden_permissions: write `data` to `path` only if nothing is there yet,
    atomically; False when another process or thread got there first."""
    from agent_friday.secure_file import create_exclusive as _create
    return _create(path, data, harden_file=harden, harden_fn=lambda p: harden_permissions(p))


# ── migration onto Friday's own keystore ─────────────────────────────────────
#
# Every credential Friday holds, rewritten under the keystore root key so the
# machine stops depending on which launcher started the process.
#
# THIS CANNOT BE ALLOWED TO LOSE A CREDENTIAL. It is the operation with the
# worst downside in the codebase: a bad run means re-authorising every Google
# account, every MCP server, every platform and every provider key. So the
# rules are narrow and boring:
#
#   * A blob is only rewritten after it has been decrypted AND the new
#     ciphertext has been decrypted back and compared. Round-trip, not hope.
#   * Anything that will not decrypt is LEFT EXACTLY WHERE IT IS and reported.
#     A credential this process cannot read is not necessarily a dead one - it
#     may be readable by the process that wrote it - and deleting it would
#     destroy the only copy on the strength of a guess.
#   * The original bytes are copied aside before anything is replaced.
#   * Already-migrated blobs are skipped, so running it twice is free and
#     an interrupted run resumes.

#: Where the pre-migration bytes go. Kept rather than deleted: the whole point
#: of a backup is to exist on the day the clever new code is wrong.
def _migration_backup_dir() -> Path:
    return _SECURITY_DIR / "pre-keystore-backup"


def _credential_files() -> list[Path]:
    """Every path `write_secret` is known to write.

    Enumerated from the call sites rather than by globbing the home directory,
    so this cannot wander into a file that merely looks like a credential.
    """
    out: list[Path] = []
    # Imported from the modules that own them rather than rebuilt here. A
    # second copy of a path is a second thing to get wrong, and getting it
    # wrong means silently migrating nothing while reporting success.
    roots: list[tuple[Path, str]] = [
        (_PROVIDER_KEYS_DIR, "*.key"),
        (core.FRIDAY_DIR / "google_accounts" / "tokens", "*.token.enc"),
        (core.FRIDAY_DIR / "mcp_oauth", "*.oauth.enc"),
        (core.FRIDAY_DIR / "platforms", "*.cred"),
    ]
    legacy = core.FRIDAY_DIR / "google_token.json"
    if legacy.exists():
        out.append(legacy)
    for d, pat in roots:
        if not d.exists():
            continue
        for p in sorted(d.glob(pat)):
            if p.is_file() and not p.name.endswith(".tmp"):
                out.append(p)
    return out


def _legacy_keys() -> list[tuple[str, bytes]]:
    """Every key a credential on this machine could plausibly have been
    written with, newest-preference first.

    NOT PARANOIA. A machine can hold TWO different passphrases: one in
    friday_startup.bat and a different one in the Windows keychain.
    `vault_passphrase.resolve()` prefers the keychain, so every credential
    written before that keychain entry appeared becomes unreadable the moment
    it does - provider API keys and MCP OAuth tokens alike, silently,
    with the health surface reporting them as "no API key set" rather than as
    "we have your key and cannot open it".

    Migration is exactly the moment to reach for an older key. Reading with one
    is safe in a way that writing with one would not be: the plaintext is
    immediately re-encrypted under the keystore root key and the old ciphertext
    is kept as a backup.
    """
    out: list[tuple[str, bytes]] = []
    if not _HAS_VC:
        return out
    try:
        salt = _vc.load_salt(_VAULT_CONFIG_FILE)
    except Exception:
        try:
            cfg = json.loads(_VAULT_CONFIG_FILE.read_text(encoding="utf-8"))
            salt = bytes.fromhex(cfg["salt_hex"])
        except Exception:
            return out
    seen: set[bytes] = set()
    try:
        from agent_friday.services import vault_passphrase as _vp
        sources: list[tuple[str, str]] = []
        try:
            hv, hn, lv, ln = _vp._env_candidates()
            if hv:
                sources.append(("env:%s" % hn, hv))
            if lv:
                sources.append(("launcher:%s" % ln, lv))
        except Exception:
            pass
        for label, fn in (("os-keychain", _vp._from_keyring),
                          ("dpapi-file", _vp._from_dpapi_file),
                          ("start.bat", _vp._from_start_bat)):
            try:
                v = fn()
                if v:
                    sources.append((label, v))
            except Exception:
                continue
        # A passphrase a home held before store() replaced it is kept
        # (vault_passphrase.keep_displaced), because it may be the only key
        # for something sealed under it. It is a read key here like any other.
        try:
            sources.extend(_vp.kept_values())
        except Exception:
            pass
        for label, pw in sources:
            try:
                k = _vc.derive_key(pw, salt)
            except Exception:
                continue
            if k in seen:
                continue
            seen.add(k)
            out.append((label, k))
    except Exception:
        pass
    return out


def decrypt_any(blob: bytes) -> tuple[bytes, str]:
    """Public `_decrypt_any`, for migrating credentials that do NOT live in a
    file of their own.

    `mcp_servers.json` keeps its secrets as base64 inside JSON rather than as
    blobs on disk (services/connector_secrets.py), so it is invisible to
    `_credential_files()`. Missing it has a concrete cost: an MCP server whose
    token is stored there fails every spawn with "GCM auth tag mismatch" while
    file-based credentials recover, because its token is still sealed under the
    passphrase the resolver had stopped preferring.
    """
    return _decrypt_any(blob)


def _decrypt_any(blob: bytes) -> tuple[bytes, str]:
    """Plaintext for `blob` using whatever key opens it, and the key's label.

    Raises the ORIGINAL failure if nothing does, so the caller reports the real
    error rather than "tried five things".
    """
    try:
        return unprotect(blob), "current"
    except Exception as first:
        if not (_HAS_VC and _vc.is_encrypted(blob)):
            raise
        for label, key in _legacy_keys():
            try:
                return _vc.decrypt(blob, key), label
            except Exception:
                continue
        raise first


def migrate_to_keystore(dry_run: bool = False) -> dict:
    """Rewrite every credential under the keystore root key.

    Returns a report. Never raises for one bad credential - a single
    unreadable blob must not stop the other nineteen from being fixed.
    """
    import shutil
    from agent_friday.services import keystore as _ks

    report = {"examined": 0, "already": 0, "migrated": 0,
              "unreadable": [], "failed": [], "dry_run": bool(dry_run),
              "backup_dir": str(_migration_backup_dir())}

    try:
        _ks.root_key()
    except Exception as e:
        report["failed"].append({"path": "<keystore>", "error": "%s: %s"
                                 % (type(e).__name__, e)})
        return report

    backup = _migration_backup_dir()
    for path in _credential_files():
        report["examined"] += 1
        try:
            blob = path.read_bytes()
        except Exception as e:
            report["failed"].append({"path": str(path),
                                     "error": "unreadable file: %s" % e})
            continue
        if _ks.is_keystore_blob(blob):
            report["already"] += 1
            continue
        try:
            plain, via = _decrypt_any(blob)
        except Exception as e:
            # Left alone on purpose. See the rules above.
            report["unreadable"].append({"path": str(path),
                                         "error": type(e).__name__})
            continue
        if via != "current":
            # Worth reporting loudly: this credential was ALREADY broken for
            # everyday use and the migration is what rescued it.
            report.setdefault("recovered", []).append(
                {"path": str(path), "via": via})
        if dry_run:
            report["migrated"] += 1
            continue
        try:
            fresh = _ks.encrypt(plain)
            if _ks.decrypt(fresh) != plain:
                raise RuntimeError("round-trip mismatch")
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup / (path.name + ".bak"))
            harden_permissions(backup / (path.name + ".bak"))
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(fresh)
            harden_permissions(tmp)
            tmp.replace(path)
            harden_permissions(path)
            report["migrated"] += 1
        except Exception as e:
            report["failed"].append({"path": str(path),
                                     "error": "%s: %s" % (type(e).__name__, e)})
        finally:
            del plain
    audit_event("keystore", "migrate", migrated=report["migrated"],
                already=report["already"], unreadable=len(report["unreadable"]),
                failed=len(report["failed"]), dry_run=bool(dry_run),
                success=not report["failed"])
    return report


# ── audit trail ──────────────────────────────────────────────────────────────
def audit_event(category: str, event: str, **fields) -> None:
    """Append a credential-related audit entry as one JSONL line.

    NEVER pass token material in `fields` — callers log identifiers and outcomes
    only (account id, event, success, protection method).
    """
    entry = {"ts": _now_iso(), "category": category, "event": event}
    entry.update(fields)
    try:
        _SECURITY_DIR.mkdir(parents=True, exist_ok=True)
        with open(_CRED_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def read_audit(category: str | None = None, limit: int = 200) -> list:
    """Recent audit entries, newest last, optionally filtered by category."""
    if not _CRED_AUDIT_LOG.exists():
        return []
    out = []
    try:
        with open(_CRED_AUDIT_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if category and e.get("category") != category:
                    continue
                out.append(e)
    except Exception:
        pass
    return out[-limit:]


# ── Provider API keys (encrypted at rest, same mechanism as OAuth tokens) ─────
# Onboarding stores each AI provider's API key here, encrypted, instead of in
# plaintext settings.json. settings.json keeps only provider CONFIG (enabled,
# base_url) and a derived connected/missing status — never the secret.
_PROVIDER_KEYS_DIR = core.FRIDAY_DIR / "providers" / "keys"


def _provider_key_path(provider: str) -> Path:
    safe = "".join(c for c in str(provider) if c.isalnum() or c in "-_") or "provider"
    # contained() proves the joined path stays inside the keys folder (and is the check the code
    # scanner recognises): the name filter above already makes escaping impossible.
    from agent_friday.paths import contained as _contained
    return _contained(_PROVIDER_KEYS_DIR, f"{safe}.key")


def set_provider_key(provider: str, key: str) -> str:
    """Encrypt and store an API key for a provider. Returns the protection method.
    The key value is never logged, printed, or echoed back."""
    method = write_secret(_provider_key_path(provider), (key or "").encode("utf-8"))
    audit_event("provider_key", "set", provider=provider, method=method, present=bool(key))
    return method


def get_provider_key(provider: str, *, use: bool = True) -> str | None:
    """Decrypt and return a stored provider key, or None if absent/unreadable. `use=False` for a
    read that is not a use of the key (the boot-time load, a re-encryption)."""
    path = _provider_key_path(provider)
    if not path.exists():
        return None
    try:
        return read_secret(path, use=use).decode("utf-8")
    except Exception:
        return None


def provider_key_status(provider: str) -> str:
    """'connected' if a key is stored AND decrypts; 'present_but_unreadable'
    if a key file exists but cannot be decrypted (wrong/rotated machine key,
    a vault passphrase change, disk corruption); 'missing' if no key file
    exists at all.

    Existence of the key file is not enough: reporting 'connected' from
    _provider_key_path(...).exists() alone, without attempting the decrypt
    read_secret() itself does, produces a status display that lies about
    undecryptable keys. 'Present but unreadable' is reachable and real, not
    hypothetical: a key written on one machine (or before a DPAPI/keychain-
    backing key rotated) does not necessarily decrypt on another, or after.
    """
    path = _provider_key_path(provider)
    if not path.exists():
        return "missing"
    try:
        unprotect(path.read_bytes())       # a status check is not a use (read_secret notes use)
        return "connected"
    except Exception:
        return "present_but_unreadable"


def delete_provider_key(provider: str) -> bool:
    path = _provider_key_path(provider)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            return False
        audit_event("provider_key", "delete", provider=provider)
        return True
    return False


def list_provider_keys() -> list:
    """Provider names that have a stored key (identifiers only — no key material)."""
    if not _PROVIDER_KEYS_DIR.exists():
        return []
    return [p.stem for p in _PROVIDER_KEYS_DIR.glob("*.key")]


# ── Stale-vault-key recovery ────────────────────────────────────────────────
#
# THE FAILURE THIS EXISTS FOR: the vault passphrase's durable homes
# (services/vault_passphrase.py — the OS keychain, keyed by the FIXED,
# machine-wide constants KEYRING_SERVICE/KEYRING_ACCOUNT, not scoped to any
# particular install or HOME) can be overwritten by anything that calls
# vault_passphrase.store() with a different value -- most often a rehearsal
# or test run against a redirected HOME, which isolates ~/.friday but NOT the
# keychain. When that happens,
# every process derives a NEW vault key from then on, and any secret
# encrypted under the OLD one becomes permanently unreadable to every FUTURE
# process — except the one process, if any, that resolved the OLD passphrase
# before the change and has kept running since. That process's cache is the
# ONLY surviving copy, and it dies with the process: a crash, a reboot, or
# (bitterly) the very restart that would load this fix.
#
# WHAT THIS CANNOT DO: rescue a secret once the process that could decrypt it
# has already stopped. That is unrecoverable by construction — the same
# design that guarantees a stored key can never be extracted through this
# app's own API is exactly what makes it unextractable here too, once the
# one process that held it is gone. This only helps when it is used from the
# process that is still running.
#
# WHAT THIS DOES: give a currently-running process a way to save whatever it
# CAN still decrypt, under whatever a brand-new process would derive right
# now — so the secret survives that process's own eventual restart, which is
# the one restart it can still get ahead of. A plaintext value is read once,
# in this process, and is re-encrypted in this process; only ciphertext ever
# touches disk, and the verification step below hashes rather than compares
# a decrypted value directly, so no plaintext crosses a process boundary to
# be checked either.

def _derive_fresh_vault_key() -> bytes | None:
    """The vault key a BRAND-NEW process would derive right now.

    Deliberately bypasses every cache: `vault_passphrase.resolve(use_cache=
    False)` re-reads the keychain/DPAPI-file/environment chain from scratch
    rather than returning whatever this process resolved earlier (which may
    itself be the stale value we are trying to move away from), and this
    function never touches `_VAULT_KEY`/`_VAULT_KEY_READY` — this process's
    own cache, which may hold the OLD key and must keep holding it for
    everything else `credential_store` does in this same run, is left alone.

    Returns None when there is nothing to derive against (no resolvable
    passphrase, cryptography unavailable, or no salt has ever been
    established — that last case means nothing has ever been vault-encrypted
    on this machine, so there is no target key to reconcile toward).
    """
    if not _HAS_VC:
        return None
    from agent_friday.services import vault_passphrase as _vp
    pw, _source = _vp.resolve(use_cache=False)
    if not pw:
        return None
    if not _VAULT_CONFIG_FILE.exists():
        return None
    try:
        cfg = json.loads(_VAULT_CONFIG_FILE.read_text(encoding="utf-8"))
        salt_hex = cfg.get("salt_hex")
        if not salt_hex:
            return None
        return _vc.derive_key(pw, bytes.fromhex(salt_hex))
    except Exception:
        return None


def _verify_reencrypted_blob(path: Path, expected_sha256: str) -> bool:
    """Prove a FRESH, SEPARATE process — not this one — can decrypt `path`
    and that it recovers the expected content, without that content ever
    being printed, logged, or returned. Only a hash crosses the process
    boundary, compared here against a hash of the original that this
    process already computed from the plaintext it holds; the two
    plaintexts are never brought together anywhere to be diffed.

    Returns False (never raises) on any failure to spawn, decrypt, or match
    — a verification step that cannot itself confirm success must be
    treated as a failure to verify, not as a pass.
    """
    src_dir = str(Path(__file__).resolve().parents[2])
    script = (
        "import sys, hashlib\n"
        f"sys.path.insert(0, {src_dir!r})\n"
        "from agent_friday.services.credential_store import read_secret\n"
        "try:\n"
        "    val = read_secret(sys.argv[1], use=False)\n"
        "    print(hashlib.sha256(val).hexdigest())\n"
        "except Exception:\n"
        "    print('VERIFY_FAILED')\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return False
    # LAST line, not the whole of stdout: `agent_friday.core`'s own import
    # prints a "[FRIDAY] Loaded N environment variable(s)..." banner to
    # STDOUT (core/__init__.py, ~line 873) as a side effect of importing it
    # at all -- unrelated to this script, harmless, and not something to
    # suppress globally for one caller. Our own `print(...)` is always the
    # final line the child writes, banner or not.
    lines = (proc.stdout or "").strip().splitlines()
    out = lines[-1].strip() if lines else ""
    return bool(out) and out == expected_sha256


def reencrypt_stale_provider_keys(names: list[str] | None = None) -> dict:
    """Re-encrypt every readable stored provider key under the vault key a
    FRESH process would derive right now, so it survives this process's own
    next restart. See the module note above for what this can and cannot do.

    `names` restricts the run to specific providers; default is every key in
    `list_provider_keys()`. Backs up `~/.friday/vault/` and
    `~/.friday/providers/keys/` once, before touching anything, into a
    timestamped sibling directory — so a mistake here costs nothing that
    was recoverable before this ran.

    Per key, atomically: read the plaintext THIS process can still decrypt;
    if a fresh key cannot even be derived, stop entirely (nothing has been
    touched yet) and say so; otherwise encrypt under the fresh key, write to
    a temp file, and only replace the real file once a genuinely separate
    subprocess has proven — by hash, never by value — that IT can decrypt
    the temp file back to the same content. A key that fails any step is
    left untouched and reported; it does not block the others.

    Returns {"recovered": [names], "unchanged": [names], "failed":
    [{"name", "reason"}], "backup_dir": str|None}.
    """
    import hashlib
    import shutil
    import time

    result = {"recovered": [], "unchanged": [], "failed": [], "backup_dir": None}

    fresh_key = _derive_fresh_vault_key()
    if fresh_key is None:
        result["failed"].append({
            "name": "*", "reason": ("could not derive a fresh vault key -- no "
                                     "resolvable passphrase, or no vault salt "
                                     "has ever been established; nothing was "
                                     "read or written")})
        return result

    targets = names if names is not None else list_provider_keys()
    if not targets:
        return result

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = core.FRIDAY_DIR / "backups" / f"vault-reencrypt-{stamp}"
    try:
        for sub in ("vault", "providers/keys"):
            src = core.FRIDAY_DIR / sub
            if src.exists():
                dst = backup_root / sub
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst)
        result["backup_dir"] = str(backup_root)
    except Exception as e:
        result["failed"].append({
            "name": "*", "reason": f"backup failed, aborting before any write: {e}"})
        return result

    for name in targets:
        real_path = _provider_key_path(name)
        try:
            # A key already under Friday's own keystore is where it should be: rewriting it
            # under the vault key would move it back to the key-per-process scheme the keystore
            # replaced (BD-08: the keystore is the only write tier).
            from agent_friday.paths import contained as _contained
            held = _contained(_PROVIDER_KEYS_DIR, real_path.name)     # only inside the keys folder
            if held.exists() and looks_protected(held.read_bytes()) == "keystore":
                result["unchanged"].append(name)
                continue
        except (OSError, ValueError):
            pass
        old_plain = None
        try:
            old_plain = get_provider_key(name, use=False)
        except Exception:
            pass
        if old_plain is None:
            result["failed"].append({
                "name": name,
                "reason": "this process cannot decrypt it either -- already lost"})
            continue

        old_hash = hashlib.sha256(old_plain.encode("utf-8")).hexdigest()
        try:
            new_blob = _vc.encrypt(old_plain.encode("utf-8"), fresh_key)
        except Exception as e:
            result["failed"].append({"name": name, "reason": f"encrypt failed: {e}"})
            continue

        # Does the FRESH key already decrypt the file on disk? Checked by
        # decrypting with `fresh_key` explicitly -- never via read_secret()/
        # _vault_key(), which would use THIS process's own (possibly stale)
        # cached key and trivially "match" against itself regardless of
        # whether the file is actually fresh-readable.
        try:
            already_fresh = (_vc.decrypt(real_path.read_bytes(), fresh_key)
                             == old_plain.encode("utf-8"))
        except Exception:
            already_fresh = False
        if already_fresh:
            result["unchanged"].append(name)
            continue

        tmp_path = real_path.with_name(real_path.name + f".reencrypt-{stamp}.tmp")
        try:
            tmp_path.write_bytes(new_blob)
            harden_permissions(tmp_path)
        except Exception as e:
            result["failed"].append({"name": name, "reason": f"temp write failed: {e}"})
            continue

        if _verify_reencrypted_blob(tmp_path, old_hash):
            try:
                tmp_path.replace(real_path)
                harden_permissions(real_path)
                result["recovered"].append(name)
                audit_event("provider_key", "reencrypt", provider=name,
                           method="vault", present=True)
            except Exception as e:
                result["failed"].append({
                    "name": name,
                    "reason": f"verified but the atomic swap itself failed: {e}"})
        else:
            try:
                tmp_path.unlink()
            except Exception:
                pass
            result["failed"].append({
                "name": name,
                "reason": ("a fresh subprocess could not decrypt the re-"
                          "encrypted blob back to the same content -- "
                          "original left untouched")})

    return result


def _env_key_for_provider(provider: str) -> str | None:
    """The environment variable a provider's auth expects (from the registry)."""
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        p = get_provider_registry().get_provider(provider)
        auth = (p or {}).get("auth") or {}
        if auth.get("type") == "env_var":
            return auth.get("key")
    except Exception:
        pass
    return None


def _came_from_a_launch_script(env_key: str) -> bool:
    """Did THIS process put that value there, reading start.bat?

    Fails closed: if core cannot tell us, treat the value as the user's own
    and leave it alone. Trampling an environment variable someone set
    deliberately would be this same bug pointed the other way.
    """
    try:
        return env_key in (getattr(core, "ENV_FROM_LAUNCH_SCRIPTS", None) or set())
    except Exception:
        return False


def bootstrap_provider_env() -> int:
    """Decrypt stored provider keys into os.environ under the env var each provider's
    auth expects, so is_provider_available() and the SDK clients see them. Called at
    server boot, after the launch-script bootstrap. Returns the number loaded.

    A KEY SAVED IN SETTINGS BEATS ONE START.BAT PUT THERE.

    This used to read "never overrides a key already set in the environment",
    and start.bat wins the race -- `_bootstrap_env_from_launch_scripts` runs at
    package import, this runs at server boot. So the stored key was skipped on
    every boot, and a key swapped in Settings survived exactly until restart:
    `hot_reload_provider_key` sets os.environ and core.ANTHROPIC_API_KEY live,
    then the next launch put the dead one back in front of it with the panel
    still reporting "connected".

    `provider_api_key()` reads the store first, which covers the probe and the
    openai-compatible dispatch but NOT `core.get_anthropic_client()` -- which
    reads os.environ and settings.json and does not consult the store at all.
    This is the half that reaches the reader that matters.

    A genuine system environment variable still wins. Someone who sets
    ANTHROPIC_API_KEY in Windows has done a deliberate thing and knows what it
    means; only the value we ourselves loaded out of a .bat file gives way.

    Kept as an int-returning wrapper around bootstrap_provider_env_detail()
    so existing callers/tests keep their exact contract -- server.py's
    boot log uses the detailed version directly instead.
    """
    return bootstrap_provider_env_detail()["loaded"]


def bootstrap_provider_env_detail() -> dict:
    """Same decryption pass as bootstrap_provider_env(), returning the full
    picture instead of a bare success count: how many provider key files
    exist on disk at all ('candidates'), how many of those actually
    decrypted into the environment ('loaded'), and which provider names
    exist but failed to decrypt ('unreadable').

    bootstrap_provider_env()'s 'loaded' count only counts real decrypt
    successes, and a boot log printing that ONE bare number has no
    denominator -- if 3 of 5 stored keys fail to decrypt, "loaded 2 from
    encrypted store" says nothing about the 3 that exist but are broken. The
    denominator and the unreadable names are what make the failure visible.
    """
    loaded = 0
    candidates = 0
    unreadable: list[str] = []
    for provider in list_provider_keys():
        env_key = _env_key_for_provider(provider)
        if not env_key:
            continue
        candidates += 1
        if os.environ.get(env_key) and not _came_from_a_launch_script(env_key):
            continue
        val = get_provider_key(provider, use=False)
        if val:
            os.environ[env_key] = val
            loaded += 1
        else:
            unreadable.append(provider)
    if loaded:
        audit_event("provider_key", "bootstrap_env", count=loaded)
    return {"loaded": loaded, "candidates": candidates, "unreadable": unreadable}


def hot_reload_provider_key(provider: str, key: str) -> None:
    """Set the provider's env var live and reset any cached SDK client so a newly
    stored key takes effect without a restart. Mirrors the well-known globals core
    reads directly (Anthropic / Gemini)."""
    env_key = _env_key_for_provider(provider)
    if env_key and key:
        os.environ[env_key] = key
    if provider == "anthropic":
        core.ANTHROPIC_API_KEY = key
        core._anthropic_client = None
    elif provider == "google-gemini":
        core.GEMINI_API_KEY = key
        core._genai_client = None


def clear_provider_key_live(provider: str) -> None:
    """Inverse of hot_reload_provider_key — drop the live env var + cached client so
    removing a key flips availability immediately."""
    env_key = _env_key_for_provider(provider)
    if env_key:
        os.environ.pop(env_key, None)
    if provider == "anthropic":
        core.ANTHROPIC_API_KEY = ""
        core._anthropic_client = None
    elif provider == "google-gemini":
        core.GEMINI_API_KEY = ""
        core._genai_client = None

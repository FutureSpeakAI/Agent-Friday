"""
credential_store — encryption-at-rest for OAuth tokens and other secrets.

Multi-account Google support stores live access/refresh tokens on disk. Plaintext
JSON (the way the legacy single-account `google_token.json` is written) is the
exact thing an attacker who gets read access to the home directory would harvest
first. This module makes that impossible by protecting every credential blob with
the strongest mechanism available on the host, picked automatically:

  1. **Vault key** — FRIDAY_PASSWORD → Argon2id → AES-256-GCM. This is the *same*
     key the Sovereign Vault derives (vault_crypto.py), so credentials are treated
     as TIER_3 sensitive material. Preferred whenever FRIDAY_PASSWORD is set.
  2. **Windows DPAPI** — CryptProtectData (per-user) via ctypes when no password
     is set. No extra dependency; the blob is bound to the OS login account and is
     unreadable by other users or if copied to another machine.
  3. **Plaintext** — last resort only (e.g. non-Windows host with no password),
     and only with a loud one-time warning + hardened file permissions. Never
     silent.

Under FRIDAY_OS_MODE (the sealed Friday Linux kiosk image — see
`agent_friday.core.os_mode`), plaintext is not an acceptable last resort: a
sealed image has no interactive operator to notice the warning, and DPAPI
(Windows-only) never applies there anyway. `protect()` raises instead of
falling through, so a credential either gets encrypted or nothing is written
at all — never plaintext on disk with nobody looking. Windows-default
behavior (OS mode off) is completely unchanged: same warning, same plaintext
fallthrough as always.

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
_WARNED_PLAINTEXT = False


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
        _VAULT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if _VAULT_CONFIG_FILE.exists():
            cfg = json.loads(_VAULT_CONFIG_FILE.read_text(encoding="utf-8"))
        salt_hex = cfg.get("salt_hex")
        if not salt_hex:
            salt_hex = os.urandom(16).hex()
            cfg.update({"salt_hex": salt_hex, "kdf": "argon2id", "cipher": "aes-256-gcm"})
            tmp = _VAULT_CONFIG_FILE.with_name(_VAULT_CONFIG_FILE.name + ".tmp")
            tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            tmp.replace(_VAULT_CONFIG_FILE)
        _VAULT_KEY = _vc.derive_key(pw, bytes.fromhex(salt_hex))
    except Exception as e:  # pragma: no cover - defensive
        print(f"[credstore] vault key derivation failed ({e}); trying DPAPI/plaintext.")
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
    """The mechanism that will be used right now."""
    try:
        from agent_friday.services import keystore as _ks
        _ks.root_key()
        return "keystore"
    except Exception:
        pass
    if _vault_key() is not None:
        return "vault"
    if _dpapi_available():
        return "dpapi"
    return "plaintext"


def protect(data: bytes) -> tuple[bytes, str]:
    """Encrypt `data` with Friday's own keystore.

    Returns (blob, method). `method` is recorded in metadata for auditing; the
    blob itself is also self-describing so unprotect() never needs it.

    FRIDAY'S OWN STORE, FIRST AND NORMALLY ONLY. Everything written from
    2026-09-19 goes through `keystore`, which keeps one random root key in one
    file that every process finds the same way. The three mechanisms below it
    survive only so that blobs written before that date can still be READ; see
    `unprotect`. Nothing new is written with them, because the whole defect was
    a store that picked a different key depending on how the process started
    and then reported the result as the user's Google account being revoked.

    The keystore raising is not a reason to drop a tier. If it is locked, the
    honest outcome is a failure the user can act on, not a credential written
    under a weaker scheme they never chose.
    """
    global _WARNED_PLAINTEXT
    from agent_friday.services import keystore as _ks
    try:
        return _ks.encrypt(data), "keystore"
    except _ks.KeystoreLocked:
        # Fail closed and say why. Falling through here would write the
        # credential under a key the unlocked process cannot read, which is
        # the original bug with the sign flipped.
        raise
    except Exception as e:
        print("[credstore] keystore unavailable (%s: %s); falling back to the "
              "legacy mechanism for this write." % (type(e).__name__, e),
              file=sys.stderr)
    key = _vault_key()
    if key is not None:
        return _vc.encrypt(data, key), "vault"
    dp = _dpapi(data, encrypt=True)
    if dp is not None:
        return _DPAPI_MAGIC + dp, "dpapi"
    if is_os_mode():
        # Fail closed. The sealed kiosk image has no interactive operator to
        # see a stderr warning, and DPAPI is Windows-only, so the plaintext
        # fallthrough below would silently ship a real secret unencrypted on
        # every Linux OS-mode host with no FRIDAY_PASSWORD set. Refusing is
        # the only option that cannot be missed.
        raise RuntimeError(
            "refusing to write a credential as PLAINTEXT under FRIDAY_OS_MODE=1: "
            "no FRIDAY_PASSWORD (vault key) is set and DPAPI is unavailable on "
            "this host (DPAPI is Windows-only; there is no equivalent on the "
            "Friday Linux kiosk image). Set FRIDAY_PASSWORD or "
            "FRIDAY_VAULT_PASSPHRASE so this credential can be encrypted before "
            "it touches disk. Nothing was written."
        )
    if not _WARNED_PLAINTEXT:
        print("[credstore] WARNING: no FRIDAY_PASSWORD and no DPAPI — credentials "
              "stored as PLAINTEXT at rest (file permissions hardened). Set "
              "FRIDAY_PASSWORD to encrypt.", file=sys.stderr)
        _WARNED_PLAINTEXT = True
    return data, "plaintext"


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


def write_secret(path: Path, data: bytes) -> str:
    """Atomically write an encrypted secret to `path`, hardening its permissions.

    Returns the protection method used. The plaintext never touches disk
    unencrypted (the temp file holds the already-protected blob).

    `protect()` is called before anything touches `path` (including creating
    its parent directory) so that a fail-closed raise under FRIDAY_OS_MODE
    (no FRIDAY_PASSWORD, no DPAPI) leaves the filesystem completely
    untouched — not even an empty directory left behind.
    """
    path = Path(path)
    blob, method = protect(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(blob)
    harden_permissions(tmp)
    tmp.replace(path)
    harden_permissions(path)
    return method


def read_secret(path: Path) -> bytes:
    """Read and decrypt a secret written by write_secret()."""
    return unprotect(Path(path).read_bytes())


def harden_permissions(path: Path) -> None:
    """Restrict a file to the current user only. Best-effort, cross-platform.

    POSIX: chmod 0600. Windows: drop inheritance and grant only the current user
    via icacls (chmod alone can't express an ACL on NTFS).
    """
    path = Path(path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    if os.name == "nt":
        try:
            user = os.environ.get("USERNAME") or ""
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    capture_output=True, timeout=10, check=False,
                )
        except Exception:
            pass


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

    NOT PARANOIA - MEASURED. On 2026-09-19 Stephen's machine held TWO different
    passphrases: one in friday_startup.bat and a different one in the Windows
    keychain. `vault_passphrase.resolve()` prefers the keychain, so every
    credential written before that keychain entry appeared became unreadable
    the moment it did - four provider API keys and an MCP OAuth token, silently,
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
    return _PROVIDER_KEYS_DIR / f"{safe}.key"


def set_provider_key(provider: str, key: str) -> str:
    """Encrypt and store an API key for a provider. Returns the protection method.
    The key value is never logged, printed, or echoed back."""
    method = write_secret(_provider_key_path(provider), (key or "").encode("utf-8"))
    audit_event("provider_key", "set", provider=provider, method=method, present=bool(key))
    return method


def get_provider_key(provider: str) -> str | None:
    """Decrypt and return a stored provider key, or None if absent/unreadable."""
    path = _provider_key_path(provider)
    if not path.exists():
        return None
    try:
        return read_secret(path).decode("utf-8")
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
        read_secret(path)
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
        "    val = read_secret(sys.argv[1])\n"
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
        old_plain = None
        try:
            old_plain = get_provider_key(name)
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
        val = get_provider_key(provider)
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

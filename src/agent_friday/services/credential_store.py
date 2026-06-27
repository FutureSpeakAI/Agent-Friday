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
    """The vault passphrase. Read live from the environment (set via FRIDAY_PASSWORD
    env var) and fall back to the value core captured at import — whichever is non-empty."""
    return os.environ.get("FRIDAY_PASSWORD") or getattr(core, "FRIDAY_PASSWORD", "") or ""


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


# ── Windows DPAPI (per-user) via ctypes — no pywin32 dependency ───────────────
def _dpapi_available() -> bool:
    return os.name == "nt"


def _dpapi(data: bytes, encrypt: bool) -> bytes | None:
    """Call CryptProtectData / CryptUnprotectData. Returns None if unavailable
    or on failure (caller falls back / raises)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        # CRYPTPROTECT_UI_FORBIDDEN = 0x1 (never prompt); per-user scope (flags=0x1).
        fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
        return out
    except Exception:
        return None


def protection_method() -> str:
    """The mechanism that will be used right now: 'vault' | 'dpapi' | 'plaintext'."""
    if _vault_key() is not None:
        return "vault"
    if _dpapi_available():
        return "dpapi"
    return "plaintext"


def protect(data: bytes) -> tuple[bytes, str]:
    """Encrypt `data` with the strongest available method.

    Returns (blob, method). `method` is recorded in metadata for auditing; the
    blob itself is also self-describing so unprotect() never needs it.
    """
    global _WARNED_PLAINTEXT
    key = _vault_key()
    if key is not None:
        return _vc.encrypt(data, key), "vault"
    dp = _dpapi(data, encrypt=True)
    if dp is not None:
        return _DPAPI_MAGIC + dp, "dpapi"
    if not _WARNED_PLAINTEXT:
        print("[credstore] WARNING: no FRIDAY_PASSWORD and no DPAPI — credentials "
              "stored as PLAINTEXT at rest (file permissions hardened). Set "
              "FRIDAY_PASSWORD to encrypt.", file=sys.stderr)
        _WARNED_PLAINTEXT = True
    return data, "plaintext"


def unprotect(blob: bytes) -> bytes:
    """Inverse of protect(). Auto-detects the protection method from the blob."""
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
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob, method = protect(data)
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
    """'connected' if a key is stored for this provider, else 'missing'."""
    return "connected" if _provider_key_path(provider).exists() else "missing"


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


def bootstrap_provider_env() -> int:
    """Decrypt stored provider keys into os.environ under the env var each provider's
    auth expects, so is_provider_available() and the SDK clients see them. Called at
    server boot, after the launch-script bootstrap. Never overrides a key already set
    in the environment. Returns the number of keys loaded."""
    loaded = 0
    for provider in list_provider_keys():
        env_key = _env_key_for_provider(provider)
        if not env_key or os.environ.get(env_key):
            continue
        val = get_provider_key(provider)
        if val:
            os.environ[env_key] = val
            loaded += 1
    if loaded:
        audit_event("provider_key", "bootstrap_env", count=loaded)
    return loaded


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

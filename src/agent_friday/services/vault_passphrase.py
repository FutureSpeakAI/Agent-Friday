"""vault_passphrase -- the ONE place that knows where the vault passphrase lives.

WHY THIS MODULE EXISTS
----------------------
The passphrase that decrypts ~/.friday/vault used to have exactly one automatic
home: ``<install>/app/start.bat``, in the clear, on the line after the API keys.
``app.copy`` deletes that directory recursively and rebuilds it from the payload,
and ``start.bat`` is correctly excluded from the payload, so the copy cannot put
it back. The ciphertext is AES-256-GCM over an Argon2id key; delete the folder
and the data survives while its only key does not. There is no recovery. 5.6.6
taught the installer to carry the file across the delete. That stopped the
bleeding; it left the credential in the condemned building, in plaintext.

It also left two modules disagreeing about the same secret:

  * ``services/agent.py::_get_vault_key`` read the environment first and the OS
    keychain only as a fallback -- while its own docstring promised the
    opposite. Since ``core._bootstrap_env_from_launch_scripts`` copies
    start.bat's SET lines into ``os.environ`` at package import, "environment
    first" meant "start.bat first".
  * ``services/credential_store.py`` never consulted the keychain at all, so
    ``friday vault-setup`` -- which writes ONLY to the keychain -- produced a
    working Sovereign Vault and a credential store that silently dropped a tier
    to DPAPI.

Relocating the passphrase on top of that disagreement would have half-landed, in
a way invisible until someone inspected which cipher a blob was written with.
So: one resolver, one documented order, every consumer calling it.

THE ORDER, AND WHY IT IS THIS ORDER
-----------------------------------
1. **An environment variable a human set.** That is what an env var is for, and
   a stale store must never override a deliberate export.
2. **The OS keychain** (``agent-friday`` / ``vault-passphrase``).
3. **The DPAPI file** under ``~/.friday/security``.
4. **An environment variable that came from a launch script**, then start.bat
   itself. Legacy, and migration sources only.

Steps 1 and 4 are the same environment variable. They are different rows in this
table because ``core.ENV_FROM_LAUNCH_SCRIPTS`` records which names the bootstrap
took out of a .bat file, so we can tell a person's decision from a file's. That
distinction is the whole of F1: it lets the keychain outrank start.bat without
also outranking the operator.

WHY TWO DURABLE HOMES AND NOT ONE
---------------------------------
``keyring`` is an optional dependency (``pyproject.toml:86``). The packaged
installer ships it (``packaging/windows/requirements/core.txt:38``); a source
checkout does not, and this repo's own dev venv does not have it. A store that
works only when an optional import succeeds is not a store. So on Windows the
passphrase is written to BOTH the keychain and a DPAPI-wrapped file, and either
one alone is enough to open the vault.

Two stores means a precedence rule, and this codebase has been bitten by exactly
that before (``credential_store.py:379``: "A KEY SAVED IN SETTINGS BEATS ONE
START.BAT PUT THERE"). The mitigation is that both are written by the same
``store()`` call, so they can only diverge if something outside this module
edits one -- and ``resolve()`` reports which home answered, so a disagreement is
visible rather than silent.

WHY NOT A PLAINTEXT FILE UNDER ~/.friday
----------------------------------------
Because the key would then sit in the same folder as the ciphertext it decrypts,
and a single folder read would yield both halves. That is the arrangement
encryption-at-rest exists to prevent. The DPAPI file is wrapped with
``CryptProtectData`` under the per-user scope: readable only by code running as
this Windows account, useless if copied to another machine. It lives in
``~/.friday/security`` rather than ``~/.friday/vault`` so that key material and
ciphertext are not in the same directory.

WHAT MAKES THE LOCATION PROVABLY SAFE
-------------------------------------
Not a convention. ``start.bat`` was not carelessly placed either -- it was placed
somewhere everyone believed was safe, and the belief was never checked against
what ``app.copy`` actually does. Two tests, because they catch different things:

  T1  static: ``file_homes()`` must not return a path under the package root.
      ``tests/unit/test_vault_passphrase_location.py`` and
      ``packaging/windows/tests/Test-Installer.ps1``.
  T2  dynamic: the published-asset upgrade rehearsal installs a real prior
      version, puts a real passphrase in the real place, encrypts a real
      artifact, upgrades with the real installer, and asserts the ciphertext
      still decrypts.
      ``packaging/windows/tests/rehearsal/upgrade-vault-test.ps1``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import agent_friday.core as core
from agent_friday.core.os_mode import is_os_mode

KEYRING_SERVICE = "agent-friday"
KEYRING_ACCOUNT = "vault-passphrase"

# Self-describing, the same way credential_store's blobs are: a reader always
# knows how a blob was written, so a format change can never be misread as
# ciphertext.
DPAPI_MAGIC = b"FRIDAYDPAPI\x01"

_DPAPI_FILE_NAME = "vault-passphrase.dpapi"

# Resolved at call time, not import time, so tests (and anything that redirects
# the home directory) can point it somewhere else.
_SECURITY_DIR: Path | None = None

# The env vars, in the order a value is looked for in each of them.
_ENV_VARS = ("FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD")

_CACHE: tuple[str, str] | None = None


def reset_cache() -> None:
    """Forget the resolved passphrase. For tests and for `friday vault-setup`."""
    global _CACHE
    _CACHE = None


def _security_dir() -> Path:
    if _SECURITY_DIR is not None:
        return Path(_SECURITY_DIR)
    return Path(core.FRIDAY_DIR) / "security"


def file_homes() -> list[Path]:
    """Every path this module may write the passphrase to.

    The containment test asserts against exactly this list, so anything added
    here is automatically covered. Returned whether or not the file exists.
    """
    return [_security_dir() / _DPAPI_FILE_NAME]


# -- Windows DPAPI (per-user) via ctypes -- no pywin32 dependency -------------

def dpapi(data: bytes, encrypt: bool) -> bytes | None:
    """CryptProtectData / CryptUnprotectData. None when unavailable or on failure.

    Canonical home for this helper: ``credential_store`` imports it from here.
    It used to have its own copy, and a second copy of a crypto primitive is a
    second thing to get wrong.
    """
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
        # CRYPTPROTECT_UI_FORBIDDEN = 0x1 (never prompt); per-user scope.
        fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def dpapi_available() -> bool:
    return os.name == "nt"


# -- The individual homes -----------------------------------------------------

def _from_keyring() -> str:
    try:
        import keyring as _keyring
        return (_keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or "").strip()
    except Exception:
        return ""


def _from_dpapi_file() -> str:
    for path in file_homes():
        try:
            raw = Path(path).read_bytes()
        except Exception:
            continue
        if not raw.startswith(DPAPI_MAGIC):
            continue
        out = dpapi(raw[len(DPAPI_MAGIC):], encrypt=False)
        if not out:
            # A truncated or foreign blob. Treat as absent rather than as a
            # passphrase: a half-written credential is worse than a missing one
            # because it looks present, derives a wrong key, and reports the
            # vault as corrupt.
            continue
        try:
            return out.decode("utf-8").strip()
        except Exception:
            continue
    return ""


def _from_start_bat() -> str:
    """Parse start.bat directly. Legacy; a migration source only."""
    try:
        proj_root = Path(__file__).resolve().parents[3]
        sb = proj_root / "start.bat"
        if not sb.exists():
            return ""
        m = re.search(r"(?im)^\s*SET\s+FRIDAY_PASSWORD=(.*)$",
                      sb.read_text(encoding="utf-8", errors="ignore"))
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def _env_candidates() -> tuple[str, str, str, str]:
    """((human_value, human_var), (launch_value, launch_var)) flattened.

    Splits the environment in two by asking ``core.ENV_FROM_LAUNCH_SCRIPTS``
    which names the bootstrap took out of a .bat file. A name in that set is a
    file's opinion wearing an environment variable's clothes.
    """
    from_launch = set(getattr(core, "ENV_FROM_LAUNCH_SCRIPTS", None) or ())
    human_v = human_n = launch_v = launch_n = ""
    for var in _ENV_VARS:
        val = (os.environ.get(var) or "").strip()
        if not val:
            continue
        if var in from_launch:
            if not launch_v:
                launch_v, launch_n = val, var
        else:
            if not human_v:
                human_v, human_n = val, var
    return human_v, human_n, launch_v, launch_n


# -- The resolver -------------------------------------------------------------

def resolve(use_cache: bool = True) -> tuple[str, str]:
    """``(passphrase, human-readable source)``, or ``("", "")``.

    The ONE order. See this module's docstring for why it is this order.
    """
    global _CACHE
    if use_cache and _CACHE is not None:
        return _CACHE

    human_v, human_n, launch_v, launch_n = _env_candidates()

    found = ("", "")
    if human_v:
        found = (human_v, "the %s environment variable" % human_n)
    else:
        kv = _from_keyring()
        if kv:
            found = (kv, "your operating system's keychain")
        else:
            dv = _from_dpapi_file()
            if dv:
                found = (dv, "Friday's protected credential file")
            elif launch_v:
                found = (launch_v, "start.bat (via the %s environment variable)" % launch_n)
            else:
                sv = _from_start_bat()
                if sv:
                    found = (sv, "start.bat")

    if use_cache:
        _CACHE = found
    return found


def legacy_value() -> tuple[str, str]:
    """The passphrase as it exists in a LEGACY home only. For migration."""
    _, _, launch_v, launch_n = _env_candidates()
    if launch_v:
        return launch_v, "start.bat (via the %s environment variable)" % launch_n
    sv = _from_start_bat()
    if sv:
        return sv, "start.bat"
    return "", ""


def durable_value() -> tuple[str, str]:
    """The passphrase as it exists in a DURABLE home only. For migration."""
    kv = _from_keyring()
    if kv:
        return kv, "your operating system's keychain"
    dv = _from_dpapi_file()
    if dv:
        return dv, "Friday's protected credential file"
    return "", ""


# -- Writing ------------------------------------------------------------------

def store(passphrase: str) -> list[str]:
    """Write the passphrase to every durable home available. Returns their names.

    Writes BOTH homes rather than picking one, so losing either is survivable.
    An empty return means nothing durable could be written -- the caller must
    say so out loud rather than reporting success.

    Under FRIDAY_OS_MODE, an empty result is not just reported -- it is
    refused outright (raises RuntimeError). Every existing caller of this
    function already treats an empty list as "could not store it" and shows
    the user an explicit message rather than claiming success (see
    cli.py::cmd_vault_setup, setup_wizard.py, routes/core_routes.py,
    routes/insights.py), so this changes nothing about what those call sites
    report on Windows. It matters specifically for the sealed Linux kiosk
    image, which has no interactive operator to read a printed message: on a
    host with neither a working `keyring` backend nor DPAPI (Windows-only),
    the old behavior returned `[]` and every caller quietly moved on as if
    "no durable home" were just informational. A silent no-op that pretends
    to have stored a secret is worse than a loud failure -- see
    KNOWN_ISSUES.md Section 7 for the Linux keyring/Secret-Service gap this
    surfaces.
    """
    passphrase = (passphrase or "").strip()
    if not passphrase:
        return []

    written: list[str] = []

    try:
        import keyring as _keyring
        _keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, passphrase)
        if (_keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or "") == passphrase:
            written.append("keychain")
    except Exception:
        pass

    if dpapi_available():
        blob = dpapi(passphrase.encode("utf-8"), encrypt=True)
        if blob:
            path = file_homes()[0]
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic. An interrupted write must not leave a truncated blob
                # that unwraps to garbage -- see _from_dpapi_file.
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_bytes(DPAPI_MAGIC + blob)
                os.replace(tmp, path)
                written.append("protected file")
            except Exception:
                pass

    if written:
        reset_cache()
    elif is_os_mode():
        raise RuntimeError(
            "refusing to report the vault passphrase as stored under "
            "FRIDAY_OS_MODE=1: neither the OS keychain (`keyring`) nor DPAPI "
            "(Windows-only) could persist it on this host, so nothing durable "
            "was written. On Linux this almost always means `keyring` is not "
            "installed or has no Secret Service provider (e.g. gnome-keyring "
            "/ libsecret) available in this image -- see KNOWN_ISSUES.md "
            "Section 7. Set FRIDAY_VAULT_PASSPHRASE in the environment "
            "instead, or add a durable secret store to this deployment."
        )
    return written


def locations() -> list[dict]:
    """Where the passphrase is, for `friday status`. Never returns the value.

    Nobody currently knows how many installs have a durable copy, because
    `vault-setup` is opt-in and nothing reported it. This is that line.
    """
    human_v, human_n, launch_v, launch_n = _env_candidates()
    out = [
        {"home": "OS keychain", "durable": True, "present": bool(_from_keyring())},
        {"home": "protected file (%s)" % file_homes()[0],
         "durable": True, "present": bool(_from_dpapi_file())},
        {"home": "environment (%s)" % (human_n or "unset"),
         "durable": False, "present": bool(human_v)},
        {"home": "start.bat", "durable": False,
         "present": bool(launch_v or _from_start_bat())},
    ]
    return out


def verify(passphrase: str):
    """True / False if it can be checked against real ciphertext, else None.

    ``None`` is not a failure -- it means there is nothing encrypted yet to test
    against, so there is nothing to be wrong about. Moved here from
    ``setup_wizard._verify_vault_passphrase`` so the wizard, the migration and
    `friday status` all ask the same question the same way.
    """
    try:
        from agent_friday.privacy import vault_crypto as vc
        vault_dir = Path(core.FRIDAY_DIR) / "vault"
        cfg = vault_dir / ".vault_config.json"
        if not cfg.exists():
            return None
        salt = vc.load_salt(cfg)
        # Production derives with the DEFAULT profile (no profile argument):
        # services/agent.py, services/credential_store.py. Match it.
        key = vc.derive_key(passphrase, salt)
        for f in sorted(vault_dir.rglob("*")):
            if not f.is_file() or f.name == ".vault_config.json":
                continue
            try:
                blob = f.read_bytes()
            except Exception:
                continue
            if not vc.is_encrypted(blob):
                continue
            try:
                vc.decrypt(blob, key)
                return True
            except Exception:
                return False
        return None
    except Exception:
        return None


# -- Migration ----------------------------------------------------------------

def _strip_start_bat_passphrase() -> bool:
    """Remove the ``SET FRIDAY_PASSWORD=`` line from start.bat. True if changed.

    Leaves the API-key lines alone. They have an encrypted second home
    (``~/.friday/providers/keys/``) and their own separate argument; this
    function is about the one credential that has no other copy.
    """
    try:
        proj_root = Path(__file__).resolve().parents[3]
        sb = proj_root / "start.bat"
        if not sb.exists():
            return False
        text = sb.read_text(encoding="utf-8", errors="ignore")
        stripped = re.sub(r"(?im)^\s*SET\s+FRIDAY_PASSWORD=.*\r?\n?", "", text)  # pragma: allowlist secret
        if stripped == text:
            return False
        tmp = sb.with_name(sb.name + ".tmp")
        tmp.write_text(stripped, encoding="utf-8")
        os.replace(tmp, sb)
        return True
    except Exception:
        return False


def migrate() -> dict:
    """Move a legacy passphrase into the durable homes. Idempotent, interruptible.

    THE ORDERING IS THE WHOLE SAFETY ARGUMENT, so it is written down rather than
    left as folklore. Copy first, verify the copy by reading it back, and only
    then remove the original. Every interruption point leaves the passphrase
    readable by the next boot:

      * interrupted before the write -- nothing changed, start.bat still has it;
      * between write and verify -- both copies exist, next boot re-verifies;
      * between verify and the strip -- both copies exist and the durable one now
        outranks start.bat, so the next boot simply finishes the job.

    The only irreversible step is the strip, and it is last.

    A disagreement is NOT resolved by overwriting. If the durable home and
    start.bat hold different values, one of them does not open the vault, and
    guessing would destroy the data. We keep both, prefer whichever actually
    decrypts existing ciphertext, and report it.
    """
    result = {"action": "none", "wrote": [], "stripped": False, "detail": ""}

    legacy, legacy_src = legacy_value()
    durable, durable_src = durable_value()

    if not legacy and not durable:
        result["detail"] = "no passphrase anywhere; vault encryption is off"
        return result

    if not legacy:
        result["action"] = "already-migrated"
        result["detail"] = "passphrase is in %s and nowhere legacy" % durable_src
        return result

    if durable and durable != legacy:
        # Do not choose by precedence alone -- ask the ciphertext which one is
        # right. This is the one question with a factual answer.
        result["action"] = "conflict"
        which = None
        if verify(durable) is True:
            which = "the durable copy"
        elif verify(legacy) is True:
            which = "the start.bat copy"
        result["detail"] = (
            "two different passphrases are stored (%s vs %s). %s"
            % (durable_src, legacy_src,
               ("%s opens the vault; the other is stale and nothing was removed."
                % which) if which else
               "Neither could be checked against ciphertext; nothing was removed.")
        )
        return result

    if durable == legacy:
        # The copy already happened; only the strip is outstanding. This is the
        # "interrupted between verify and strip" resume path.
        result["action"] = "finish"
    else:
        wrote = store(legacy)
        if not wrote:
            result["action"] = "blocked"
            result["detail"] = (
                "could not write the passphrase to any durable home "
                "(no OS keychain and no DPAPI); start.bat left untouched"
            )
            return result
        result["wrote"] = wrote
        # Verify by reading it back out of the durable homes, not by trusting
        # the write call.
        back, _ = durable_value()
        if back != legacy:
            result["action"] = "blocked"
            result["detail"] = "the durable copy did not read back; start.bat left untouched"
            return result
        result["action"] = "migrated"

    result["stripped"] = _strip_start_bat_passphrase()
    if not result["detail"]:
        result["detail"] = "passphrase now lives in %s" % (
            " and ".join(result["wrote"]) or "its durable home")
    reset_cache()
    return result

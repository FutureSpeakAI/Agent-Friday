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

import json
import os
import re
import time
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

# Moves on every reset_cache(): every store, rotation and repair goes through
# it. Anything that caches keys derived from a passphrase (the knowledge
# graph's _known_keys) keys its cache on this number, so a passphrase change is
# never answered from a cache built under the previous one.
_KEY_GENERATION = 0

# The sealed files verify() samples, chosen once per key generation.
_SAMPLE: list | None = None


def reset_cache() -> None:
    """Forget the resolved passphrase, and move the key generation on."""
    global _CACHE, _KEY_GENERATION, _SAMPLE
    _CACHE = None
    _SAMPLE = None
    _KEY_GENERATION += 1


def key_generation() -> int:
    """A number that changes whenever the passphrase homes may have changed."""
    return _KEY_GENERATION


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

def _real_profile_dir() -> Path | None:
    """The account's own profile folder, ignoring a redirected USERPROFILE/HOME
    (which is exactly how rehearsals isolate ~/.friday), or None when it cannot
    be known.

    On Windows the profile is asked of the logon token (GetUserProfileDirectoryW,
    which reads the account's profile record, not the environment). There is no
    environment fallback: HOMEDRIVE+HOMEPATH can legitimately differ from the
    profile (a domain home share), and a wrong "redirected" verdict would fence
    a real home off its own keychain. None means "cannot tell", and the
    redirect test then does not count."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32")
            adv = ctypes.WinDLL("advapi32")
            ue = ctypes.WinDLL("userenv")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.HANDLE)]
            ue.GetUserProfileDirectoryW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
            tok = wintypes.HANDLE()
            if adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008,   # TOKEN_QUERY
                                    ctypes.byref(tok)):
                try:
                    size = wintypes.DWORD(1024)
                    buf = ctypes.create_unicode_buffer(size.value)
                    if ue.GetUserProfileDirectoryW(tok, buf, ctypes.byref(size)) and buf.value:
                        return Path(buf.value)
                finally:
                    k32.CloseHandle(tok)
        except Exception:
            pass
        return None
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        return None


def _process_profile_dir() -> Path:
    """The profile folder THIS process was given (USERPROFILE / HOME)."""
    return Path.home()


def _temp_roots() -> list[Path]:
    import tempfile
    return [Path(tempfile.gettempdir())]


def _same_path(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except Exception:
        return False


def _home_is_scratch() -> bool:
    """True for a test run's or a rehearsal's home, which must never touch the
    account's keychain slot. The signals are how those runs isolate a home:

      * ``FRIDAY_TESTING=1`` (the test suite);
      * a process profile (USERPROFILE / HOME) redirected away from the
        account's logon profile (the upgrade rehearsal's recipe);
      * a home under the system temp directory.

    ``FRIDAY_HOME`` alone is NOT one of them: an owner may keep Friday's data
    anywhere (the installer's upgrade honours it), and that home is as real as
    ``~/.friday``."""
    if os.environ.get("FRIDAY_TESTING") == "1":
        return True
    real = _real_profile_dir()
    if real is not None and not _same_path(_process_profile_dir(), real):
        return True
    try:
        here = Path(core.FRIDAY_DIR).resolve()
        for t in _temp_roots():
            try:
                here.relative_to(Path(t).resolve())
                return True
            except ValueError:
                continue
    except Exception:
        return True
    return False


def keychain_is_this_homes() -> bool:
    """May THIS Friday home use the OS keychain slot?

    The slot (agent-friday / vault-passphrase) is one per Windows ACCOUNT, not
    per Friday home. A rehearsal or test that redirects the home and then
    calls store() would overwrite the owner's real entry, and any Friday
    started without the right passphrase in its environment would then
    resolve a value that opens none of its own files. So only the account's
    real home uses the slot: ``<account profile>/.friday``, or the directory
    ``FRIDAY_HOME`` names when this is not a scratch home (``_home_is_scratch``).
    Every other home's durable store is its own DPAPI file.

    Every backend that persists outside this process is fenced: the OS
    keychains (Windows Credential Manager, macOS Keychain, Secret Service,
    KWallet, a chain of them) and the keyrings.alt file and encrypted-file
    backends, which write under the account's profile. Only a test's stand-in
    module or a backend defined outside those packages (an in-memory test
    double) is left alone, because it is not touching the machine.
    """
    try:
        import keyring as _keyring
    except Exception:
        return False
    if not hasattr(_keyring, "get_keyring"):      # a test's stand-in module
        return True
    try:
        backend = type(_keyring.get_keyring())
        mod = backend.__module__ or ""
        if mod in ("keyring.backends.null", "keyring.backends.fail"):
            return True                           # writes nowhere
        if not mod.startswith(("keyring.backends", "keyrings.")):
            return True                           # a test double, not the machine's
    except Exception:
        pass
    if _home_is_scratch():
        return False
    here = Path(core.FRIDAY_DIR)
    real = _real_profile_dir()
    if real is not None and _same_path(here, real / ".friday"):
        return True
    if real is None and _same_path(here, Path.home() / ".friday"):
        return True
    chosen = os.environ.get("FRIDAY_HOME")
    return bool(chosen) and _same_path(here, Path(os.path.expanduser(chosen)))


def _from_keyring() -> str:
    if not keychain_is_this_homes():
        return ""
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

def _candidates() -> list[tuple[str, str]]:
    """Every (value, source) a home holds, in precedence order, values only once."""
    human_v, human_n, launch_v, launch_n = _env_candidates()
    out: list[tuple[str, str]] = []
    if human_v:
        out.append((human_v, "the %s environment variable" % human_n))
    kv = _from_keyring()
    if kv:
        out.append((kv, "your operating system's keychain"))
    dv = _from_dpapi_file()
    if dv:
        out.append((dv, "Friday's protected credential file"))
    if launch_v:
        out.append((launch_v, "start.bat (via the %s environment variable)" % launch_n))
    sv = _from_start_bat()
    if sv:
        out.append((sv, "start.bat"))
    return out


#: Set by resolve() when the homes hold different passphrases. Never holds a
#: value: which homes disagreed, which one the ciphertext accepted, and whether
#: precedence alone would have picked a passphrase that opens nothing.
LAST_CONFLICT: dict = {}


def resolve(use_cache: bool = True) -> tuple[str, str]:
    """``(passphrase, human-readable source)``, or ``("", "")``.

    The ONE order. See this module's docstring for why it is this order.

    Precedence decides only while the homes agree. When they hold DIFFERENT
    passphrases, the ciphertext is asked: each distinct value is tried on the
    same sample of sealed files across the home (``open_count``), and the
    value that opens the most is used; precedence breaks a tie. The
    disagreement is recorded in LAST_CONFLICT for the integrity report.
    Choosing a passphrase that reads less of the data than another home's
    value is never right.
    """
    global _CACHE, LAST_CONFLICT
    if use_cache and _CACHE is not None:
        return _CACHE

    # A store(), rotation or repair may finish while this runs (the health
    # probe resolves in the background). The answer is cached only if the key
    # generation is still the one it started under.
    gen = _KEY_GENERATION
    cands = _candidates()
    found = cands[0] if cands else ("", "")
    distinct = []
    for v, src in cands:
        if all(v != d for d, _ in distinct):
            distinct.append((v, src))
    conflict: dict = {}
    if len(distinct) > 1:
        sample = _sealed_sample()
        verdicts = [(v, src, open_count(v, sample)) for v, src in distinct]
        conflict = {"homes": [src for _, src in cands],
                    "distinct_values": len(distinct),
                    "sampled": len(sample),
                    "opens": [src for _, src, n in verdicts if n],
                    "opens_nothing": [src for _, src, n in verdicts if n == 0]}
        best = max(verdicts, key=lambda t: t[2] or 0)     # max keeps the first of a tie
        winner_n = next(n for v, _, n in verdicts if v == found[0])
        if (best[2] or 0) > (winner_n or 0):
            conflict["precedence_would_pick"] = found[1]
            found = (best[0], best[1])
        conflict["chosen"] = found[1]
    if gen == _KEY_GENERATION:
        LAST_CONFLICT = conflict
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


# -- Kept passphrases ---------------------------------------------------------
#
# A passphrase a home holds is never simply overwritten by a different one: it
# may be the only key for files sealed under it. Before store() replaces a
# differing value, the value is kept, DPAPI-wrapped in
# security/stray-passphrase-<stamp>.dpapi (Windows), or under its own keychain
# account listed in security/kept-passphrases.json (elsewhere), and read back.
# Every reader that looks for "any key this machine knows" (credential_store.
# _legacy_keys, so also the knowledge graph's _known_keys; the keystore unwrap)
# includes the kept values.

_KEPT_GLOB = "stray-*.dpapi"
_KEPT_INDEX = "kept-passphrases.json"


def _durable_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a fsynced temp file and an atomic
    replace, then fsync the directory where the OS allows it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return                                  # Windows: directories cannot be opened
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def kept_values() -> list[tuple[str, str]]:
    """Every kept passphrase, as (label, value). Never logged or printed."""
    out: list[tuple[str, str]] = []
    sec = _security_dir()
    try:
        files = sorted(sec.glob(_KEPT_GLOB))
    except OSError:
        files = []
    for f in files:
        try:
            raw = f.read_bytes()
        except OSError:
            continue
        if not raw.startswith(DPAPI_MAGIC):
            continue
        v = dpapi(raw[len(DPAPI_MAGIC):], encrypt=False)
        if v:
            try:
                out.append(("kept:%s" % f.name, v.decode("utf-8").strip()))
            except Exception:
                continue
    try:
        names = json.loads((sec / _KEPT_INDEX).read_text(encoding="utf-8"))
        import keyring as _keyring
        for acct in names if isinstance(names, list) else []:
            v = (_keyring.get_password(KEYRING_SERVICE, str(acct)) or "").strip()
            if v:
                out.append(("kept:%s" % acct, v))
    except Exception:
        pass
    return out


def keep_displaced(value: str) -> str:
    """Keep ``value`` before a home holding it is overwritten. Returns where
    it went. Raises RuntimeError when it cannot be kept AND read back, so the
    caller refuses rather than overwrite. A value already kept is not kept
    twice."""
    value = (value or "").strip()
    if not value:
        return ""
    for label, v in kept_values():
        if v == value:
            return label
    stamp = time.strftime("%Y%m%dT%H%M%S")
    sec = _security_dir()
    if dpapi_available():
        blob = dpapi(value.encode("utf-8"), encrypt=True)
        if not blob:
            raise RuntimeError("the passphrase being replaced could not be wrapped to keep it")
        dest = sec / ("stray-passphrase-%s.dpapi" % stamp)
        n = 1
        while dest.exists():
            dest = sec / ("stray-passphrase-%s-%d.dpapi" % (stamp, n))
            n += 1
        try:
            _durable_write(dest, DPAPI_MAGIC + blob)
            raw = dest.read_bytes()
        except OSError as e:
            raise RuntimeError("the passphrase being replaced could not be kept (%s)"
                               % type(e).__name__) from e
        back = dpapi(raw[len(DPAPI_MAGIC):], encrypt=False) if raw.startswith(DPAPI_MAGIC) else None
        if back is None or back.decode("utf-8", "replace") != value:
            raise RuntimeError("the kept copy of the passphrase being replaced did not read back")
        return dest.name
    if keychain_is_this_homes():
        try:
            import keyring as _keyring
            acct = "%s-kept-%s-%s" % (KEYRING_ACCOUNT, stamp, os.urandom(3).hex())
            _keyring.set_password(KEYRING_SERVICE, acct, value)
            if (_keyring.get_password(KEYRING_SERVICE, acct) or "") != value:
                raise RuntimeError("did not read back")
            idx = sec / _KEPT_INDEX
            try:
                names = json.loads(idx.read_text(encoding="utf-8"))
                names = names if isinstance(names, list) else []
            except Exception:
                names = []
            names.append(acct)
            _durable_write(idx, json.dumps(names).encode("utf-8"))
            return acct
        except Exception as e:
            raise RuntimeError("the passphrase being replaced could not be kept (%s)"
                               % type(e).__name__) from e
    raise RuntimeError("there is nowhere to keep the passphrase being replaced")


def known_values() -> list[str]:
    """Every distinct passphrase this machine holds: the homes, then the kept
    values. For unwrapping and for opening old files; never for sealing."""
    out: list[str] = []
    for v, _src in _candidates() + [(v, lbl) for lbl, v in kept_values()]:
        if v and v not in out:
            out.append(v)
    return out


# -- Writing ------------------------------------------------------------------

def _keystore_state() -> tuple[str, str]:
    """("none", "") when the credential keystore is not passphrase-wrapped (or
    absent); ("ok", passphrase) with the passphrase that opens its check block;
    ("locked", "") when it is wrapped and no passphrase this machine knows
    opens it. The passphrase is found by the check block itself
    (keystore.unwrapping_passphrase), never by asking the resolver."""
    try:
        from agent_friday.services import keystore as ks
        doc = ks._read_file() or {}
    except Exception:
        return "none", ""
    if doc.get("wrap") != "passphrase":
        return "none", ""
    try:
        pw = ks.unwrapping_passphrase()
    except Exception:
        pw = ""
    return ("ok", pw) if pw else ("locked", "")


def _rewrap_keystore(passphrase: str) -> bool:
    """Rewrap the same root key under ``passphrase``. True when it did.

    keystore.set_wrap writes atomically, so on failure the keystore is still
    wrapped under the previous passphrase; the caller then aborts."""
    try:
        from agent_friday.services import keystore as ks
        ks.set_wrap("passphrase", passphrase)
        return True
    except Exception as e:
        import logging
        logging.getLogger("friday.vault_passphrase").error(
            "the credential keystore could not follow the new vault passphrase "
            "(%s); it stays wrapped under the previous one", type(e).__name__)
        return False


#: What a caller is told when a passphrase change is asked of a running Friday.
ROTATE_OFFLINE_ONLY = (
    "a different vault passphrase already opens this vault. Changing it "
    "re-encrypts every private file, which Friday does only while it is "
    "stopped: quit Friday, then run "
    "\"venv\\Scripts\\python.exe scripts\\vault_key_doctor.py --rotate\"")


#: Why the last store() wrote nothing, for its caller to say. "" when it wrote.
LAST_STORE_REFUSAL = ""


def store(passphrase: str, *, rotate: bool = False) -> list[str]:
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
    global LAST_STORE_REFUSAL
    LAST_STORE_REFUSAL = ""
    passphrase = (passphrase or "").strip()
    if not passphrase:
        return []

    import logging
    _log = logging.getLogger("friday.vault_passphrase")

    def _refuse(why: str) -> list:
        global LAST_STORE_REFUSAL
        LAST_STORE_REFUSAL = why
        _log.error("[vault] %s", why)
        return []

    # NEVER SILENTLY REPLACE THE PASSPHRASE THAT OPENS THE DATA, and never
    # write one that opens none of it. A different value is written only as a
    # rotation (rotate=True, offline: scripts/vault_key_doctor.py --rotate):
    # every file sealed under the old one is re-sealed under the new one
    # first, or nothing is written. When sealed files exist and no stored
    # passphrase opens any of them, the new value is written only if IT opens
    # them; otherwise the homes would lose the last record of which
    # passphrase the data needs.
    current = _working_passphrase()
    if not current:
        n_new = open_count(passphrase)
        if n_new == 0:
            return _refuse(
                "the encrypted files in this Friday home do not open with any stored "
                "vault passphrase or with this one, so nothing was written; run "
                "scripts/vault_key_doctor.py with Friday stopped to see which "
                "passphrase they need")
    rotating = bool(current) and current != passphrase
    if rotating and not rotate:
        return _refuse(ROTATE_OFFLINE_ONLY)

    # A keystore wrapped with the vault passphrase must follow it, or the next
    # start cannot unwrap the credential root key. Which passphrase wraps it
    # now is read from its own check block. A wrapped keystore that no known
    # passphrase opens is a refusal, not a skip: rewrapping it is impossible,
    # and writing a new passphrase over the homes would lose the one that
    # might still open it.
    ks_state, prior = _keystore_state()
    if ks_state == "locked":
        return _refuse("the credential keystore is passphrase-wrapped and no passphrase "
                       "stored on this computer unlocks it, so nothing was written; run "
                       "scripts/vault_key_doctor.py with Friday stopped")
    follow_keystore = ks_state == "ok" and prior != passphrase

    # The rotation runs first: every reason it can refuse (embedded
    # ciphertext, files another passphrase opens) is found before anything is
    # kept or written, and a refusal leaves the home exactly as it was.
    rotation = None
    if rotating:
        rotation = rotate_files(current, passphrase)
        if not rotation["ok"]:
            return _refuse("the vault could not be re-encrypted under the new "
                           "passphrase (%s)" % rotation["error"])

    def _put_back(why: str, keystore_back: bool = True) -> list:
        """Undo the rotation; say exactly what could not be undone."""
        failed = _undo_rotation(rotation) if rotation else []
        if failed:
            return _refuse("%s; %d file(s) could NOT be put back and are still sealed "
                           "under the new passphrase (their originals are in %s)"
                           % (why, len(failed), rotation.get("backup_dir")))
        return _refuse("%s; every file was put back%s" % (
            why, " and nothing was changed" if keystore_back else ""))

    # NEVER OVERWRITE A HOME'S DIFFERENT VALUE WITHOUT KEEPING IT. It may be
    # the only key for files, kept copies or a keystore sealed under it.
    displaced: list[str] = []
    if keychain_is_this_homes():
        kv = _from_keyring()
        if kv and kv != passphrase:
            displaced.append(kv)
    if dpapi_available():
        dv = _from_dpapi_file()
        if dv and dv != passphrase and dv not in displaced:
            displaced.append(dv)
    for v in displaced:
        try:
            keep_displaced(v)
        except RuntimeError as e:
            if rotation:
                return _put_back("%s, so nothing was written" % e)
            return _refuse("%s, so nothing was written" % e)

    # It is rewrapped BEFORE any home is written; if that fails, the rotation
    # is undone and nothing is written, so the keystore, the files and the
    # homes never disagree.
    if follow_keystore and not _rewrap_keystore(passphrase):
        return _put_back("the credential keystore could not be re-wrapped under the "
                         "new passphrase")

    written: list[str] = []

    if keychain_is_this_homes():
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
                # Atomic and fsynced. An interrupted write must not leave a
                # truncated blob that unwraps to garbage -- see _from_dpapi_file.
                _durable_write(path, DPAPI_MAGIC + blob)
                written.append("protected file")
            except Exception:
                pass

    if written:
        reset_cache()
    elif rotation or follow_keystore:
        # Nothing durable holds the new passphrase: put the files and the
        # keystore back under the one the homes still hold.
        why = "no durable home could hold the new passphrase"
        keystore_back = True
        if follow_keystore and prior and not _rewrap_keystore(prior):
            keystore_back = False
            why += ("; the credential keystore could NOT be re-wrapped back and is "
                    "still wrapped under the new passphrase, which no home holds "
                    "(run scripts/vault_key_doctor.py with Friday stopped)")
        if rotation:
            _put_back(why, keystore_back)
        elif not keystore_back:
            _refuse(why)
        reset_cache()
    if not written and is_os_mode():
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

    The question is asked of a SAMPLE of sealed files spread across the whole
    home (``_sealed_sample``), not of one file: True when the passphrase opens
    at least one of them. Callers that must choose between passphrases compare
    ``open_count`` instead.
    """
    n = open_count(passphrase)
    if n is None:
        return None
    return n > 0


#: How many sealed files verify() and open_count() try by default.
SAMPLE_SIZE = 8


def _sealed_sample(k: int | None = None) -> list:
    """Up to ``k`` sealed files, evenly spread over the sorted list of every
    sealed file in the home, so the wiki, the vault folders and the knowledge
    graph are all represented. Chosen once per key generation."""
    global _SAMPLE
    k = k or SAMPLE_SIZE
    if _SAMPLE is not None and k == SAMPLE_SIZE:
        return list(_SAMPLE)
    try:
        files = sealed_files()
    except Exception:
        files = []
    if len(files) > k:
        step = len(files) / k
        files = [files[int(i * step)] for i in range(k)]
    if k == SAMPLE_SIZE:
        _SAMPLE = list(files)
    return files


def open_count(passphrase: str, sample: list | None = None):
    """How many of the sampled sealed files ``passphrase`` opens, or None when
    there is nothing to test against (no vault salt, or no sealed file)."""
    try:
        from agent_friday.privacy import vault_crypto as vc
        cfg = Path(core.FRIDAY_DIR) / "vault" / ".vault_config.json"
        if not cfg.exists():
            return None
        files = _sealed_sample() if sample is None else sample
        if not files:
            return None
        salt = vc.load_salt(cfg)
        # Production derives with the DEFAULT profile (no profile argument):
        # services/agent.py, services/credential_store.py. Match it.
        key = vc.derive_key(passphrase, salt)
        n = 0
        for f in files:
            try:
                vc.decrypt(Path(f).read_bytes(), key)
                n += 1
            except Exception:
                continue
        return n
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


# -- Rotation -----------------------------------------------------------------

def _working_passphrase() -> str:
    """The value a home holds that opens the most of the sampled sealed files
    (precedence breaks a tie), or "" when none opens any of them."""
    sample = _sealed_sample()
    best, best_n = "", 0
    seen = set()
    for v, _src in _candidates():
        if v in seen:
            continue
        seen.add(v)
        n = open_count(v, sample) or 0
        if n > best_n:
            best, best_n = v, n
    return best


#: The top-level folders of a Friday home that hold passphrase-sealed files:
#: the wiki (entities included), the vault folders, the knowledge graph's
#: private records, finance and health, goals and governance records, the
#: Laya personal store, and legacy credential blobs. Sampling and the boot
#: scan look only here; a rotation walks the whole home (``full=True``) so a
#: sealed file somewhere unexpected is never left behind under the old key.
SEALED_ROOTS = ("wiki", "vault", "knowledge-graph", "finance", "health", "goals",
                "governance", "runtime/laya2/personal", "providers", "mcp_oauth",
                "google_accounts", "platforms")


def sealed_files(root: Path | None = None, full: bool = False) -> list[Path]:
    """Every file carrying the passphrase-key envelope, found by reading the
    header, never by name. Only ``SEALED_ROOTS`` unless ``full``; backups,
    pre-rotation and recovery copies are skipped either way."""
    from agent_friday.privacy import vault_crypto as vc
    root = Path(root or core.FRIDAY_DIR)
    skip = {"browser-profile", "models", "audio-cache", "cache", "node_modules"}
    tops = [root] if full else [root / r for r in SEALED_ROOTS if (root / r).is_dir()]
    out = []
    for top in tops:
        for dp, dns, fns in os.walk(top):
            dns[:] = [d for d in dns if d not in skip and "backup" not in d.lower()
                      and not d.startswith(("pre-rotation-", "kg-pre-recover-"))]
            for fn in fns:
                p = Path(dp) / fn
                try:
                    with open(p, "rb") as fh:
                        if fh.read(len(vc.MAGIC)) == vc.MAGIC:
                            out.append(p)
                except OSError:
                    continue
    return sorted(set(out))


def _embedded_token() -> bytes:
    """The base64 text every base64-encoded vault ciphertext starts with,
    derived from the envelope header (whole 3-byte groups only, so it does not
    depend on what follows the header)."""
    import base64
    from agent_friday.privacy import vault_crypto as vc
    n = len(vc.MAGIC) // 3 * 3
    return base64.b64encode(vc.MAGIC[:n])


def embedded_sealed_files(root: Path | None = None) -> list[Path]:
    """Files that carry vault ciphertext base64-encoded INSIDE them (the
    Library shelf's SQLite rows, the Laya personal store and its staging key)
    rather than as a raw sealed file. A rotation cannot re-seal them, so it
    refuses while any exists. Read in chunks; raw sealed files are skipped,
    and so is the credential keystore, whose wrap store() itself moves to the
    new passphrase (keystore.set_wrap)."""
    from agent_friday.privacy import vault_crypto as vc
    token = _embedded_token()
    root = Path(root or core.FRIDAY_DIR)
    exempt = set()
    try:
        from agent_friday.services import keystore as _ks
        exempt.add(os.path.normcase(str(Path(_ks.KEYSTORE_PATH).resolve())))
    except Exception:
        pass
    skip = {"browser-profile", "models", "audio-cache", "cache", "node_modules"}
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip and "backup" not in d.lower()
                  and not d.startswith(("pre-rotation-", "kg-pre-recover-"))]
        for fn in fns:
            p = Path(dp) / fn
            try:
                if os.path.normcase(str(p.resolve())) in exempt:
                    continue
                with open(p, "rb") as fh:
                    head = fh.read(len(vc.MAGIC))
                    if head == vc.MAGIC:
                        continue
                    tail = head
                    while True:
                        chunk = fh.read(1 << 20)
                        buf = tail + chunk
                        if token in buf:
                            out.append(p)
                            break
                        if not chunk:
                            break
                        tail = buf[-(len(token) - 1):]
            except OSError:
                continue
    return sorted(out)


def rotate_files(old: str, new: str, root: Path | None = None) -> dict:
    """Re-seal every passphrase-sealed file from ``old`` to ``new``. All or nothing.

    1. every file in the whole home is opened with the old key and re-sealed
       in memory, and the new ciphertext is checked by opening it (nothing
       written yet);
    2. a file the old key does not open is tried with every OTHER passphrase
       this machine knows (homes and kept values). If one opens it, the
       rotation is refused: re-sealing the rest and moving the homes to
       ``new`` would leave that file under a passphrase nothing points at.
       A file no known passphrase opens is left as it is and reported;
    3. each original is copied to security/pre-rotation-<stamp>/ (read back)
       and replaced through a fsynced temp file;
    4. if any step fails, every replaced file is put back from that copy, and
       the report lists any file that could not be (``not_restored``).
    Never prints or returns contents.
    """
    import shutil
    import time as _t
    from agent_friday.privacy import vault_crypto as vc
    report = {"ok": False, "resealed": 0, "not_opened_by_old": [], "error": "",
              "not_restored": []}
    try:
        cfg = Path(core.FRIDAY_DIR) / "vault" / ".vault_config.json"
        salt = vc.load_salt(cfg)
        k_old, k_new = vc.derive_key(old, salt), vc.derive_key(new, salt)
    except Exception as e:
        report["error"] = type(e).__name__
        return report
    root = Path(root or core.FRIDAY_DIR)
    embedded = embedded_sealed_files(root)
    if embedded:
        where: dict = {}
        for p in embedded:
            try:
                top = "/".join(p.relative_to(root).parts[:1]) or p.name
            except ValueError:
                top = p.name
            where[top] = where.get(top, 0) + 1
        report["embedded"] = [str(p) for p in embedded]
        report["error"] = ("%d file(s) hold vault ciphertext inside them (%s: the Library "
                           "shelf, the Laya personal store or another store), which a "
                           "rotation cannot re-seal; nothing was changed"
                           % (len(embedded), ", ".join("%s %d" % kv for kv in sorted(where.items()))))
        return report
    staged = []
    for p in sealed_files(root, full=True):
        try:
            plain = vc.decrypt(p.read_bytes(), k_old)
        except Exception:
            report["not_opened_by_old"].append(str(p))
            continue
        fresh = vc.encrypt(plain, k_new)
        if vc.decrypt(fresh, k_new) != plain:
            report["error"] = "round-trip mismatch"
            return report
        del plain
        staged.append((p, fresh))
    if report["not_opened_by_old"]:
        others = [v for v in known_values() if v not in (old, new)]
        for v in others:
            try:
                k = vc.derive_key(v, salt)
            except Exception:
                continue
            for s in report["not_opened_by_old"]:
                try:
                    vc.decrypt(Path(s).read_bytes(), k)
                except Exception:
                    continue
                report["error"] = ("%d file(s) are sealed under another passphrase stored "
                                   "on this computer, not the current one; rotating would "
                                   "orphan them. Re-seal them first (vault_key_doctor.py "
                                   "--recover-kg for the knowledge graph)"
                                   % len(report["not_opened_by_old"]))
                return report
    bdir = Path(core.FRIDAY_DIR) / "security" / ("pre-rotation-%s" % _t.strftime("%Y%m%dT%H%M%S"))
    n = 1
    while bdir.exists():
        bdir = bdir.with_name(bdir.name.split("~")[0] + "~%d" % n)
        n += 1
    done = []
    try:
        for p, fresh in staged:
            dest = bdir / p.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            if dest.read_bytes() != p.read_bytes():
                raise OSError("the pre-rotation copy of a file did not read back")
            _durable_write(p, fresh)
            done.append(p)
    except Exception as e:
        for p, _fresh in staged:
            try:
                p.with_name(p.name + ".tmp").unlink()
            except OSError:
                pass
        failed = _undo_rotation({"root": str(root), "backup_dir": str(bdir),
                                 "replaced": [str(p) for p in done]})
        report["not_restored"] = failed
        report["backup_dir"] = str(bdir)
        report["error"] = ("%s while re-sealing; %s" % (
            type(e).__name__,
            "every file was put back" if not failed else
            "%d file(s) could NOT be put back (originals in %s)" % (len(failed), bdir)))
        return report
    report.update(ok=True, resealed=len(done), backup_dir=str(bdir), root=str(root),
                  replaced=[str(p) for p in done])
    return report


def _undo_rotation(report: dict) -> list:
    """Put every file a rotation replaced back from its pre-rotation copy.
    Returns the paths that could NOT be put back (also logged); a caller
    never says "every file was put back" unless this is empty."""
    root, bdir = Path(report["root"]), Path(report["backup_dir"])
    failed = []
    for s in report.get("replaced") or []:
        p = Path(s)
        try:
            _durable_write(p, (bdir / p.relative_to(root)).read_bytes())
        except Exception:
            failed.append(s)
    if failed:
        import logging
        logging.getLogger("friday.vault_passphrase").error(
            "[vault] %d file(s) could not be put back from %s; their originals are "
            "still there", len(failed), bdir)
    return failed


# -- Said out loud (services/integrity_report) ---------------------------------
#
# THE UPGRADE CHECK. Every boot (so also the first boot after an upgrade or a
# reinstall) asks two factual questions and says the answer:
#   1. do the passphrase homes disagree, and which one opens the ciphertext?
#   2. does the key this process uses open every sealed file in the home?
# The second walks the home once per process, in the background; until it has
# finished the probe reports only (1).

_SCAN: dict = {}
_SCAN_LOCK = __import__("threading").Lock()


def _scan_sealed() -> None:
    try:
        from agent_friday.privacy import vault_crypto as vc
        from agent_friday.services.agent import _get_vault_key
        key = _get_vault_key()
        files = sealed_files()
        bad = []
        for p in files:
            try:
                if not key:
                    raise ValueError("no vault key")
                vc.decrypt(p.read_bytes(), key)
            except Exception:
                bad.append(p)
        by_dir: dict = {}
        root = Path(core.FRIDAY_DIR)
        for p in bad:
            try:
                top = p.relative_to(root).parts[0]
            except ValueError:
                top = "?"
            by_dir[top] = by_dir.get(top, 0) + 1
        _SCAN.update(done=True, total=len(files), unreadable=len(bad), by_dir=by_dir)
    except Exception as e:
        _SCAN.update(done=True, error=type(e).__name__)


_RESOLVING = __import__("threading").Event()


def _resolve_in_background() -> None:
    try:
        resolve()
    finally:
        _RESOLVING.clear()


def _integrity_probe() -> list:
    """Never derives a key or reads a sealed file on the caller's thread (it
    runs inside /api/health): an unresolved passphrase and the sealed-file
    scan are both done in the background, and reported once known."""
    out = []
    if _CACHE is None:
        if not _RESOLVING.is_set():
            _RESOLVING.set()
            try:
                __import__("threading").Thread(target=_resolve_in_background,
                                               name="vault-resolve", daemon=True).start()
            except Exception:
                _RESOLVING.clear()        # never leave the gate shut on a failed start
        c = {}
    else:
        c = dict(LAST_CONFLICT or {})
    if c.get("distinct_values", 0) > 1 and c.get("opens_nothing"):
        fixed = (" Precedence alone would have used %s; Friday used %s instead, because "
                 "that is the one that opens the data." % (c["precedence_would_pick"], c["chosen"])
                 if c.get("precedence_would_pick") else " Friday is using %s." % c.get("chosen"))
        out.append({"store": "vault passphrase", "state": "passphrase_homes_disagree",
                    "records": None,
                    "message": ("Two different vault passphrases are stored on this PC. %s "
                                "opens the encrypted files; %s opens none of them.%s"
                                % (" and ".join(c.get("opens") or ["none of them"]),
                                   " and ".join(c["opens_nothing"]), fixed))})
    with _SCAN_LOCK:
        if _SCAN.get("done") and "gen" in _SCAN and _SCAN["gen"] != _KEY_GENERATION:
            _SCAN.clear()                        # the key changed: scan again
        start = not _SCAN
        if start:
            _SCAN["running"] = True
            _SCAN["gen"] = _KEY_GENERATION
    if start:
        __import__("threading").Thread(target=_scan_sealed, name="vault-seal-check",
                                       daemon=True).start()
    if _SCAN.get("done") and _SCAN.get("unreadable"):
        where = ", ".join("%s %d" % kv for kv in sorted(_SCAN["by_dir"].items()))
        out.append({"store": "encrypted files", "state": "cannot_decrypt",
                    "records": _SCAN["unreadable"],
                    "message": ("%d of %d encrypted files do not open with the vault key "
                                "Friday is using (%s). They are kept untouched."
                                % (_SCAN["unreadable"], _SCAN["total"], where))})
    return out


try:
    from agent_friday.services import integrity_report as _integrity_report
    _integrity_report.register("vault passphrase", _integrity_probe)
except Exception:
    pass

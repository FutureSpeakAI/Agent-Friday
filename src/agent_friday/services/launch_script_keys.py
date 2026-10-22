"""Provider API keys in Friday's launch scripts move into the credential store.

start.bat (and launch_now.bat, friday_startup.bat) are plain text in the app folder.
Earlier releases and hand setups put provider keys there as `set NAME=VALUE` lines, and
core reads them into the environment at every import. The setup wizard stopped writing
them and moves them when it is run (setup_wizard.migrate_plaintext_keys), but nothing
moved them on an install where the wizard never runs again, which is nearly all of them.

At server boot, before the stored keys are loaded into the environment, this:

  1. reads each launch script beside the app;
  2. for every `set` line whose name is a provider's key variable (the provider
     registry's own auth.key), stores the value in the credential store unless a key
     is already saved there (a key saved in Settings wins: the server already prefers
     it, see credential_store.bootstrap_provider_env), and reads it back;
  3. keeps the whole original script, encrypted, under ~/.friday/providers/legacy/;
  4. blanks those lines IN PLACE: each becomes `rem` and spaces, the same length, so
     a cmd.exe that is running the script right now (it re-reads a batch file by
     offset after every command) continues at the same place;
  5. records an audit event naming the script and the providers, never a value.

Lines that are not provider keys stay: the vault passphrase (FRIDAY_PASSWORD) lives in
services/vault_passphrase and is the credentials lane's; FRIDAY_SECRET_KEY,
FRIDAY_REMOTE_KEY and anything else are left and named in the result.
When the store cannot write, or a saved key does not decrypt on this boot, nothing in
that script changes and the next boot tries again.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

_log = logging.getLogger("friday.launch_script_keys")

SCRIPTS = ("start.bat", "launch_now.bat", "friday_startup.bat")
_MAX_BYTES = 256 * 1024
#: The same shape core._bootstrap_env_from_launch_scripts reads.
_SET_LINE = re.compile(r'^(\s*set\s+"?)([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)("?\s*)$', re.IGNORECASE)


def _app_dir() -> Path:
    import agent_friday.core as core
    return Path(core.__file__).resolve().parents[3]


def _legacy_dir() -> Path:
    import agent_friday.core as core
    return Path(core.FRIDAY_DIR) / "providers" / "legacy"


def _provider_env_names() -> Dict[str, str]:
    names: Dict[str, str] = {}
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        for p in get_provider_registry().list_providers():
            auth = (p or {}).get("auth") or {}
            if auth.get("type") == "env_var" and auth.get("key") and p.get("name"):
                names.setdefault(str(auth["key"]).upper(), str(p["name"]))
    except Exception:
        pass
    return names


def _decode(data: bytes) -> Optional[str]:
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return None              # cmd.exe does not run UTF-16 batch files; not ours
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("mbcs" if os.name == "nt" else "latin-1")
        except Exception:
            return None


def _blank(line: str) -> str:
    """`rem` plus spaces, exactly as long as the line it replaces (in bytes)."""
    n = len(line.encode("utf-8"))
    return "rem" + " " * max(0, n - 3) if n >= 3 else " " * n


def _migrate_one(path: Path) -> dict:
    from agent_friday.services import credential_store as cs
    res: dict = {"file": path.name, "imported": [], "kept_saved": [], "left": [], "changed": False}
    if path.is_symlink() or not path.is_file():
        res["error"] = "not a plain file; left as it is"
        return res
    data = path.read_bytes()
    if len(data) > _MAX_BYTES:
        res["error"] = "larger than a launch script; left as it is"
        return res
    text = _decode(data)
    if text is None:
        res["error"] = "not text this can read; left as it is"
        return res
    env_names = _provider_env_names()
    if not env_names:
        res["error"] = "the provider list could not be read; left as it is"
        return res
    # Keep the file's own line endings: split keeping them.
    lines = text.splitlines(keepends=True)
    blank_idx: List[int] = []
    for i, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        m = _SET_LINE.match(body)
        if not m:
            continue
        name, value = m.group(2).upper(), m.group(3).strip()
        if not value or value.startswith("%"):
            continue
        provider = env_names.get(name)
        if not provider:
            if name not in res["left"]:
                res["left"].append(name)
            continue
        status = cs.provider_key_status(provider)
        if status == "present_but_unreadable":
            res["error"] = "a saved key could not be read on this boot; left as it is"
            return res
        if status == "missing":
            cs.set_provider_key(provider, value)            # raises if the store cannot write
            if cs.get_provider_key(provider) != value:
                raise RuntimeError("a key stored from %s did not read back" % path.name)
            res["imported"].append(provider)
        else:
            # A saved key wins (the server already uses it over this line); the
            # line is a stale plaintext copy, and the original stays in the archive.
            res["kept_saved"].append(provider)
        blank_idx.append(i)
    if not blank_idx:
        return res
    # The whole original, encrypted, before anything in it changes.
    digest = hashlib.sha256(data).hexdigest()[:16]
    dest = _legacy_dir() / ("%s.%s.enc" % (path.name, digest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        cs.write_secret(dest, data)
    if cs.read_secret(dest) != data:
        raise RuntimeError("the encrypted copy of %s did not read back" % path.name)
    for i in blank_idx:
        raw = lines[i]
        body = raw.rstrip("\r\n")
        lines[i] = _blank(body) + raw[len(body):]
    new = "".join(lines)
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    out = bom + new.encode("utf-8")
    if len(out) != len(data):
        raise RuntimeError("the rewritten %s would change length; left as it is" % path.name)
    tmp = path.with_name(path.name + ".friday-tmp")
    tmp.write_bytes(out)
    os.replace(tmp, path)
    after = path.read_bytes()
    left_values = [m.group(3).strip() for ln in after.decode("utf-8-sig", "replace").splitlines()
                   for m in [_SET_LINE.match(ln)] if m and m.group(2).upper() in env_names]
    if any(left_values):
        raise RuntimeError("%s still holds a provider key after the rewrite" % path.name)
    res["changed"] = True
    res["archived_as"] = dest.name
    return res


def migrate(app_dir: Optional[Path] = None) -> List[dict]:
    """Move provider keys out of every launch script beside the app. Never raises;
    one result per script found (names and provider names only). Skipped under the
    test suite unless a folder is given."""
    if app_dir is None and os.environ.get("FRIDAY_TESTING") == "1":
        return []
    base = Path(app_dir) if app_dir is not None else _app_dir()
    results: List[dict] = []
    for name in SCRIPTS:
        p = base / name
        if not (p.exists() or p.is_symlink()):
            continue
        try:
            r = _migrate_one(p)
        except Exception as e:  # noqa: BLE001 - the script stays as it was; next boot retries
            r = {"file": name, "changed": False,
                 "error": "the credential store could not take it (%s); left as it is"
                          % type(e).__name__}
            try:
                tmp = p.with_name(p.name + ".friday-tmp")
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        results.append(r)
        try:
            from agent_friday.services import credential_store as cs
            if r.get("changed") or r.get("error"):
                cs.audit_event("launch_script_keys", "moved" if r.get("changed") else "left",
                               file=r["file"], imported=r.get("imported", []),
                               kept_saved=r.get("kept_saved", []), left=r.get("left", []),
                               archived_as=r.get("archived_as"), error=r.get("error"))
        except Exception:
            pass
        if r.get("changed"):
            _log.info("Moved provider keys out of %s (%d imported, %d already saved)",
                      r["file"], len(r.get("imported", [])), len(r.get("kept_saved", [])))
        elif r.get("error"):
            _log.warning("Left %s as it is: %s", r["file"], r["error"])
    return results

"""Plaintext key files an older release left beside the app are moved into the credential store.

Releases before the credential store kept API keys in ``.env`` and ``secrets.yaml`` in
the app folder. Nothing has read them for a long time, but the installer carries them
across every upgrade (install.ps1 AppUserFiles, update_guard) so they are not lost, and
so a plaintext copy of the owner's keys outlives every fix that removed plaintext
elsewhere (astra progress, drift item 8; NS-34.17-3).

At boot, before the stored keys are loaded into the environment, this:

  1. reads each file that is there;
  2. for every provider whose key variable it names (the provider registry's own
     ``auth.key``), stores the value in the credential store, unless a key is already
     stored there (a key saved in Settings wins), and reads it back to check;
  3. keeps the whole file, encrypted, under ``~/.friday/providers/legacy/`` (lines it
     did not recognise are not thrown away), and reads that back to check;
  4. only then deletes the plaintext file, and records an audit event that names the
     file and the providers, never a value.

Only an installed copy is touched (install-manifest.json beside the app folder); a
source checkout's .env belongs to a developer.

If the credential store cannot write (a locked or failed keystore refuses, BD-08), the
file is left exactly as it was and the next boot tries again. Nothing here prints,
logs or returns a key.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

_log = logging.getLogger("friday.legacy_key_files")

LEGACY_NAMES = (".env", "secrets.yaml")
_MAX_BYTES = 256 * 1024
_ENV_LINE = re.compile(r"^\s*(?:export\s+|set\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*?)\s*$")


def _app_dir() -> Path:
    from agent_friday.services import credential_paths
    return credential_paths._app_dir()


def _legacy_dir() -> Path:
    import agent_friday.core as core
    return Path(core.FRIDAY_DIR) / "providers" / "legacy"


def decode(data: bytes) -> Optional[str]:
    """Text of a key file: UTF-8 (with or without a byte-order mark) or UTF-16 with its
    mark (what PowerShell 5's Out-File writes). None when it is neither."""
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def parse(text: str) -> Dict[str, str]:
    """NAME -> value from KEY=VALUE / KEY: VALUE lines (.env and a flat secrets.yaml).
    Comments, blank lines and indented (nested YAML) lines are skipped; quotes and a
    trailing ' # comment' are removed."""
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("---"):
            continue
        if line[:1] in (" ", "\t"):
            continue                 # a child of some other block, not a top-level key
        m = _ENV_LINE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        q = value[:1]
        if q in ("'", '"') and q in value[1:]:
            value = value[1:value.index(q, 1)]        # "abc" # note -> abc
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if value:
            out[name] = value
    return out


def mentions(text: str, names) -> list:
    """Known key variable names that appear at the start of any line, parsed or not."""
    found = []
    for n in names:
        if re.search(r"(?m)^\s*(?:export\s+|set\s+)?%s\s*[=:]" % re.escape(n), text or ""):
            found.append(n)
    return found


def _provider_env_names() -> Dict[str, str]:
    """env var name -> provider name, from the provider registry."""
    names: Dict[str, str] = {}
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        for p in get_provider_registry().list_providers():
            auth = (p or {}).get("auth") or {}
            if auth.get("type") == "env_var" and auth.get("key") and p.get("name"):
                names.setdefault(str(auth["key"]), str(p["name"]))
    except Exception:
        pass
    return names


def _migrate_one(path: Path) -> dict:
    from agent_friday.services import credential_store as cs
    res: dict = {"file": path.name, "imported": [], "kept_saved": [], "moved": False}
    try:
        if path.is_symlink() or not path.is_file():
            res["error"] = "not a plain file; left as it is"
            return res
        data = path.read_bytes()
    except OSError:
        res["error"] = "could not be read; left as it is"
        return res
    if len(data) > _MAX_BYTES:
        res["error"] = "larger than a key file; left as it is"
        return res
    text = decode(data)
    if text is None:
        res["error"] = "not text this can read; left as it is"
        return res
    env_names = _provider_env_names()
    if not env_names:
        # Without the registry nothing can be recognised; deleting now would leave the
        # keys only in an archive nothing reads.
        res["error"] = "the provider list could not be read; left as it is"
        return res
    values = parse(text)
    unparsed = [n for n in mentions(text, env_names) if not values.get(n)]
    if unparsed:
        res["error"] = "a key line could not be read; left as it is"
        return res
    for env_name, provider in sorted(env_names.items()):
        value = values.get(env_name)
        if not value:
            continue
        # A key file that exists (even one that does not decrypt on this boot) is a
        # key the owner saved: it is never overwritten from an older file.
        status = cs.provider_key_status(provider)
        if status == "present_but_unreadable":
            res["error"] = "a saved key could not be read on this boot; left as it is"
            return res
        if status != "missing":
            res["kept_saved"].append(provider)
            continue
        cs.set_provider_key(provider, value)          # raises if the store cannot write
        if cs.get_provider_key(provider) != value:
            raise RuntimeError("a key stored from %s did not read back" % path.name)
        res["imported"].append(provider)
    # The whole file, encrypted, so nothing in it is lost; then the plaintext goes.
    # One archive per distinct content: a retry after a failed delete adds no copy.
    import hashlib
    digest = hashlib.sha256(data).hexdigest()[:16]
    dest = _legacy_dir() / ("%s.%s.enc" % (path.name.lstrip(".") or "env", digest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        cs.write_secret(dest, data)
    if cs.read_secret(dest) != data:
        raise RuntimeError("the encrypted copy of %s did not read back" % path.name)
    try:
        import os
        import stat
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)   # Copy-Item keeps a read-only bit
    except OSError:
        pass
    path.unlink()
    if path.exists():
        raise RuntimeError("%s could not be removed" % path.name)
    res["moved"] = True
    res["archived_as"] = dest.name
    return res


def migrate(app_dir: Optional[Path] = None) -> List[dict]:
    """Move every legacy key file out of the app folder. Never raises; returns one result
    per file found (names and provider names only)."""
    base = Path(app_dir) if app_dir is not None else _app_dir()
    results: List[dict] = []
    # Only an installed copy (the setup program writes install-manifest.json beside
    # the app folder). A source checkout's .env is a developer's file, not a key
    # file an older Friday left behind, and is never touched.
    if app_dir is None and (not (base.parent / "install-manifest.json").is_file()
                            or (base / ".git").exists()):
        return results
    # The installer's holding folder too: an upgrade interrupted mid-copy leaves the
    # files there (install.ps1 Save-AppUserFiles).
    places = [base / name for name in LEGACY_NAMES]
    places += [base.parent / ".upgrade-keep" / name for name in LEGACY_NAMES]
    for p in places:
        name = p.name
        if not (p.exists() or p.is_symlink()):
            continue
        try:
            r = _migrate_one(p)
        except Exception as e:  # noqa: BLE001 - the file stays; the next boot retries
            r = {"file": name, "moved": False,
                 "error": "the credential store could not take it (%s); left as it is"
                          % type(e).__name__}
        results.append(r)
        try:
            from agent_friday.services import credential_store as cs
            cs.audit_event("legacy_key_file", "moved" if r.get("moved") else "left",
                           file=r["file"], imported=r.get("imported", []),
                           kept_saved=r.get("kept_saved", []),
                           archived_as=r.get("archived_as"), error=r.get("error"))
        except Exception:
            pass
        if r.get("moved"):
            _log.info("Moved the plaintext %s into the credential store (%d key(s) imported)",
                      r["file"], len(r.get("imported", [])))
        else:
            _log.warning("Left the plaintext %s in place: %s", r["file"], r.get("error"))
    return results

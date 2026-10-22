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


def parse(text: str) -> Dict[str, str]:
    """NAME -> value from KEY=VALUE / KEY: VALUE lines (.env and a flat secrets.yaml).
    Comments, blank lines and nested YAML blocks are skipped; quotes are removed."""
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("---"):
            continue
        m = _ENV_LINE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        if " #" in value and not value.startswith(("'", '"')):
            value = value.split(" #", 1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            out[name] = value
    return out


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
    values = parse(data.decode("utf-8", errors="replace"))
    for env_name, provider in sorted(_provider_env_names().items()):
        value = values.get(env_name)
        if not value:
            continue
        if cs.get_provider_key(provider):
            res["kept_saved"].append(provider)
            continue
        cs.set_provider_key(provider, value)          # raises if the store cannot write
        if cs.get_provider_key(provider) != value:
            raise RuntimeError("a key stored from %s did not read back" % path.name)
        res["imported"].append(provider)
    # The whole file, encrypted, so nothing in it is lost; then the plaintext goes.
    stamp = time.strftime("%Y%m%dT%H%M%S")
    dest = _legacy_dir() / ("%s.%s.enc" % (path.name.lstrip(".") or "env", stamp))
    dest.parent.mkdir(parents=True, exist_ok=True)
    cs.write_secret(dest, data)
    if cs.read_secret(dest) != data:
        raise RuntimeError("the encrypted copy of %s did not read back" % path.name)
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
    if app_dir is None and not (base.parent / "install-manifest.json").is_file():
        return results
    for name in LEGACY_NAMES:
        p = base / name
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

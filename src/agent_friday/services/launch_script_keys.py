"""Provider API keys in Friday's launch scripts move into the credential store.

start.bat (and launch_now.bat, friday_startup.bat) are plain text in the app folder.
Earlier releases and hand setups put provider keys there as `set NAME=VALUE` lines, and
core reads them into the environment at every import. The setup wizard stopped writing
them and moves them when it is run (setup_wizard.migrate_plaintext_keys), but nothing
moved them on an install where the wizard never runs again, which is nearly all of them.

At server boot, before the stored keys are loaded into the environment, this:

  1. reads each launch script beside the app;
  2. for every plain `set` line whose name is a provider's key variable (the provider
     registry's own auth.key), stores the value in the credential store when none is
     saved, and reads it back. A line is LEFT (and named) when a different key is
     already saved (which is newer is not knowable), when the same name has two
     different values anywhere in the scripts, or when the line is more than a plain
     assignment (&, |, <, >, parentheses, ^, %);
     every later import puts the moved keys back into the environment from the store
     with the precedence the line had (restore_moved_keys, called by core), so the
     server, the CLI and the voice path see them exactly as before;
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


#: Only an API key variable of a built-in provider is ever moved or restored. A custom
#: provider descriptor can name any variable (FRIDAY_PASSWORD, FRIDAY_SECRET_KEY): those
#: are never touched here.
_KEY_NAME = re.compile(r"^[A-Z0-9_]+_API_KEY$")


def _provider_env_names() -> Dict[str, str]:
    names: Dict[str, str] = {}
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        reg = get_provider_registry()
        for p in reg.list_providers():
            auth = (p or {}).get("auth") or {}
            if not (auth.get("type") == "env_var" and auth.get("key") and p.get("name")):
                continue
            env = str(auth["key"]).upper()
            if not _KEY_NAME.match(env) or env.startswith("FRIDAY_"):
                continue
            try:
                if reg.provider_origin(p["name"]) != "builtin":
                    continue
            except Exception:
                continue
            names.setdefault(env, str(p["name"]))
    except Exception:
        pass
    return names


#: Characters that mean a `set` line is more than a plain assignment (a chained
#: command, a block, an escape, a variable reference): never moved or blanked.
_UNSAFE_VALUE = re.compile(r"[&|<>()^%]")
#: Names (only names) of the variables this moved, so the store can stand in for the
#: script at every later import (restore_moved_keys) exactly as the script did.
_MOVED_FILE = "launch-script-moved.json"


def _moved_path() -> Path:
    return _legacy_dir() / _MOVED_FILE


def moved_names() -> Dict[str, str]:
    """env name -> provider for every key this has moved out of a launch script."""
    try:
        import json
        data = json.loads(_moved_path().read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in (data.get("names") or {}).items()}
    except Exception:
        return {}


def _remember_moved(pairs: Dict[str, str]) -> None:
    import json
    cur = moved_names()
    cur.update(pairs)
    path = _moved_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"names": cur}, indent=1), encoding="utf-8")
    os.replace(tmp, path)                  # a crash never leaves half a file


def _trusted_moved() -> Dict[str, str]:
    """moved_names(), keeping only pairs the registry itself confirms (the variable is
    that built-in provider's API key variable). The file is a record, never an
    instruction: a name written into it by anything else is ignored."""
    current = _provider_env_names()
    return {n: p for n, p in moved_names().items() if current.get(n) == p}


def _keystore_needs_passphrase() -> bool:
    try:
        from agent_friday.services import keystore as _ks
        doc = _ks._read_file()
        return bool(doc) and (doc.get("wrap") or "none") == "passphrase"
    except Exception:
        return True                        # unknown: do not risk a prompt or a slow unwrap


def restore_moved_keys(already_loaded=(), force_names=()) -> List[str]:
    """Called by core at import, right after it reads the launch scripts: every key this
    moved out of a script is put back into the environment FROM THE STORE, with the same
    precedence the script line had (a force name beats a stale Windows variable; any other
    name fills only an empty one). Returns the names set. Never raises; never logs a value."""
    if not moved_names():
        return []
    # A passphrase-wrapped keystore is unlocked by the server, not at every import
    # (that would mean a key derivation, or a keychain prompt, per CLI command); the
    # server's own bootstrap then loads these keys (refresh_core_constants).
    if _keystore_needs_passphrase():
        return []
    names = _trusted_moved()
    done = []
    try:
        from agent_friday.services import credential_store as cs
    except Exception:
        return []
    for env_name, provider in names.items():
        if env_name in already_loaded:
            continue                        # a script still sets it: that wins, as before
        if env_name not in force_names and os.environ.get(env_name):
            continue
        try:
            value = cs.get_provider_key(provider)
        except Exception:
            value = None
        if value:
            os.environ[env_name] = value
            done.append(env_name)
    # Reading the store may have resolved the vault passphrase before core finished
    # loading its launch-script variables; nothing resolved here is kept.
    try:
        from agent_friday.services import vault_passphrase as _vp
        _vp.reset_cache()
    except Exception:
        pass
    try:
        cs._VAULT_KEY, cs._VAULT_KEY_READY = None, False
    except Exception:
        pass
    return done


def refresh_core_constants() -> None:
    """After the server's own key bootstrap: core constants computed at import (e.g.
    core.GEMINI_API_KEY) take the environment value when they are empty."""
    try:
        import agent_friday.core as core
        for env_name in _trusted_moved():
            if hasattr(core, env_name) and not getattr(core, env_name) and os.environ.get(env_name):
                setattr(core, env_name, os.environ[env_name])
    except Exception:
        pass


def _blank(line: str) -> str:
    """`rem` plus spaces, exactly as long as the line it replaces (in bytes)."""
    n = len(line.encode("utf-8"))
    return "rem" + " " * max(0, n - 3) if n >= 3 else " " * n


def _read_script(path: Path):
    """(bytes, text) of a launch script, or (None, why)."""
    if path.is_symlink() or not path.is_file():
        return None, "not a plain file; left as it is"
    data = path.read_bytes()
    if len(data) > _MAX_BYTES:
        return None, "larger than a launch script; left as it is"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # A script in the ANSI code page: rewriting it as UTF-8 would change its
        # bytes; it is left as it is.
        return None, "not UTF-8 text; left as it is"
    return data, text


def _scan(text: str, env_names: Dict[str, str]):
    """[(line index, ENV NAME, value)] for plain provider-key `set` lines, and the
    names of other `set` lines (left)."""
    hits, left = [], []
    for i, raw in enumerate(text.splitlines(keepends=True)):
        body = raw.rstrip("\r\n")
        m = _SET_LINE.match(body)
        if not m:
            continue
        name, value = m.group(2).upper(), m.group(3).strip()
        if not value:
            continue
        if name not in env_names:
            if name not in left:
                left.append(name)
            continue
        if _UNSAFE_VALUE.search(value) or value.endswith("^"):
            if name not in left:
                left.append(name)
            hits.append((i, name, None))       # None: an unsafe line for this name
            continue
        hits.append((i, name, value))
    return hits, left


def _rewrite(path: Path, data: bytes, text: str, blank_idx: List[int]) -> bytes:
    lines = text.splitlines(keepends=True)
    for i in blank_idx:
        raw = lines[i]
        body = raw.rstrip("\r\n")
        lines[i] = _blank(body) + raw[len(body):]
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    out = bom + "".join(lines).encode("utf-8")
    if len(out) != len(data):
        raise RuntimeError("the rewritten %s would change length" % path.name)
    return out


def _cleanup_tmp(base: Path) -> None:
    for name in SCRIPTS:
        tmp = base / (name + ".friday-tmp")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def migrate(app_dir: Optional[Path] = None) -> List[dict]:
    """Move provider keys out of every launch script beside the app. Never raises;
    one result per script found (names and provider names only). Skipped under the
    test suite unless a folder is given."""
    if app_dir is None and os.environ.get("FRIDAY_TESTING") == "1":
        return []
    base = Path(app_dir) if app_dir is not None else _app_dir()
    _cleanup_tmp(base)                     # a hard kill mid-rewrite leaves one behind
    env_names = _provider_env_names()
    found = []
    for name in SCRIPTS:
        p = base / name
        if p.exists() or p.is_symlink():
            data, text = _read_script(p)
            found.append((p, data, text))
    if not found:
        return []
    # Every value each provider-key name has, across all three scripts and every line:
    # a name with two different values (an old key above a new one) is not moved
    # anywhere, because which one the owner means is not ours to guess.
    values: Dict[str, set] = {}
    scans = {}
    unreadable = any(data is None for _p, data, _t in found)
    for p, data, text in found:
        if data is None or not env_names:
            continue
        hits, left = _scan(text, env_names)
        scans[p] = ([h for h in hits if h[2] is not None], left)
        for _i, n, v in hits:
            values.setdefault(n, set()).add(v)
    # Two values, or a line for the same name this cannot move (None), make the name
    # conflicting: blanking the plain line would hand the process the other one.
    conflicting = {n for n, vs in values.items() if len(vs) > 1 or None in vs}
    if unreadable:
        # A script this cannot read may set any of these names; moving them from the
        # others could change which key the process ends up with.
        conflicting |= set(env_names)
    results: List[dict] = []
    moved_pairs: Dict[str, str] = {}
    from agent_friday.services import credential_store as cs
    for p, data, text in found:
        res: dict = {"file": p.name, "imported": [], "kept_saved": [], "left": [], "changed": False}
        if data is None:
            res["error"] = text
            results.append(res)
            continue
        if not env_names:
            res["error"] = "the provider list could not be read; left as it is"
            results.append(res)
            continue
        hits, left = scans[p]
        res["left"] = list(left)
        try:
            # Decide everything first; nothing is stored until the rewrite is known
            # to be possible.
            plan = []                          # (index, env name, provider, value, action)
            for i, n, v in hits:
                prov = env_names[n]
                if n in conflicting:
                    res["left"].append(n + " (two different values)")
                    continue
                status = cs.provider_key_status(prov)
                if status == "present_but_unreadable":
                    raise _Leave("a saved key could not be read on this boot")
                if status == "missing":
                    plan.append((i, n, prov, v, "import"))
                elif (cs.get_provider_key(prov) or "") == v:
                    plan.append((i, n, prov, v, "same"))
                else:
                    # A different key is saved: which is newer is not knowable here.
                    res["left"].append(n + " (differs from the saved key)")
            if not plan:
                results.append(res)
                continue
            out = _rewrite(p, data, text, sorted({x[0] for x in plan}))
            # The whole original, encrypted, before anything changes.
            digest = hashlib.sha256(data).hexdigest()[:16]
            dest = _legacy_dir() / ("%s.%s.enc" % (p.name, digest))
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                cs.write_secret(dest, data)
            if cs.read_secret(dest) != data:
                raise RuntimeError("the encrypted copy did not read back")
            for _i, n, prov, v, action in plan:
                if action == "import" and prov not in res["imported"]:
                    if cs.provider_key_status(prov) == "missing":
                        cs.set_provider_key(prov, v)
                    if cs.get_provider_key(prov) != v:
                        raise RuntimeError("a stored key did not read back")
                    res["imported"].append(prov)
                elif action == "same" and prov not in res["kept_saved"]:
                    res["kept_saved"].append(prov)
                moved_pairs[n] = prov
            # Nobody changed the file while this ran (the wizard scrubs it too).
            if p.read_bytes() != data:
                raise _Leave("the script changed while this ran")
            _remember_moved(moved_pairs)
            tmp = p.with_name(p.name + ".friday-tmp")
            tmp.write_bytes(out)
            os.replace(tmp, p)
            res["changed"] = True
            res["archived_as"] = dest.name
        except _Leave as why:
            res["error"] = "%s; left as it is" % why
        except Exception as e:  # noqa: BLE001 - the script stays as it was; next boot retries
            res["error"] = "it could not be moved (%s); left as it is" % type(e).__name__
        finally:
            _cleanup_tmp(base)
        results.append(res)
    for r in results:
        try:
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


class _Leave(Exception):
    """A reason to leave a script exactly as it is."""

"""vault_key_doctor -- which vault passphrase opens this Friday's data, and repair.

The passphrase homes (OS keychain, the protected DPAPI file, the environment,
the launcher) can hold different values, and only one of them may open the
sealed files. This tool says which, and makes them agree. See
services/vault_passphrase.py (resolve, keychain_is_this_homes, store).

MODES
  (default)            READ-ONLY report: each passphrase home (present, which
                       group of equal values), which group opens the sealed
                       files, how many files each opens, the knowledge graph's
                       sealed files and their record counts.
  --repair-keychain    Make every durable home hold the passphrase that opens
                       the data. Each home's previous, different value (the
                       keychain's and the protected file's) is first kept,
                       DPAPI-wrapped, at security/stray-passphrase-<stamp>.dpapi
                       and read back (vault_passphrase.keep_displaced).
  --recover-kg         Re-seal knowledge-graph sealed files that open only with
                       a non-current key under the current key, and restore a
                       kept copy (*.sensitive.kept-*.json) only where its live
                       file is absent; beside a live file the current key opens,
                       a kept copy is retired unmerged (records missing from the
                       live file may have been removed on purpose) and counted.
                       Retired copies and replaced files go to
                       security/kg-pre-recover-<stamp>/ under unique names, read
                       back. Runs before --repair-keychain.
  --rotate             Change the vault passphrase: every sealed file is
                       re-sealed under the new one (originals kept in
                       security/pre-rotation-<stamp>/), the credential keystore
                       follows, then the homes are written. The new passphrase
                       is asked for twice at the prompt, never on the command
                       line. This is the ONLY way to rotate.

Importing agent_friday touches the account: see _prepare_env, which sets
FRIDAY_HOME before the import (so the legacy ~/wiki merge does not run) and
leaves nothing from a launch script in os.environ.

Every mode that writes refuses to run while Friday is running for this home:
the server's single-instance lock (friday_server.lock) is held, the tray's
session mutex exists, or a server answers on its port. While a repair runs,
the doctor holds that same lock, so a server cannot start under it.
Nothing here prints a passphrase, a key or a record.

usage: python scripts/vault_key_doctor.py [--repair-keychain] [--recover-kg]
                                          [--rotate] [--port 3000] [--json out.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# -- Before anything imports agent_friday ------------------------------------------

def _prepare_env() -> None:
    """Importing agent_friday.core has two side effects on this account:
    it copies SET lines from the repo's launch scripts (start.bat,
    launch_now.bat, friday_startup.bat) into os.environ, and, when FRIDAY_HOME
    is unset, it runs the legacy ~/wiki merge against the real home.

    So, before the first import: FRIDAY_HOME is set to the home this tool
    works on (the account's ~/.friday unless FRIDAY_HOME already names
    another), which turns the merge off; and right after it, os.environ is put
    back exactly as it was, so nothing from a launch script stays in this
    process's environment. A launch script's vault passphrase is still a
    passphrase home, so it is handed to vault_passphrase directly (as its
    start.bat reader), never through the environment.

    Does nothing when agent_friday.core is already imported (a test)."""
    if "agent_friday.core" in sys.modules:
        return
    os.environ.setdefault("FRIDAY_HOME", str(Path.home() / ".friday"))
    snapshot = dict(os.environ)
    import agent_friday.core as core
    _restore_env(snapshot, core)


def _restore_env(snapshot: dict, core) -> None:
    """Put os.environ back to ``snapshot`` and forget what the launch scripts
    loaded; a launch script's vault passphrase reaches vault_passphrase as its
    start.bat reader instead of through the environment."""
    launched = {k: os.environ.get(k, "") for k in getattr(core, "ENV_FROM_LAUNCH_SCRIPTS", set())}
    for k in list(os.environ):
        if k not in snapshot:
            del os.environ[k]
    for k, v in snapshot.items():
        if os.environ.get(k) != v:
            os.environ[k] = v
    core.ENV_FROM_LAUNCH_SCRIPTS = set()
    value = (launched.get("FRIDAY_VAULT_PASSPHRASE") or launched.get("FRIDAY_PASSWORD") or "").strip()
    if value:
        from agent_friday.services import vault_passphrase as vp
        original = vp._from_start_bat
        vp._from_start_bat = lambda: original() or value


# -- Is Friday running for this home? ------------------------------------------

def _server_lock_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "friday_server.lock"


def take_server_lock():
    """Take the server's single-instance lock (server._acquire_single_instance_lock's
    protocol: 1 byte at offset 0, msvcrt / flock). The open handle, or None when
    a live process holds it. The caller keeps the handle while it writes."""
    path = _server_lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+b")
    except OSError:
        return None
    try:
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def release_server_lock(fh) -> None:
    if fh is None:
        return
    try:
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    fh.close()


def _tray_running() -> bool:
    """True when the tray's session mutex (friday_tray._claim_windows_mutex)
    exists. Opened, never created, so the check cannot claim it."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenMutexW.restype = ctypes.c_void_p
        h = k32.OpenMutexW(0x00100000, False, r"Local\AgentFridayTray")   # SYNCHRONIZE
        if h:
            k32.CloseHandle(ctypes.c_void_p(h))
            return True
    except Exception:
        pass
    return False


def _port_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _ports(extra: int | None) -> list:
    out = []
    try:
        from agent_friday import paths
        out += [paths.server_port(), paths.configured_server_port()]
    except Exception:
        pass
    if extra:
        out.append(extra)
    return sorted({p for p in out if p})


def running_reasons(port: int | None = None) -> list:
    """Why Friday counts as running for this home; [] when it is stopped."""
    why = []
    fh = take_server_lock()
    if fh is None:
        why.append("a Friday server holds %s" % _server_lock_path().name)
    else:
        release_server_lock(fh)
    if _tray_running():
        why.append("the Friday tray is running (it restarts the server)")
    for p in _ports(port):
        if _port_answers(p):
            why.append("something answers on port %d" % p)
    return why


# -- Report ---------------------------------------------------------------------

def report() -> dict:
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import vault_passphrase as vp
    import agent_friday.core as core

    salt = vc.load_salt(Path(core.FRIDAY_DIR) / "vault" / ".vault_config.json")
    homes = vp._candidates()
    groups: dict = {}
    rows = []
    for value, src in homes:
        fp = hashlib.sha256(b"doctor|" + value.encode()).digest()
        g = groups.setdefault(fp, {"name": "P%d" % (len(groups) + 1), "value": value})
        rows.append({"home": src, "group": g["name"]})
    keys = {g["name"]: vc.derive_key(g["value"], salt) for g in groups.values()}
    files = vp.sealed_files(full=True)
    opens = {name: 0 for name in keys}
    none = 0
    kg = {}
    for p in files:
        blob = p.read_bytes()
        hit = None
        for name, k in keys.items():
            try:
                pt = vc.decrypt(blob, k)
                hit = name
                break
            except Exception:
                continue
        if hit:
            opens[hit] += 1
        else:
            none += 1
        if p.parent.name == "knowledge-graph":
            n = None
            if hit:
                try:
                    n = len(json.loads(pt.decode("utf-8")))
                except Exception:
                    n = None
            kg[p.name] = {"opens_with": hit, "records": n}
    chosen = vp.resolve(use_cache=False)[1]
    return {"homes": rows, "sealed_files": len(files), "opened_by": opens,
            "opened_by_nothing": none, "knowledge_graph": kg,
            "friday_would_use": chosen, "conflict": vp.LAST_CONFLICT}


# -- Repairs (Friday stopped, server lock held) ----------------------------------

def repair_keychain() -> dict:
    """Make every durable home hold the passphrase that opens the data.
    vault_passphrase.store() keeps each home's differing value (keychain AND
    protected file), read back, before it overwrites it, or refuses."""
    from agent_friday.services import vault_passphrase as vp
    working = vp._working_passphrase()
    if not working:
        return {"ok": False, "detail": "no stored passphrase opens the data; nothing changed"}
    before = {label for label, _ in vp.kept_values()}
    written = vp.store(working)                 # same value as the data: no rotation
    vp.reset_cache()
    out = {"ok": bool(written), "wrote": written,
           "kept": [label for label, _ in vp.kept_values() if label not in before],
           "now_used": vp.resolve(use_cache=False)[1]}
    if not written and vp.LAST_STORE_REFUSAL:
        out["detail"] = vp.LAST_STORE_REFUSAL
    return out


def rotate(new: str) -> dict:
    from agent_friday.services import vault_passphrase as vp
    written = vp.store(new, rotate=True)
    if not written:
        return {"ok": False, "detail": vp.LAST_STORE_REFUSAL or "nothing durable was written"}
    return {"ok": True, "wrote": written}


def _backup(src: Path, bdir: Path) -> Path:
    """Copy ``src`` into ``bdir`` under a name no other backup uses, and read
    it back. Raises OSError when the copy is not identical."""
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / src.name
    n = 1
    while dest.exists():
        dest = bdir / ("%s.%d" % (src.name, n))
        n += 1
    shutil.copy2(src, dest)
    if dest.read_bytes() != src.read_bytes():
        raise OSError("the backup of %s did not read back" % src.name)
    return dest


def recover_kg() -> dict:
    """Bring the knowledge graph's sealed files under the current key.

    1. A live ``<name>.sensitive.json`` the current key does not open, but
       another known key does, is re-sealed in place under the current key.
    2. A kept copy (``*.sensitive.kept-*.json``) is restored only where its
       live file is ABSENT (a save made with no key dropped it). Where a live
       file the current key opens exists, the kept copy is NOT merged: the
       live file is the truth, and records missing from it may have been
       removed on purpose (a deleted document, a forget, a correction), so
       merging would bring them back. That kept copy is retired unmerged and
       its record count reported. A live file the current key cannot open is
       never touched, and its kept copy stays.
       Retired means moved into the backup folder
       (security/kg-pre-recover-<stamp>/, which the boot scan skips), so it
       is not applied again and not counted as unreadable.
    Every file replaced or retired is first copied to that folder under a
    unique name and read back. Nothing prints a record or a key."""
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services.agent import _get_vault_key
    from agent_friday.services.knowledge_graph import store as st
    from agent_friday.services import vault_passphrase as vp
    import agent_friday.core as core
    cur = _get_vault_key()
    if not cur:
        return {"ok": False, "detail": "no current vault key"}
    kgdir = Path(st.KG_DIR)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    bdir = Path(core.FRIDAY_DIR) / "security" / ("kg-pre-recover-%s" % stamp)
    n = 1
    while bdir.exists():
        bdir = bdir.with_name("kg-pre-recover-%s-%d" % (stamp, n))
        n += 1
    done, left, retired = [], [], []

    def seal(recs) -> bytes:
        fresh = vc.encrypt(json.dumps(recs, ensure_ascii=False).encode("utf-8"), cur)
        if json.loads(vc.decrypt(fresh, cur).decode("utf-8")) != recs:
            raise ValueError("round-trip mismatch")
        return fresh

    def opens_with_current(raw: bytes):
        try:
            return json.loads(vc.decrypt(raw, cur).decode("utf-8")) or []
        except Exception:
            return None

    try:
        for p in sorted(kgdir.glob("*.sensitive.json")):
            raw = p.read_bytes()
            if not vc.is_encrypted(raw) or opens_with_current(raw) is not None:
                continue
            recs, via = st._open_sealed(raw)
            if recs is None:
                left.append(p.name)
                continue
            fresh = seal(recs)
            _backup(p, bdir)
            vp._durable_write(p, fresh)
            done.append({"file": p.name, "records": len(recs), "was_sealed_with": via})

        for k in sorted(kgdir.glob("*.sensitive.kept-*.json")):
            raw = k.read_bytes()
            if not vc.is_encrypted(raw):
                continue
            recs, via = st._open_sealed(raw)
            if recs is None:
                left.append(k.name)
                continue
            target = k.with_name(k.name.split(".sensitive.kept-")[0] + ".sensitive.json")
            if target.exists():
                if opens_with_current(target.read_bytes()) is None:
                    left.append(k.name)          # its live file is not readable yet
                    continue
                _backup(k, bdir)
                k.unlink()                       # retired unmerged: the live file wins
                retired.append({"file": target.name, "kept_copy": k.name,
                                "records_not_merged": len(recs), "was_sealed_with": via})
                continue
            vp._durable_write(target, seal(recs))
            _backup(k, bdir)
            k.unlink()                           # retired: its copy is in bdir
            done.append({"file": target.name, "from_kept": k.name, "records": len(recs),
                         "was_sealed_with": via})
    except (OSError, ValueError) as e:
        return {"ok": False, "detail": "%s; stopped (backups in %s)" % (e, bdir),
                "resealed": done, "retired_unmerged": retired, "still_unreadable": left}
    return {"ok": True, "resealed": done, "retired_unmerged": retired,
            "still_unreadable": left,
            "backup_dir": str(bdir) if (done or retired) else None}


def _ask_new_passphrase() -> str:
    import getpass
    a = getpass.getpass("New vault passphrase: ")
    b = getpass.getpass("Again: ")
    if not a or a != b:
        return ""
    return a


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repair-keychain", action="store_true")
    ap.add_argument("--recover-kg", action="store_true")
    ap.add_argument("--rotate", action="store_true")
    ap.add_argument("--port", type=int, default=3000)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    _prepare_env()
    writes = a.repair_keychain or a.recover_kg or a.rotate
    lock = None
    if writes:
        why = running_reasons(a.port)
        lock = take_server_lock() if not why else None
        if why or lock is None:
            print("Friday is running (%s): stop it first; repairs never run beside "
                  "a live Friday." % ("; ".join(why) or "its server lock is held"))
            return 2
    try:
        new = ""
        if a.rotate:
            new = _ask_new_passphrase()
            if not new:
                print("The passphrases were empty or did not match; nothing changed.")
                return 1
        out = {"report": report()}
        # Recovery first: it re-seals with the keys the homes hold now, before
        # a keychain repair changes them (the repair keeps what it replaces,
        # and recovery reads kept values too, so either order loses nothing).
        if a.recover_kg:
            out["recover_kg"] = recover_kg()
        if a.repair_keychain:
            out["repair_keychain"] = repair_keychain()
        if a.rotate:
            out["rotate"] = rotate(new)
            new = ""
        text = json.dumps(out, indent=1)
        print(text)
        if a.json:
            Path(a.json).write_text(text, encoding="utf-8")
        return 0
    finally:
        release_server_lock(lock)


if __name__ == "__main__":
    sys.exit(main())

"""Versioned migrations of Friday's data folder (north star §28.8-1: versioned,
idempotent, backed up first, resumable, logged, integrity-checked; §37.3-11: a
migration receipt).

The data folder's schema number lives in ``<home>/schema.json``
({"schema": N, "stores": {...}}): N is the number of the last migration
applied (0 when the file is absent; a folder Friday has just made has none,
so migration 1 runs at its next start, and every migration must accept a
folder already in its new shape). Each Migration in MIGRATIONS has a number,
the files it may change (relative paths; folders end in "/"), an
``apply(home)`` that is safe to run again, and a ``check(home)``.

``run_pending()`` runs at start, after an interrupted restore is settled and
before Friday uses its data folder:

1. under a lock beside the data folder, and never while a restore holds its
   lock; a touched path that is a link or a junction stops it (nothing is
   copied through a link);
2. the files the pending migrations may change are copied beside the folder
   (``<folder>.pre-migration-<to>-<stamp>``) before the first step; the newest
   three such copies are kept;
3. each step is journaled (``<folder>.migration-journal.json``) with a
   fingerprint of the touched files; a start that finds a journal resumes at
   the step the schema reached, and only if the touched files are as that
   step left them;
4. after each step its check runs, then schema.json moves to its number;
5. a receipt file goes into ``<home>/migrations/`` at once, and into the
   action ledger after the start (flush_receipts), so the ledger and its key
   are not touched before Friday is up;
6. a step that fails, or whose check fails, puts the copied files back
   (journaled first, so a stop in the middle is finished at the next start),
   leaves schema.json where it was, and this start is safe mode. If the files
   cannot be put back, Friday does not start, and the journal names the copy.

A folder from a newer Friday (schema above what this Friday knows) is never
touched (safe mode for that start; the next start looks again). A migration
that failed, was cut off unusably, or found a link in its way keeps Friday in
safe mode until the owner leaves it with a press on the Doctor page
(safe_mode.set_next_start with reason "migration_failed"); the next start still
tries the migration again.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SCHEMA_FILE = "schema.json"
JOURNAL_SUFFIX = ".migration-journal.json"
LOCK_SUFFIX = ".migration.lock"
BACKUP_INFIX = ".pre-migration-"
MARKER_REASON = "migration_failed"      # safe_mode's marker: left only by a press on the PC
KEEP_BACKUPS = 3


@dataclass(frozen=True)
class Migration:
    number: int
    name: str
    touches: tuple
    apply: Callable[[Path], None]
    check: Callable[[Path], bool]
    describe: str = ""


# ── the migrations ──────────────────────────────────────────────────────────

def _m1_apply(home: Path) -> None:
    """Record each store's version in schema.json (the starting point every
    later migration reads). Bookkeeping only: a store inventory that cannot be
    read is recorded as such, never a failure."""
    cur = _read_schema(home)
    try:
        from agent_friday.services import store_inventory
        inv = store_inventory.public(store_inventory.inventory(home))
        cur["stores"] = {r["store"]: r["version"] for r in inv["stores"] if r.get("durable", True)}
        cur["inventory_version"] = inv.get("inventory_version")
    except Exception as e:  # noqa: BLE001
        cur["stores"] = {}
        cur["stores_unreadable"] = type(e).__name__
    _write_json(home / SCHEMA_FILE, cur)


def _m1_check(home: Path) -> bool:
    return isinstance(_read_schema(home).get("stores"), dict)


MIGRATIONS: list = [
    Migration(1, "record store versions", (SCHEMA_FILE,), _m1_apply, _m1_check,
              "the version of every store, recorded in schema.json"),
]


def latest() -> int:
    return max((m.number for m in MIGRATIONS), default=0)


# ── small helpers ───────────────────────────────────────────────────────────

def _replace(src: Path, dst: Path) -> None:
    for n in range(12):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or n == 11:
                raise
            time.sleep(min(2.0, 0.05 * (2 ** n)))      # a scanner holding the file


def _write_json(p: Path, data) -> None:
    tmp = p.with_name(p.name + ".partial-" + uuid.uuid4().hex[:6])
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    _replace(tmp, p)


def _read_schema(home: Path) -> dict:
    try:
        d = json.loads((home / SCHEMA_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def current(home: Path) -> int:
    v = _read_schema(home).get("schema", 0)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _journal_path(home: Path) -> Path:
    return home.parent / (home.name + JOURNAL_SUFFIX)


def _journal(home: Path, jr: dict) -> None:
    _write_json(_journal_path(home), jr)
    _journal_step(jr.get("step"))


def _journal_step(step) -> None:
    """Called after each step is recorded (the crash tests stop here)."""


def _read_journal(home: Path) -> Optional[dict]:
    try:
        d = json.loads(_journal_path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _is_link(p: Path) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _links_in(home: Path, touches: list) -> list:
    """Touched paths that are, or pass through, a link or a junction."""
    bad = []
    for rel in touches:
        p = home
        for part in rel.rstrip("/").split("/"):
            p = p / part
            if _is_link(p):
                bad.append(rel)
                break
        else:
            q = home / rel.rstrip("/")
            if q.is_dir():
                for root, dirs, files in os.walk(q):
                    if any(_is_link(Path(root) / n) for n in dirs + files):
                        bad.append(rel)
                        break
    return bad


def _fingerprint(home: Path, touches: list) -> str:
    """Names, sizes and contents of every touched file, as one hash."""
    h = hashlib.sha256()
    for rel in sorted(touches):
        q = home / rel.rstrip("/")
        files = ([q] if q.is_file() else sorted(p for p in q.rglob("*") if p.is_file()) if q.is_dir() else [])
        h.update(("%s:%d\n" % (rel, len(files))).encode())
        for f in files:
            h.update(str(f.relative_to(home)).replace("\\", "/").encode())
            with open(f, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
    return h.hexdigest()


def _copy_aside(home: Path, touches: list, dest: Path) -> list:
    """Copy each touched file or folder that exists into `dest`; returns the
    relative paths copied (absent ones are recorded as absent)."""
    copied = []
    for rel in touches:
        src = home / rel.rstrip("/")
        if not src.exists():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        tgt = dest / rel.rstrip("/")
        tgt.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, tgt)
        else:
            shutil.copy2(src, tgt)
        copied.append(rel)
    return copied


def _put_back(home: Path, touches: list, copied: list, dest: Path) -> None:
    """Make every touched path what was copied (absent ones removed). Safe to
    run again: each path is replaced whole from the copy."""
    for rel in touches:
        cur = home / rel.rstrip("/")
        if rel in copied:
            src = dest / rel.rstrip("/")
            if not src.exists():
                raise FileNotFoundError("the saved copy of %s is missing" % rel)
            staged = cur.with_name(cur.name + ".restoring-" + uuid.uuid4().hex[:6])
            if src.is_dir():
                shutil.copytree(src, staged)
            else:
                shutil.copy2(src, staged)
            if cur.is_dir():
                shutil.rmtree(cur)
            elif cur.exists():
                cur.unlink()
            _replace(staged, cur)
        elif cur.is_dir():
            shutil.rmtree(cur)
        elif cur.exists():
            cur.unlink()


def _prune_backups(home: Path) -> None:
    olds = sorted((p for p in home.parent.iterdir()
                   if p.is_dir() and p.name.startswith(home.name + BACKUP_INFIX) and not _is_link(p)),
                  key=lambda p: p.name)
    for p in olds[:-KEEP_BACKUPS]:
        shutil.rmtree(p, ignore_errors=True)


def _receipt(home: Path, rec: dict) -> None:
    """The receipt into the folder now; into the ledger after the start."""
    d = home / "migrations"
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / ("receipt-%s-to-%s-%s.json" % (rec["from"], rec["to"], time.strftime("%Y%m%d-%H%M%S"))),
                dict(rec, ledgered=False))


def flush_receipts(home: Optional[Path] = None) -> int:
    """Put each receipt not yet in the action ledger there (called once
    Friday is up). Returns how many went in."""
    from agent_friday.paths import friday_home
    from agent_friday.services import action_ledger
    home = Path(home or friday_home())
    n = 0
    for p in sorted((home / "migrations").glob("receipt-*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("ledgered"):
            continue
        body = {k: v for k, v in rec.items() if k != "ledgered"}
        if action_ledger.try_append("migration", body) is not None:
            rec["ledgered"] = True
            _write_json(p, rec)
            n += 1
    return n


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.fh = None
        self.held = False
        self.denied = None

    def __enter__(self):
        try:
            self.fh = open(self.path, "a+b")
        except OSError as e:
            self.denied = type(e).__name__
            return self
        try:
            self.fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.held = True
        except OSError:
            self.held = False
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            try:
                if self.held:
                    self.fh.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self.fh.close()


# ── the runner ──────────────────────────────────────────────────────────────

def run_pending(home: Optional[Path] = None) -> Optional[dict]:
    """Bring the data folder to the latest schema. None when nothing was to
    do. "safe_mode": true means this start must be safe mode; "stop": true
    means Friday must not start on this folder yet."""
    from agent_friday.paths import friday_home
    home = Path(os.path.abspath(str(home or friday_home())))
    if not home.is_dir():
        return None
    jr = _read_journal(home)
    if current(home) == latest() and not jr:
        return None
    if current(home) > latest():
        return {"ok": False, "safe_mode": True,
                "why": "this data folder was used by a newer Friday (schema %d; this Friday knows %d). "
                       "Nothing was changed; update Friday." % (current(home), latest())}
    with _Lock(home.parent / (home.name + LOCK_SUFFIX)) as lk:
        if lk.denied:
            return {"ok": False, "safe_mode": True,
                    "why": "the migration lock beside the data folder could not be opened (%s); "
                           "nothing was changed" % lk.denied}
        if not lk.held:
            return {"ok": False, "stop": True, "why": "a migration is running in another Friday process"}
        from agent_friday.services import friday_bundle
        with friday_bundle._RestoreLock(home) as rl:
            if not rl.held and not rl.denied:
                return {"ok": False, "stop": True, "why": "a restore is running on this data folder"}
            return _run_locked(home)


def _keep_safe_mode() -> None:
    """Safe mode from now until the owner leaves it with a press on the PC."""
    try:
        from agent_friday.services import safe_mode
        safe_mode.set_next_start(True, reason=MARKER_REASON)
    except Exception:  # noqa: BLE001 - this start is safe mode regardless (FRIDAY_SAFE_START)
        pass


def _fail(home: Path, jr: dict, number: int, name: str, err: str) -> dict:
    """Put the files back; journaled first so a stop here is finished next start."""
    jr.update(state="restoring", failed=number, error=err, step="restoring")
    _journal(home, jr)
    try:
        if jr.get("backup"):
            _put_back(home, jr["touches"], jr["copied"], Path(jr["backup"]))
        else:
            _put_back(home, jr["touches"], [], Path(home.parent / "nothing"))
    except Exception as e:  # noqa: BLE001
        jr.update(state="restore_failed", step="restore_failed", restore_error=type(e).__name__)
        _journal(home, jr)
        return {"ok": False, "stop": True,
                "why": "migration %d failed and its files could not be put back (%s). Friday is not "
                       "starting; the saved copy is %s" % (number, type(e).__name__, jr.get("backup"))}
    jr.update(state="failed", step="failed")
    _journal(home, jr)
    _receipt(home, {"event": "migration_failed", "from": jr["from"], "to": jr["to"], "failed": number,
                    "name": name, "error": err,
                    "restored_from": Path(jr["backup"]).name if jr.get("backup") else None})
    try:
        _journal_path(home).unlink()     # the next start tries again from the restored folder
    except FileNotFoundError:
        pass
    _keep_safe_mode()
    return {"ok": False, "safe_mode": True, "failed": number,
            "why": "migration %d (%s) failed (%s); the files it touches were put back, and Friday stays "
                   "in safe mode until you leave it on the Doctor page" % (number, name, err)}


def _run_locked(home: Path) -> dict:
    have, want = current(home), latest()
    jr = _read_journal(home)
    if jr and jr.get("state") == "restore_failed":
        return {"ok": False, "stop": True,
                "why": "an earlier migration could not put its files back; the saved copy is %s"
                       % jr.get("backup")}
    if jr and jr.get("state") == "restoring":
        return _fail(home, jr, int(jr.get("failed") or 0), "an earlier step", str(jr.get("error")))
    if have >= want and jr:
        try:
            _journal_path(home).unlink()
        except FileNotFoundError:
            pass
        return {"ok": True, "from": have, "to": have, "steps": [], "note": "already at the latest schema"}
    pending = [m for m in sorted(MIGRATIONS, key=lambda m: m.number) if m.number > have]
    touches = sorted({t for m in pending for t in m.touches})
    resumed = False
    if jr and jr.get("state") == "running":
        shape_ok = isinstance(jr.get("copied"), list) and isinstance(jr.get("touches"), list)
        backup_ok = shape_ok and (not jr["copied"] or Path(jr.get("backup") or "").is_dir())
        print_ok = shape_ok and jr.get("fingerprint") == _fingerprint(home, jr["touches"])
        if jr.get("to") != want or not backup_ok or not print_ok:
            _keep_safe_mode()
            return {"ok": False, "safe_mode": True,
                    "why": "a migration was cut off, and its saved copy is missing or its files changed "
                           "since; nothing was changed. The journal beside the data folder says what ran."}
        touches, resumed = list(jr["touches"]), True
    else:
        bad = _links_in(home, touches)
        if bad:
            _keep_safe_mode()
            return {"ok": False, "safe_mode": True,
                    "why": "a file a migration would change is a link or a junction (%s); nothing was "
                           "changed" % ", ".join(bad)}
        backup = home.parent / ("%s%s%d-%s-%s" % (home.name, BACKUP_INFIX, want, time.strftime("%Y%m%d-%H%M%S"),
                                                  uuid.uuid4().hex[:6]))
        copied = _copy_aside(home, touches, backup)                          # step 2
        jr = {"v": 1, "state": "running", "from": have, "to": want,
              "backup": str(backup) if copied else None, "touches": touches, "copied": copied,
              "step": "backed_up", "fingerprint": _fingerprint(home, touches)}
        _journal(home, jr)
        _prune_backups(home)
    checks = {}
    for m in pending:
        if m.number <= current(home):
            continue                         # done before the stop: the schema says so
        try:
            m.apply(home)                                                      # step 3
            ok = bool(m.check(home))                                           # step 4
            err = None if ok else "its check failed"
        except Exception as e:  # noqa: BLE001 - any failure puts the files back
            ok, err = False, type(e).__name__
        checks[m.number] = ok
        if not ok:
            return _fail(home, jr, m.number, m.name, err)                      # step 6
        sch = _read_schema(home)
        sch["schema"] = m.number
        _write_json(home / SCHEMA_FILE, sch)
        jr.update(step="done-%d" % m.number, fingerprint=_fingerprint(home, touches))
        _journal(home, jr)
    rec = {"event": "migrated", "from": jr["from"], "to": want,
           "steps": sorted(m.number for m in MIGRATIONS if jr["from"] < m.number <= want),
           "resumed": resumed,
           "checks": {str(k): v for k, v in checks.items()},
           "backup": Path(jr["backup"]).name if jr.get("backup") else None,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _receipt(home, rec)                                                       # step 5
    try:
        _journal_path(home).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, **rec}

"""Versioned migrations of Friday's data folder (north star §28.8-1: versioned,
idempotent, backed up first, resumable, logged, integrity-checked; §37.3-11: a
migration receipt).

The data folder's schema version lives in ``<home>/schema.json``
({"schema": N, "stores": {...}}): N is the number of the last migration
applied (0 when the file is absent). Each Migration in MIGRATIONS has a
number, the files it may change (relative paths; folders end in "/"), an
``apply(home)`` that is safe to run again, and a ``check(home)`` that says
whether its result is sound.

``run_pending()`` runs, at start, before Friday uses its data folder:

1. under a lock beside the data folder (never while a restore runs);
2. the files the pending migrations may change are copied beside the folder
   (``<folder>.pre-migration-<to>-<stamp>``) before the first step;
3. each step is journaled (``<folder>.migration-journal.json``): a start that
   finds a journal resumes at the step it reached, which is safe because every
   step can run again;
4. after each step its check runs, then schema.json moves to its number;
5. one receipt (kind "migration": from, to, steps, the backup, the checks)
   goes into the action ledger, and a copy into ``<home>/migrations/``;
6. a step that fails, or whose check fails, puts the copied files back,
   leaves schema.json where it was, and asks for safe mode at this start:
   Friday never runs half-migrated. The journal records the failure.

A newer data folder (schema above what this Friday knows) is never touched and
starts in safe mode: an older Friday must not write a newer folder's stores.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

SCHEMA_FILE = "schema.json"
JOURNAL_SUFFIX = ".migration-journal.json"
LOCK_SUFFIX = ".migration.lock"


@dataclass(frozen=True)
class Migration:
    number: int
    name: str
    touches: tuple
    apply: Callable[[Path], None]
    check: Callable[[Path], bool]
    describe: str = ""
    keep: tuple = field(default_factory=tuple)


class MigrationError(RuntimeError):
    """Said plainly to the owner."""


# ── the migrations ──────────────────────────────────────────────────────────

def _m1_apply(home: Path) -> None:
    """Record each store's version from the store inventory in schema.json
    (the starting point every later migration reads)."""
    from agent_friday.services import store_inventory
    inv = store_inventory.public(store_inventory.inventory(home))
    cur = _read_schema(home)
    cur["stores"] = {r["store"]: r["version"] for r in inv["stores"] if r.get("durable", True)}
    cur["inventory_version"] = inv.get("inventory_version")
    _write_json(home / SCHEMA_FILE, cur)


def _m1_check(home: Path) -> bool:
    d = _read_schema(home)
    return isinstance(d.get("stores"), dict)


MIGRATIONS: list = [
    Migration(1, "record store versions", (SCHEMA_FILE,), _m1_apply, _m1_check,
              "the version of every store, recorded in schema.json"),
]


def latest() -> int:
    return max((m.number for m in MIGRATIONS), default=0)


# ── small helpers ───────────────────────────────────────────────────────────

def _write_json(p: Path, data) -> None:
    tmp = p.with_name(p.name + ".partial-" + uuid.uuid4().hex[:6])
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _read_schema(home: Path) -> dict:
    try:
        d = json.loads((home / SCHEMA_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def current(home: Path) -> int:
    v = _read_schema(home).get("schema", 0)
    return v if isinstance(v, int) and v >= 0 else 0


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


def _copy_aside(home: Path, touches: list, dest: Path) -> list:
    """Copy each touched file or folder that exists into `dest`; returns the
    relative paths copied (a path absent now is recorded as absent)."""
    dest.mkdir(parents=True)
    copied = []
    for rel in touches:
        src = home / rel.rstrip("/")
        if not src.exists() or src.is_symlink():
            continue
        tgt = dest / rel.rstrip("/")
        tgt.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, tgt, symlinks=True)
        else:
            shutil.copy2(src, tgt)
        copied.append(rel)
    return copied


def _put_back(home: Path, touches: list, copied: list, dest: Path) -> None:
    """Restore every touched path to what was copied (absent ones removed)."""
    for rel in touches:
        cur = home / rel.rstrip("/")
        if cur.is_dir() and not cur.is_symlink():
            shutil.rmtree(cur)
        elif cur.exists() or cur.is_symlink():
            cur.unlink()
        if rel in copied:
            src = dest / rel.rstrip("/")
            cur.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, cur, symlinks=True)
            else:
                shutil.copy2(src, cur)


def _receipt(home: Path, rec: dict) -> None:
    d = home / "migrations"
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / ("receipt-%s-to-%s.json" % (rec["from"], rec["to"])), rec)
    try:
        from agent_friday.services import action_ledger
        action_ledger.try_append("migration", rec)
    except Exception:  # noqa: BLE001 - the copy in the folder stays
        pass


def _safe_mode_next() -> None:
    try:
        from agent_friday.services import safe_mode
        safe_mode.set_next_start(True)
    except Exception:  # noqa: BLE001
        pass


class _Lock:
    def __init__(self, home: Path):
        self.path = home.parent / (home.name + LOCK_SUFFIX)
        self.fh = None
        self.held = False

    def __enter__(self):
        try:
            self.fh = open(self.path, "a+b")
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
    do. A result with "safe_mode": true means this start must be safe mode."""
    from agent_friday.paths import friday_home
    home = Path(os.path.abspath(str(home or friday_home())))
    if not home.is_dir():
        return None                          # a first start: the folder is made at the current schema
    have, want = current(home), latest()
    jr = _read_journal(home)
    if have > want:
        _safe_mode_next()
        return {"ok": False, "safe_mode": True,
                "why": "this data folder was used by a newer Friday (schema %d; this Friday knows %d). "
                       "Nothing was changed; update Friday." % (have, want)}
    if have == want and not (jr and jr.get("state") == "running"):
        return None
    with _Lock(home) as lk:
        if not lk.held:
            return {"ok": False, "safe_mode": True, "why": "a migration is running in another Friday process"}
        return _run_locked(home, have, want, _read_journal(home))


def _run_locked(home: Path, have: int, want: int, jr: Optional[dict]) -> dict:
    pending = [m for m in sorted(MIGRATIONS, key=lambda m: m.number) if m.number > have]
    if jr and jr.get("state") == "running" and not (jr.get("to") == want and Path(jr.get("backup", "")).is_dir()):
        # a migration was cut off and its copy of the files is gone (or it
        # aimed elsewhere): copying the folder now would save a half-migrated
        # state as "before". Nothing is touched; safe mode says why.
        _safe_mode_next()
        return {"ok": False, "safe_mode": True,
                "why": "a migration was cut off and its saved copy is missing; nothing was changed. "
                       "The journal beside the data folder says what ran."}
    if jr and jr.get("state") == "running":
        backup, copied, done = Path(jr["backup"]), list(jr.get("copied") or []), list(jr.get("done") or [])
        resumed = True
    else:
        touches = sorted({t for m in pending for t in m.touches})
        backup = home.parent / ("%s.pre-migration-%d-%s-%s" % (home.name, want, time.strftime("%Y%m%d-%H%M%S"),
                                                               uuid.uuid4().hex[:6]))
        copied = _copy_aside(home, touches, backup)                          # step 2
        jr = {"v": 1, "state": "running", "from": have, "to": want, "backup": str(backup),
              "touches": touches, "copied": copied, "done": [], "step": "backed_up"}
        _journal(home, jr)
        done, resumed = [], False
    touches = list(jr.get("touches") or sorted({t for m in pending for t in m.touches}))
    checks = {}
    for m in pending:
        if m.number in done:
            continue
        try:
            m.apply(home)                                                      # step 3
            ok = bool(m.check(home))                                           # step 4
        except Exception as e:  # noqa: BLE001 - any failure puts the files back
            ok, err = False, type(e).__name__
        else:
            err = None if ok else "its check failed"
        checks[m.number] = ok
        if not ok:
            _put_back(home, touches, copied, backup)                           # step 6
            jr.update(state="failed", failed=m.number, error=err, step="failed")
            _journal(home, jr)
            _safe_mode_next()
            _receipt(home, {"event": "migration_failed", "from": have, "to": want, "failed": m.number,
                            "name": m.name, "error": err, "restored_from": backup.name})
            return {"ok": False, "safe_mode": True, "failed": m.number,
                    "why": "migration %d (%s) failed (%s); the files it touches were put back and Friday "
                           "starts in safe mode" % (m.number, m.name, err)}
        sch = _read_schema(home)
        sch["schema"] = m.number
        _write_json(home / SCHEMA_FILE, sch)
        done.append(m.number)
        jr.update(done=done, step="done-%d" % m.number)
        _journal(home, jr)
    rec = {"event": "migrated", "from": have, "to": want, "steps": done, "resumed": resumed,
           "checks": {str(k): v for k, v in checks.items()}, "backup": backup.name,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _receipt(home, rec)                                                       # step 5
    try:
        _journal_path(home).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, **rec}

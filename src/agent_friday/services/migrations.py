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
2. the whole data folder is copied beside it
   (``<folder>.pre-migration-<to>-<stamp>``) before the first step (§37.3-2):
   every store and every name the store list does not know, leaving out what
   store_inventory declares not durable (downloads such as models and
   runtimes, caches, logs, this run's temporary files) and anything that is a
   link, a junction or another reparse point (a cloud placeholder file is
   one): those are never followed, and the receipt counts them. The copy is
   checked against the free disk first: too little room, or a copy that fails
   part way (its partial folder is deleted, and any partial folder a stop left
   is deleted at the next run), and nothing is changed, the start is safe
   mode and the owner is told how much room the copy needs. The copy is made
   readable by this account only (0700; on Windows an owner-only access list
   its files inherit), since it holds Friday's keys. The newest two finished
   copies are kept, never the one this run made;
3. each step is journaled (``<folder>.migration-journal.json``) with a
   fingerprint of the touched files; a start that finds a journal resumes at
   the step the schema reached, and only if the touched files are as that
   step left them;
4. after each step its check runs, then schema.json moves to its number;
5. a receipt file goes into ``<home>/migrations/`` at once, and into the
   action ledger after the start (flush_receipts), so the ledger and its key
   are not touched before Friday is up. Before an upgrade's receipt goes in,
   the history it carries forward is checked (§37.3-8, history_check): the
   action ledger in full and the provenance ledger's hash chain. The counts go
   into the receipt, and a problem becomes an item on Home saying where to
   look; nothing is changed or repaired;
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
import sys
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
KEEP_BACKUPS = 2
#: Room the full copy must leave free on the disk beyond its own size.
FREE_MARGIN_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class Migration:
    number: int
    name: str
    touches: tuple
    apply: Callable[[Path], None]
    check: Callable[[Path], bool]
    describe: str = ""
    rebuilds: tuple = ()          # derived indexes made from what it changes (services/derived_rebuild)


# ── the migrations ──────────────────────────────────────────────────────────

def _m1_apply(home: Path) -> None:
    """Record each store's version in schema.json (the starting point every
    later migration reads). Bookkeeping only: a store inventory that cannot be
    read is recorded as such, never a failure."""
    cur = _read_schema(home)
    try:
        from agent_friday.services import store_inventory
        inv = store_inventory.public(store_inventory.inventory(home))
        cur["stores"] = {r["store"]: r["version"] for r in inv["stores"]
                         if r.get("durable", True) and r["store"] != "data_schema"}   # not itself
        cur["inventory_version"] = inv.get("inventory_version")
    except Exception as e:  # noqa: BLE001
        cur["stores"] = {}
        cur["stores_unreadable"] = type(e).__name__
        _unresolved(home, "inventory_unreadable", "store_inventory",
                    "The upgrade could not read the list of Friday's stores (%s), so their versions were "
                    "not recorded." % type(e).__name__,
                    "Run the checks on this page; if the store list shows an error, make a Friday Bundle "
                    "before the next update.")
    _write_json(home / SCHEMA_FILE, cur)


#: Items a later migration that runs to the end settles.
SETTLED_BY_A_RUN = ("migration_failed", "migration_no_room", "migration_copy_failed")


def _settle_failed(home: Path) -> None:
    """A migration that failed before and has now run is sorted, unless Friday
    is still held in safe mode for it: then the item stays until the owner's
    press leaves safe mode (routes/doctor.py), so Home keeps saying why."""
    try:
        from agent_friday.services import safe_mode, upgrade_review
        if safe_mode.marker_reason() == safe_mode.MIGRATION_FAILED:
            return
        for it in upgrade_review.listing(home):
            if it["code"] in SETTLED_BY_A_RUN:
                upgrade_review.resolve(it["id"], home)
    except Exception:  # noqa: BLE001
        pass


def _unresolved(home: Path, code: str, store: str, said: str, todo: str) -> None:
    """Something the owner should look at (services/upgrade_review). Never
    stops a migration."""
    try:
        from agent_friday.services import upgrade_review
        upgrade_review.add(code, store, said, todo, source="upgrade", home=home)
    except Exception:  # noqa: BLE001
        pass


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


def _not_durable() -> dict:
    """Top-level names in the data folder that store_inventory declares not
    durable (downloads, caches, logs, this run's state) -> their store name.
    An unreadable store list leaves nothing out: copying too much is safe."""
    try:
        from agent_friday.services import store_inventory
        return {st.location.rstrip("/"): st.name for st in store_inventory.STORES
                if not st.durable and "/" not in st.location.rstrip("/")}
    except Exception:  # noqa: BLE001
        return {}


def _full_set(home: Path) -> tuple:
    """(relative paths to copy, store names left out, links left out): the
    whole data folder at its top level, folders ending in "/"."""
    skip = _not_durable()
    copy, left, links = [], [], 0
    for p in sorted(home.iterdir(), key=lambda q: q.name):
        if p.name in skip:
            left.append(skip[p.name])
        elif _is_link(p):
            links += 1
        else:
            copy.append(p.name + "/" if p.is_dir() else p.name)
    return copy, sorted(left), links


def _size(home: Path, rels: list) -> int:
    """Bytes in these paths, never through a link."""
    total = 0
    stack = [home / r.rstrip("/") for r in rels]
    while stack:
        p = stack.pop()
        if _is_link(p):
            continue
        try:
            if p.is_dir():
                stack.extend(p.iterdir())
            else:
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _skip_links(folder, names) -> list:
    """For shutil.copytree: never copy through a link or a junction."""
    out = [n for n in names if _is_link(Path(folder) / n)]
    _SKIPPED[0] += len(out)
    return out


_SKIPPED = [0]


def _gb(n: int) -> str:
    if n < 1e6:
        return "less than 1 MB"
    return "%.1f GB" % (n / 1e9) if n >= 1e8 else "%d MB" % round(n / 1e6)


PARTIAL = ".partial"


def _rmtree(p: Path) -> None:
    """Delete a folder, read-only files included (Windows refuses those)."""
    def again(fn, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            fn(path)
        except OSError as e:
            print("  Migrations: could not delete %s (%s)" % (path, type(e).__name__))
    if sys.version_info >= (3, 12):
        shutil.rmtree(p, onexc=again)
    else:
        shutil.rmtree(p, onerror=again)


def _owner_only_dir(dest: Path) -> None:
    """Make `dest` readable by this account only, before anything is copied
    into it (its files take its access list). Raises when that fails."""
    dest.mkdir(mode=0o700)
    if os.name == "nt":
        import subprocess
        user = os.environ.get("USERNAME") or ""
        r = subprocess.run(["icacls", str(dest), "/inheritance:r", "/grant:r", "%s:(OI)(CI)F" % user],
                           capture_output=True, timeout=30, check=False) if user else None
        if r is None or r.returncode != 0:
            raise PermissionError("the copy's folder could not be made private")
    else:
        os.chmod(dest, 0o700)


def _drop_partials(home: Path) -> None:
    for p in home.parent.iterdir():
        if p.name.startswith(home.name + BACKUP_INFIX) and p.name.endswith(PARTIAL) and not _is_link(p):
            _rmtree(p)


def _copy_aside(home: Path, touches: list, dest: Path) -> list:
    """Copy each listed file or folder that exists into `dest`, never through
    a link; returns the relative paths copied (absent ones are recorded as
    absent)."""
    copied = []
    for rel in touches:
        src = home / rel.rstrip("/")
        if not src.exists():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        tgt = dest / rel.rstrip("/")
        tgt.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, tgt, ignore=_skip_links)
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


def _prune_backups(home: Path, keep: Optional[Path] = None) -> None:
    def made(p: Path) -> tuple:                 # <to>-<YYYYmmdd>-<HHMMSS>-<rand>: by when, not by name
        parts = p.name[len(home.name + BACKUP_INFIX):].split("-")
        return (parts[1:3], p.stat().st_mtime) if len(parts) >= 4 else ([], 0.0)
    olds = sorted((p for p in home.parent.iterdir()
                   if p.is_dir() and p.name.startswith(home.name + BACKUP_INFIX)
                   and not p.name.endswith(PARTIAL) and not _is_link(p) and p != keep),
                  key=made)
    for p in olds[:max(0, len(olds) - (KEEP_BACKUPS - (1 if keep else 0)))]:
        _rmtree(p)


def _receipt(home: Path, rec: dict) -> None:
    """The receipt into the folder now; into the ledger after the start."""
    d = home / "migrations"
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / ("receipt-%s-to-%s-%s.json" % (rec["from"], rec["to"], time.strftime("%Y%m%d-%H%M%S"))),
                dict(rec, ledgered=False))


def _provenance_chain(path: Path) -> dict:
    """Walk services/provenance's ledger: each line's "prev" must be the
    sha256 of the line before it (the first: 64 zeros). Counts only."""
    out = {"entries": 0, "broken_links": 0, "unreadable": 0}
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return out
    except OSError as e:
        return {"unchecked": type(e).__name__}
    prev = "0" * 64
    try:
        with fh:
            text = fh.read()
    except OSError as e:
        return {"unchecked": type(e).__name__}
    lines = text.split("\n")
    if lines and not text.endswith("\n"):
        lines = lines[:-1]                       # a line still being written is not judged
    for line in lines:
        line = line.strip()
        if not line:
            continue
        out["entries"] += 1
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError
        except ValueError:
            out["unreadable"] += 1
        else:
            if rec.get("prev") != prev:
                out["broken_links"] += 1
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return out


def history_check(home: Path) -> dict:
    """The receipt and provenance history an upgrade carries forward
    (§37.3-8): {"receipts": {records, open_problems} | {unchecked},
    "provenance": {entries, broken_links, unreadable} | {unchecked}}. Read
    only. Called once Friday is up, since the action ledger needs its key."""
    out: dict = {}
    try:
        from agent_friday.services import action_ledger
        r = action_ledger.verify(force=True, migrate=False)
        if r.get("key_available") is False:
            out["receipts"] = {"unchecked": "no_key"}      # its problems cannot be read or acknowledged yet
        else:
            n = r.get("open_problem_count")
            if not isinstance(n, int):
                n = sum(1 for p in (r.get("problems") or []) if not p.get("acknowledged"))
            out["receipts"] = {"records": int(r.get("records") or 0), "open_problems": n}
    except Exception as e:  # noqa: BLE001 - a history that cannot be checked is said, not fatal
        out["receipts"] = {"unchecked": type(e).__name__}
    out["provenance"] = _provenance_chain(home / "provenance" / "ledger.jsonl")
    return out


def _history_items(home: Path, to, hist: dict) -> None:
    rc, pv = hist.get("receipts") or {}, hist.get("provenance") or {}
    store = "migration-%s" % to
    if rc.get("unchecked"):
        _unresolved(home, "history_receipts", store,
                    "After the upgrade, Friday's record of what it did (the action ledger) could not be "
                    "checked. Outward actions wait while it cannot be checked.",
                    "Run friday diagnose; the Doctor page's receipt check says what is missing.")
    elif rc.get("open_problems"):
        _unresolved(home, "history_receipts", store,
                    "After the upgrade, Friday's record of what it did (the action ledger) has %d problem(s) "
                    "nobody has looked at yet (counted when Friday started)." % rc["open_problems"],
                    "Open Activity and review the ledger's breaks; outward actions wait until you do.")
    if pv.get("unchecked") or pv.get("broken_links") or pv.get("unreadable"):
        _unresolved(home, "history_provenance", store,
                    "After the upgrade, the record of where Friday's creations came from (the provenance "
                    "ledger) %s." % ("could not be read" if pv.get("unchecked") else
                                     "has %d broken link(s) and %d unreadable line(s)"
                                     % (pv.get("broken_links", 0), pv.get("unreadable", 0))),
                    "Make a Friday Bundle so the record as it is now is kept, then run friday diagnose.")


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
        if rec.get("event") == "migrated" and "history" not in rec:
            try:
                rec["history"] = history_check(home)
                _history_items(home, rec.get("to"), rec["history"])
                _write_json(p, rec)                      # checked once, even if the ledger is not ready
            except Exception as e:  # noqa: BLE001 - never stops a receipt going in
                rec.setdefault("history", {"unchecked": type(e).__name__})
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


def _no_copy(home: Path, want: int, code: str, said: str, todo: str, why: str) -> dict:
    """The folder could not be copied first: nothing changed, safe mode until
    the owner leaves it, and the owner told what to do."""
    _unresolved(home, code, "migration-%d" % want, said + " Friday stays in safe mode until you leave it "
                "on the Doctor page.", todo)
    _keep_safe_mode()
    return {"ok": False, "safe_mode": True, "why": why + "; nothing was changed"}


def _keep_safe_mode() -> None:
    """Safe mode from now until the owner leaves it with a press on the PC.
    The marker is written under friday_home(), the folder run_pending works on
    at start."""
    try:
        from agent_friday.services import safe_mode
        safe_mode.set_next_start(True, reason=MARKER_REASON)
    except Exception as e:  # noqa: BLE001 - this start is safe mode regardless (FRIDAY_SAFE_START)
        print("  Migrations: could not keep safe mode for later starts (%s)" % type(e).__name__)


def _fail(home: Path, jr: dict, number: int, name: str, err: str) -> dict:
    """Put the files back; journaled first so a stop here is finished next start."""
    _keep_safe_mode()                    # first: a stop below still leaves Friday in safe mode
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
    _unresolved(home, "migration_failed", "migration-%d" % number,
                "Friday's upgrade step %d (%s) could not finish (%s). Its files were put back, and Friday "
                "stays in safe mode until you leave it on the Doctor page; the next start tries the step "
                "again." % (number, name, err),
                "Restart Friday to try again. If it keeps failing, make a Friday Bundle and run friday "
                "diagnose. Leave safe mode on the Doctor page when it has run.")
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
            _unresolved(home, "migration_cut_off", "migration-%s" % jr.get("to"),
                        "An upgrade was cut off part way, and its saved copy is missing or its files "
                        "changed since, so Friday left them as they are and stays in safe mode until you "
                        "leave it on the Doctor page.",
                        "Make a Friday Bundle, then run friday diagnose; the journal beside the data "
                        "folder says what ran.")
            _keep_safe_mode()
            return {"ok": False, "safe_mode": True,
                    "why": "a migration was cut off, and its saved copy is missing or its files changed "
                           "since; nothing was changed. The journal beside the data folder says what ran."}
        touches, resumed = list(jr["touches"]), True
    else:
        bad = _links_in(home, touches)
        if bad:
            _unresolved(home, "migration_link", "migration-%d" % want,
                        "A file an upgrade would change is a link or a junction, so the upgrade did not "
                        "run, and Friday stays in safe mode until you leave it on the Doctor page.",
                        "Replace the link in Friday's data folder with the real file or folder, then "
                        "restart Friday.")
            _keep_safe_mode()
            return {"ok": False, "safe_mode": True,
                    "why": "a file a migration would change is a link or a junction (%s); nothing was "
                           "changed" % ", ".join(bad)}
        backup = home.parent / ("%s%s%d-%s-%s" % (home.name, BACKUP_INFIX, want, time.strftime("%Y%m%d-%H%M%S"),
                                                  uuid.uuid4().hex[:6]))
        whole, left_out, links = _full_set(home)                             # step 2: the whole folder
        need = _size(home, whole)
        try:
            free = shutil.disk_usage(str(home.parent)).free
        except OSError:
            free = None
        if free is not None and free < need + FREE_MARGIN_BYTES:
            return _no_copy(home, want, "migration_no_room",
                            "There is not enough free disk beside Friday's data folder to copy it before "
                            "the upgrade (the copy needs about %s, %s is free), so the upgrade did not "
                            "run and nothing was changed." % (_gb(need + FREE_MARGIN_BYTES), _gb(free)),
                            "Free about %s on that disk, then restart Friday; the upgrade runs then."
                            % _gb(need + FREE_MARGIN_BYTES - free),
                            "not enough free disk to copy the data folder first (needs about %s)"
                            % _gb(need + FREE_MARGIN_BYTES))
        _SKIPPED[0] = 0
        partial = backup.with_name(backup.name + PARTIAL)
        try:
            _drop_partials(home)                     # what a stop during an earlier copy left
            _owner_only_dir(partial)
            copied = _copy_aside(home, sorted(set(touches) | set(whole)), partial)
            if copied:
                _replace(partial, backup)            # whole, or not at all
            else:
                _rmtree(partial)
        except Exception as e:  # noqa: BLE001 - a copy that fails part way is no copy
            if partial.exists():
                _rmtree(partial)
            return _no_copy(home, want, "migration_copy_failed",
                            "Friday could not copy its data folder before the upgrade (%s), so the "
                            "upgrade did not run and nothing was changed." % type(e).__name__,
                            "Check the disk has room and Friday's folder can be read, then restart "
                            "Friday; the upgrade runs then.",
                            "the data folder could not be copied first (%s)" % type(e).__name__)
        jr = {"v": 1, "state": "running", "from": have, "to": want,
              "backup": str(backup) if copied else None, "touches": touches, "copied": copied,
              "scope": "full", "left_out": left_out, "links_left_out": links + _SKIPPED[0],
              "step": "backed_up", "fingerprint": _fingerprint(home, touches)}
        _journal(home, jr)
        _prune_backups(home, keep=backup if copied else None)
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
           "backup_scope": jr.get("scope", "touched"), "left_out": list(jr.get("left_out") or []),
           "links_left_out": int(jr.get("links_left_out") or 0),
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _receipt(home, rec)                                                       # step 5
    rebuilds = sorted({ix for m in MIGRATIONS if jr["from"] < m.number <= want for ix in m.rebuilds})
    if rebuilds:
        try:
            from agent_friday.services import derived_rebuild
            derived_rebuild.request(rebuilds, "the upgrade to schema %d" % want, home)
        except Exception:  # noqa: BLE001 - the rebuild is asked again by hand from the Doctor page
            pass
    _settle_failed(home)
    try:
        _journal_path(home).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, **rec}

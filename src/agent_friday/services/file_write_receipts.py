"""Backups, receipts and undo for Friday's write_file tool.

Before write_file changes anything, this module:

  * for an overwrite, copies the existing file to a backup under Friday's own
    folder (``actions/file_backups/<receipt id>/``), checked by size and
    SHA-256; for an append, records the file's size and SHA-256 (undo cuts the
    file back to that size, so a growing log is never copied whole);
  * writes the receipt (services/action_journal, domain ``file_write``) that
    names the path, what was there and what the write is about to do. The
    receipt exists before the first byte is written, so a write that fails half
    way still has its record and its way back.

After the write, the receipt is updated with the result and the new hash.

``undo(receipt)`` puts the file back as it was: the backup is copied over it,
an append is cut back, or a file the write created moves to Friday's trash
(nothing is deleted). Work done since is never lost: a version newer than
Friday's write is kept beside the backup first, and a created file that has
changed since is left where it is. Every refusal says why. The undo is itself
a receipt.

Limits, each said when it applies: a backup is kept for a file up to
MAX_BACKUP_FILE_BYTES; the store holds MAX_BACKUP_BYTES, oldest removed first
(never the newest), and a receipt whose backup was removed says so. Off the
record, the copy is kept in this process's memory only (up to
MAX_MEMORY_BACKUP_BYTES), so nothing about the file reaches Friday's folder
and undo works until Friday restarts.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from agent_friday.services import action_journal as journal

DOMAIN = "file_write"
#: The largest file write_file keeps a backup of before replacing it.
MAX_BACKUP_FILE_BYTES = 200 * 1024 ** 2
#: The most the backup store may hold before the oldest backups are removed.
MAX_BACKUP_BYTES = 2 * 1024 ** 3
#: Free space a backup must leave on the drive that holds Friday's folder.
FREE_HEADROOM_BYTES = 512 * 1024 ** 2
#: Off the record, the largest file whose copy is held in memory, and the most held at once.
MAX_MEMORY_BACKUP_BYTES = 20 * 1024 ** 2
MAX_MEMORY_BACKUPS_TOTAL = 200 * 1024 ** 2

_LOCK = threading.Lock()
_MEM_BACKUPS: dict[str, bytes] = {}
_MEM_PREFIX = "memory:"


class BackupFailed(OSError):
    """The existing file could not be backed up, so nothing was written."""


def backups_root() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "actions" / "file_backups"


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.skip("action_receipts"))
    except Exception:  # noqa: BLE001 - unknown privacy state: keep nothing on disk
        return True


def _sha256(path: Path, limit: int | None = None) -> str:
    h = hashlib.sha256()
    left = limit
    with open(path, "rb") as fh:
        while left is None or left > 0:
            chunk = fh.read(1024 * 1024 if left is None else min(1024 * 1024, left))
            if not chunk:
                break
            h.update(chunk)
            if left is not None:
                left -= len(chunk)
    return h.hexdigest()


def _human(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%d bytes" % n


def describe_target(raw_path: str) -> dict:
    """What a write to `raw_path` would do, for the card that asks first:
    {"exists", "size", "words"}. `words` is the plain sentence."""
    raw = str(raw_path or "")
    if raw.startswith(("\\\\", "//")):
        # A network or device path: not touched before the owner decides.
        return {"exists": False, "size": 0, "words": "write %s (a network or device path)" % raw}
    try:
        p = Path(os.path.expanduser(raw)).resolve()
    except (OSError, RuntimeError, ValueError):
        return {"exists": False, "size": 0, "words": "create %s" % raw_path}
    if p.is_file():
        size = p.stat().st_size
        return {"exists": True, "size": size,
                "words": "replace the existing file %s (%s); Friday keeps a copy so you can undo it"
                         % (p, _human(size))}
    return {"exists": False, "size": 0, "words": "create the new file %s" % p}


def _copy_checked(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if dst.stat().st_size != src.stat().st_size:
        raise BackupFailed("the backup copy of %s is incomplete" % src.name)
    digest = _sha256(dst)
    if digest != _sha256(src):
        raise BackupFailed("the backup copy of %s does not match it" % src.name)
    return digest


def _keep_copy(path: Path, rid: str, size: int) -> tuple[str, str]:
    """(backup reference, sha256) for an overwrite. Raises BackupFailed with the reason."""
    if size > MAX_BACKUP_FILE_BYTES:
        raise BackupFailed("%s is %s, and Friday keeps copies of files up to %s before replacing them; "
                           "copy it somewhere first, or ask for a new file instead"
                           % (path.name, _human(size), _human(MAX_BACKUP_FILE_BYTES)))
    if _off_record():
        if size > MAX_MEMORY_BACKUP_BYTES:
            raise BackupFailed("off the record Friday keeps the copy in memory only, up to %s, and %s is %s"
                               % (_human(MAX_MEMORY_BACKUP_BYTES), path.name, _human(size)))
        data = path.read_bytes()
        if len(data) != size:
            raise BackupFailed("%s changed while it was being copied" % path.name)
        with _LOCK:
            _MEM_BACKUPS[rid] = data
            # The oldest copies go first once the total passes its cap; their receipts say so.
            while sum(len(v) for v in _MEM_BACKUPS.values()) > MAX_MEMORY_BACKUPS_TOTAL and len(_MEM_BACKUPS) > 1:
                old = min(k for k in _MEM_BACKUPS if k != rid)
                _MEM_BACKUPS.pop(old, None)
                journal.update(old, backup_removed="its in-memory copy was dropped to keep off-record copies "
                                                   "under %s" % _human(MAX_MEMORY_BACKUPS_TOTAL))
        return _MEM_PREFIX + rid, hashlib.sha256(data).hexdigest()
    try:
        free = shutil.disk_usage(str(backups_root().parent if backups_root().parent.exists() else Path.home())).free
    except OSError:
        raise BackupFailed("the free space for a copy of %s could not be checked" % path.name)
    if free < size + FREE_HEADROOM_BYTES:
        raise BackupFailed("there is not enough free space to keep a copy of %s (%s) first"
                           % (path.name, _human(size)))
    backup = backups_root() / rid / path.name
    try:
        return str(backup), _copy_checked(path, backup)
    except BackupFailed:
        shutil.rmtree(backups_root() / rid, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(backups_root() / rid, ignore_errors=True)
        raise BackupFailed("a copy of %s could not be kept (%s)" % (path.name, type(e).__name__))


def begin(path: Path, mode: str, content_len: int, *, conversation_id: str | None = None) -> dict:
    """Keep what is at `path` (if anything) and write the receipt. Call BEFORE
    writing. Returns the receipt. Raises BackupFailed when the earlier version
    cannot be kept; the caller then writes nothing."""
    try:
        return _begin(Path(path), mode, content_len, conversation_id=conversation_id)
    except BackupFailed:
        raise
    except OSError as e:     # a locked or unreadable file: nothing is written, and the answer says so
        raise BackupFailed("%s could not be read to keep its earlier version (%s)" % (Path(path).name, type(e).__name__))


def _begin(path: Path, mode: str, content_len: int, *, conversation_id: str | None = None) -> dict:
    if _MEM_BACKUPS and not _off_record():
        _drop_memory_copies("off the record ended, and its in-memory copies went with it")
    if path.exists() and not path.is_file():
        raise BackupFailed("%s is not an ordinary file" % path.name)
    rid = journal.new_id()
    existed = path.is_file()
    item = {"path": str(path), "name": path.name, "mode": mode, "existed": existed,
            "content_chars": int(content_len)}
    if existed:
        size = path.stat().st_size
        item["before_size"] = size
        if mode == "append":
            item["before_sha256"] = _sha256(path)
        else:
            item["backup"], item["before_sha256"] = _keep_copy(path, rid, size)
    else:
        # Folders the write will create, deepest first, so undo can remove the empty ones in order.
        made, parent = [], path.parent
        while not parent.exists() and parent != parent.parent:
            made.append(str(parent))
            parent = parent.parent
        item["made_dirs"] = made
    action = ("append" if mode == "append" else "overwrite") if existed else "create"
    summary = {"overwrite": "Replaced %s (the earlier version is kept)",
               "append": "Added to %s (undo cuts it back)",
               "create": "Created %s"}[action] % path
    rec = journal.record("write_file", action, DOMAIN, items=[item], summary=summary, status="pending",
                         undo={"file": item}, conversation_id=conversation_id, receipt_id=rid,
                         surface="write_file")
    _prune(keep=rid)
    return rec


def _drop_memory_copies(why: str) -> None:
    with _LOCK:
        gone = list(_MEM_BACKUPS)
        _MEM_BACKUPS.clear()
    for rid in gone:
        journal.update(rid, backup_removed=why)


def finish(receipt_id: str, ok: bool, *, error: str = "") -> dict | None:
    """Record the write's outcome on its receipt."""
    rec = journal.get(receipt_id) or {}
    item = dict(((rec.get("undo") or {}).get("file")) or {})
    fields = {"status": "complete" if ok else "failed", "finished_at": time.time()}
    if ok:
        try:
            item["after_sha256"] = _sha256(Path(item["path"]))
        except (OSError, KeyError):
            item["after_sha256"] = ""
        fields["undo"] = {"file": item}
        fields["items"] = [item]
    else:
        fields["error"] = str(error)[:200]
        # A failed write may still have changed the file: keep the way back to the earlier version.
        fields["status"] = "partial" if item.get("existed") else "failed"
    return journal.update(receipt_id, **fields)


def _backup_path(rid: str, ref: str) -> Path:
    """The backup a receipt names, only if it is this receipt's own backup."""
    from agent_friday.paths import contained
    p = Path(ref)
    own = contained(backups_root() / rid, p.name)
    if os.path.normcase(str(own)) != os.path.normcase(str(p.resolve())):
        raise OSError("its backup is not where Friday keeps backups")
    return own


def _restore_bytes(path: Path, data_from: Path | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".friday-undo-", dir=str(path.parent))
    os.close(fd)
    try:
        if isinstance(data_from, bytes):
            # The owner's own file, put back byte for byte where it was.
            import io
            with open(tmp, "wb") as fh:
                shutil.copyfileobj(io.BytesIO(data_from), fh)
        else:
            shutil.copy2(data_from, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def undo(receipt: dict) -> dict:
    """Put the file back as it was before the write. Returns the undo's receipt."""
    rid = receipt["receipt_id"]
    item = (receipt.get("undo") or {}).get("file") or {}
    path = Path(item.get("path") or "")
    kept_newer = ""
    said = ""
    try:
        if not item.get("path"):
            raise OSError("the receipt names no file")
        status = receipt.get("status")
        if status == "pending" and item.get("existed") and item.get("backup"):
            status = "partial"        # stopped mid-write (Friday was closed): its copy is the way back
        if status not in ("complete", "partial"):
            if not item.get("existed"):
                raise OSError("that write did not finish, so Friday did not make %s; if a file is there now it "
                              "is left where it is" % path.name)
            raise OSError("that write did not happen (%s), so there is nothing to put back" % (status or "unknown"))
        now_sha = _sha256(path) if path.is_file() else None
        after = item.get("after_sha256")
        if item.get("existed") and item.get("mode") == "append":
            before_size = int(item.get("before_size") or 0)
            if not path.is_file():
                raise OSError("the file is no longer there")
            if path.stat().st_size < before_size or _sha256(path, before_size) != item.get("before_sha256"):
                raise OSError("the part that was there before Friday added to it has changed since, so it is "
                              "left as it is")
            if now_sha != after:
                newer = journal.home_trash(rid) / ("newer-" + path.name)
                _copy_checked(path, newer)
                kept_newer = str(newer)
            seen_size = path.stat().st_size
            with open(path, "r+b") as fh:
                if os.fstat(fh.fileno()).st_size != seen_size:
                    raise OSError("it is still being added to, so it is left as it is; try again")
                fh.truncate(before_size)
            said = "Cut %s back to how it was before Friday added to it." % path.name
        elif item.get("existed"):
            ref = str(item.get("backup") or "")
            if ref.startswith(_MEM_PREFIX):
                data = _MEM_BACKUPS.get(ref[len(_MEM_PREFIX):])
                if data is None:
                    raise OSError("its copy was kept in memory off the record and Friday has restarted since")
                if hashlib.sha256(data).hexdigest() != item.get("before_sha256"):
                    raise OSError("its copy no longer matches the file as it was")
                source: Path | bytes = data
            else:
                backup = _backup_path(rid, ref) if ref else None
                if backup is None or not backup.is_file():
                    raise OSError("its backup is no longer kept (%s), so it cannot be put back"
                                  % (receipt.get("backup_removed") or "removed"))
                if _sha256(backup) != item.get("before_sha256"):
                    raise OSError("its backup no longer matches the file as it was")
                source = backup
            if now_sha is not None and now_sha != after and now_sha != item.get("before_sha256"):
                # Anything there now that is not exactly what Friday wrote (or what was there
                # before) is someone's newer work: keep it first.
                newer = journal.home_trash(rid) / ("newer-" + path.name)
                _copy_checked(path, newer)
                kept_newer = str(newer)
            _restore_bytes(path, source)
            said = "Put %s back as it was before Friday's write." % path.name
        else:
            if path.exists():
                if not after or now_sha != after:
                    raise OSError("it has changed since Friday created it (or the write did not finish), so it "
                                  "is left where it is")
                journal.safe_move(path, journal.home_trash(rid) / path.name)
            for d in item.get("made_dirs") or []:
                dp = Path(d)
                if dp.is_dir() and not any(dp.iterdir()):
                    dp.rmdir()
            said = "Moved %s, which Friday had created, to Friday's trash." % path.name
        if kept_newer:
            said += " It had changed since Friday wrote it; that newer version is kept at %s." % kept_newer
        status, error = "complete", ""
    except Exception as e:  # noqa: BLE001 - the receipt and the answer say what happened
        status, error = "failed", str(e)[:200] or type(e).__name__
        said = "%s was not put back: %s." % (path.name or "The file", error)
    journal.update(rid, undone=status == "complete", undone_at=time.time(), undo_failed=0 if status == "complete" else 1)
    if status == "complete":
        _MEM_BACKUPS.pop(rid, None)
    return journal.record("undo_action", "undo", DOMAIN, items=[{"receipt_id": rid, "path": str(path),
                                                                  "kept_newer": kept_newer}],
                          summary=said, status=status,
                          conversation_id=(receipt.get("run") or {}).get("conversation_id"),
                          extra={"said": said, **({"error": error} if error else {})})


def _prune(keep: str = "") -> None:
    """Keep the backup store under MAX_BACKUP_BYTES, removing the oldest first, never `keep`."""
    root = backups_root()
    if not root.is_dir():
        return
    with _LOCK:
        dirs = []
        total = 0
        for d in root.iterdir():
            if not d.is_dir():
                continue
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            total += size
            if d.name != keep:
                dirs.append((d.name, d, size))
        dirs.sort()                       # receipt ids sort by time
        while total > MAX_BACKUP_BYTES and dirs:
            name, d, size = dirs.pop(0)
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            journal.update(name, backup_removed="removed to keep backups under %s" % _human(MAX_BACKUP_BYTES))

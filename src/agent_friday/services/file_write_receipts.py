"""Backups, receipts and undo for Friday's write_file tool.

Before write_file changes anything, this module:

  * copies an existing file to a backup under Friday's own folder
    (``actions/file_backups/<receipt id>/``), checked by size and SHA-256;
  * writes the receipt (services/action_journal, domain ``file_write``) that
    names the path, what was there (hash, size, backup) and what the write is
    about to do. The receipt exists before the first byte is written, so a
    write that fails half way still has its record and its way back.

After the write, the receipt is updated with the result and the new hash.

``undo(receipt)`` puts the file back as it was: the backup is copied over it,
or, for a file the write created, the file moves to Friday's trash (nothing is
deleted). If the file changed after Friday's write, that newer version is kept
beside the backup first and the result says where, so an undo never destroys
work. The undo is itself a receipt.

Backups are kept while their receipt is reachable by undo; once the store
passes MAX_BACKUP_BYTES the oldest backups are removed and their receipts say
so, so an undo that can no longer restore says that instead of failing silently.
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
#: The most the backup store may hold before the oldest backups are removed.
MAX_BACKUP_BYTES = 2 * 1024 ** 3
#: Free space a backup must leave on the drive that holds Friday's folder.
FREE_HEADROOM_BYTES = 512 * 1024 ** 2

_LOCK = threading.Lock()


class BackupFailed(OSError):
    """The existing file could not be backed up, so nothing was written."""


def backups_root() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "actions" / "file_backups"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _human(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%d bytes" % n


def describe_target(raw_path: str) -> dict:
    """What a write to `raw_path` would do, for the card that asks first:
    {"exists", "size", "words"}. `words` is the plain sentence."""
    try:
        p = Path(os.path.expanduser(str(raw_path or ""))).resolve()
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


def begin(path: Path, mode: str, content_len: int, *, conversation_id: str | None = None) -> dict:
    """Back up what is at `path` (if anything) and write the receipt. Call
    BEFORE writing. Returns the receipt. Raises BackupFailed when an existing
    file cannot be backed up; the caller then writes nothing."""
    path = Path(path)
    rid = journal.new_id()
    existed = path.is_file()
    item = {"path": str(path), "name": path.name, "mode": mode, "existed": existed,
            "content_chars": int(content_len)}
    if existed:
        size = path.stat().st_size
        try:
            free = shutil.disk_usage(str(backups_root().parent if backups_root().parent.exists()
                                         else Path.home())).free
        except OSError:
            free = 0
        if free < size + FREE_HEADROOM_BYTES:
            raise BackupFailed("there is not enough free space to keep a copy of %s (%s) first"
                               % (path.name, _human(size)))
        backup = backups_root() / rid / path.name
        try:
            item["before_sha256"] = _copy_checked(path, backup)
        except BackupFailed:
            shutil.rmtree(backups_root() / rid, ignore_errors=True)
            raise
        except OSError as e:
            shutil.rmtree(backups_root() / rid, ignore_errors=True)
            raise BackupFailed("a copy of %s could not be kept (%s)" % (path.name, type(e).__name__))
        item["before_size"] = size
        item["backup"] = str(backup)
    else:
        # Folders the write will create, deepest first, so undo can remove the empty ones in order.
        made, parent = [], path.parent
        while not parent.exists() and parent != parent.parent:
            made.append(str(parent))
            parent = parent.parent
        item["made_dirs"] = made
    action = ("append" if mode == "append" else "overwrite") if existed else "create"
    summary = {"overwrite": "Replaced %s (the earlier version is kept)",
               "append": "Added to %s (the earlier version is kept)",
               "create": "Created %s"}[action] % path
    rec = journal.record("write_file", action, DOMAIN, items=[item], summary=summary, status="pending",
                         undo={"file": item}, conversation_id=conversation_id, receipt_id=rid,
                         surface="write_file")
    _prune()
    return rec


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
        # A failed write may still have changed the file: keep the way back.
        fields["status"] = "partial" if Path(item.get("path") or "").exists() and item.get("existed") else "failed"
    return journal.update(receipt_id, **fields)


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
        now_sha = _sha256(path) if path.is_file() else None
        changed_since = (now_sha is not None and item.get("after_sha256")
                         and now_sha != item.get("after_sha256"))
        if changed_since and item.get("existed"):
            newer = journal.home_trash(rid) / ("newer-" + path.name)
            _copy_checked(path, newer)
            kept_newer = str(newer)
        elif changed_since:
            kept_newer = str(journal.home_trash(rid) / path.name)
        if item.get("existed"):
            backup = Path(item.get("backup") or "")
            if not backup.is_file():
                raise OSError("its backup is no longer kept (%s), so it cannot be put back"
                              % (receipt.get("backup_removed") or "removed"))
            if _sha256(backup) != item.get("before_sha256"):
                raise OSError("its backup no longer matches the file as it was")
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".friday-undo-", dir=str(path.parent))
            os.close(fd)
            try:
                shutil.copy2(backup, tmp)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            said = "Put %s back as it was before Friday's write." % path.name
        else:
            if path.exists():
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
    return journal.record("undo_action", "undo", DOMAIN, items=[{"receipt_id": rid, "path": str(path),
                                                                  "kept_newer": kept_newer}],
                          summary=said, status=status,
                          conversation_id=(receipt.get("run") or {}).get("conversation_id"),
                          extra={"said": said, **({"error": error} if error else {})})


def _prune() -> None:
    """Keep the backup store under MAX_BACKUP_BYTES, removing the oldest first."""
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
            dirs.append((d.name, d, size))
            total += size
        dirs.sort()                       # receipt ids sort by time
        while total > MAX_BACKUP_BYTES and len(dirs) > 1:
            name, d, size = dirs.pop(0)
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            journal.update(name, backup_removed="removed to keep backups under %s" % _human(MAX_BACKUP_BYTES))

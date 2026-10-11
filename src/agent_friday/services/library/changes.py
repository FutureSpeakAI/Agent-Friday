"""Finding what changed in a folder since the Library last read it, without reading anything.

The idle sweep calls `detect` for each folder the owner has added. It walks the folder and compares each
file's size and modified time with the row the index holds (a stat, no read). A file that is new, or whose
size or time differ, or that was left half-read, is "changed"; a row whose file is gone is "gone". Nothing
is read here and nothing is queued here: `queue` hands the list to the folder's indexer, which folds a burst
of changes into ONE job per folder, and that job runs on the Local AI queue when the PC is idle (never at
the moment of the change). When the job reads a changed file it hashes it first, and a file whose bytes are
the same as before (touched, copied back, saved without edits) is not read again.

A folder the owner paused or stopped is left alone until they resume it, and a folder that is already
waiting for its turn or being read is not walked again.
"""
from __future__ import annotations

import os
from pathlib import Path

from agent_friday.services.library import index_job, indexer
from agent_friday.services.library.store import INDEX_VERSION, Store


def _key(path) -> str:
    return os.path.normcase(str(path))


def detect(store: Store, scope: str | Path, *, recursive: bool = True, glob: str | None = None,
           kinds: set[str] | None = None, allowed=None) -> dict:
    """{"changed": [paths], "new": [paths], "gone": [paths]} for one folder. Stat only."""
    root = Path(scope)
    try:
        base = str(root.resolve() if root.is_dir() else root.resolve().parent)
    except OSError:
        base = str(root)
    rows = {_key(r["path"]): r for r in store.q(
        "SELECT id, path, size, mtime, state, index_version FROM documents WHERE scope=?", (base,))}
    out = {"changed": [], "new": [], "gone": []}
    seen: set[str] = set()
    for p in indexer.scan_scope(root, recursive=recursive, glob=glob, kinds=kinds):
        if allowed and not allowed(p):
            continue
        try:
            real = p.resolve()
            st = real.stat()
        except OSError:
            continue
        k = _key(real)
        seen.add(k)
        row = rows.get(k)
        if row is None:
            out["new"].append(str(real))
            continue
        state = row["state"] or ""
        if state == "skipped:forgotten":
            continue
        moved = row["size"] != st.st_size or row["mtime"] != st.st_mtime
        if state == "indexed":
            if moved or row["index_version"] != INDEX_VERSION:
                out["changed"].append(str(real))
        elif state == "queued" or moved:                   # half-read, or a failed/skipped file that was edited
            out["changed"].append(str(real))
    for k, row in rows.items():
        if k not in seen and not Path(row["path"]).exists():
            out["gone"].append(row["path"])
    return out


def purge_gone(principal: str, store: Store, scope_event: dict, *, allowed=None) -> int:
    """Remove the documents of files that are gone from disk the way a removal does (graph, drafts, caches).
    A stat and a delete, never a read: it runs whether or not reading is paused, stopped or waiting for idle."""
    from agent_friday.services.library import forget
    scope = scope_event["path"]
    d = detect(store, scope, recursive=bool(scope_event.get("recursive", True)), glob=scope_event.get("glob"),
               kinds=set(scope_event["kinds"]) if scope_event.get("kinds") else None, allowed=allowed)
    n = 0
    for path in d["gone"]:
        row = store.find_document(path)
        if row is not None:
            forget.remove_document(principal, row["id"])
            n += 1
    return n


def queue(principal: str, ix, scope_event: dict, store: Store, *, allowed=None) -> int:
    """Hand one folder's changes to its indexer as a single job. Returns 1 when a job was queued, else 0."""
    scope = scope_event["path"]
    pr = index_job.progress_for(principal)
    rec = pr.data["folders"].get(str(scope)) or {}
    is_dir = Path(scope).is_dir()
    if pr.paused or rec.get("state") in (index_job.RUNNING, index_job.ADMITTING, index_job.QUEUED,
                                         index_job.PAUSED, index_job.STOPPED):
        return 0
    kw = {"recursive": bool(scope_event.get("recursive", True)), "glob": scope_event.get("glob"),
          "kinds": set(scope_event["kinds"]) if scope_event.get("kinds") else None}
    d = detect(store, scope, allowed=allowed, **kw)
    paths = d["changed"] + d["new"] + d["gone"]
    try:
        # Laya's notes are made by a folder's job; a single file has none, so it is never "waiting" for them.
        waiting = is_dir and bool(store.untriaged(index_job._scope_root(str(scope))))
    except Exception:  # noqa: BLE001
        waiting = False
    if not paths and not waiting:
        return 0
    if is_dir:
        index_job.note_queued(principal, str(scope), only=paths)
    ix.enqueue(scope, paths=paths, **kw)
    return 1

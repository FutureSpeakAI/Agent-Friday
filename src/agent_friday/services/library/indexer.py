"""Reading documents into the index, one file at a time, in a limited child.

`index_file` is synchronous and testable on its own. `Indexer` runs a queue of
them on one background thread, pausing while the machine is busy; the callers
that decide *what* may be indexed (the consent record) live in `grants`.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

from agent_friday.services.library import extract as _ext
from agent_friday.services.library import procrun, structure, vectors, withhold
from agent_friday.services.library.store import INDEX_VERSION, Store, store_for, OWNER
from agent_friday.services.library.textclean import safe_title

# Never indexed whatever the scope says: dependency trees, VCS, caches.
SKIP_DIRS = {"node_modules", ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".cache", "$RECYCLE.BIN",
             "System Volume Information"}

# (path, title, sample text) -> "open" | "vault"
Classify = Callable[[Path, str, str], str]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _refused(path: Path) -> str | None:
    """Why this path is never read, or None."""
    try:
        from agent_friday.services import credential_paths
        why = credential_paths.check(path, sniff=False)
        if why:
            return "private"
    except Exception:
        pass
    try:
        from agent_friday.services import studio_files as sf
        real = os.path.normcase(str(path.resolve()))
        for ex in sf._excluded():
            if sf._under(real, ex):
                return "private"
    except Exception:
        pass
    return None


def scan_scope(root: str | Path, *, recursive: bool = True, glob: str | None = None,
               kinds: set[str] | None = None) -> Iterator[Path]:
    """Files under `root` the Library can read, skipping private and noise folders."""
    root = Path(root)
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        if _refused(Path(dirpath)):
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            if glob and not fnmatch.fnmatch(fn.lower(), glob.lower()):
                continue
            k = _ext.kind_for(fn)
            if k is None or (kinds and k not in kinds):
                continue
            yield Path(dirpath) / fn
        if not recursive:
            break


def _failure_text(e: procrun.TaskFailed) -> str:
    return {"timeout": "took too long to read", "memory": "needs more memory than is allowed"}.get(e.kind, e.reason)


def index_file(store: Store, path: str | Path, scope: str | Path, *, classify: Classify | None = None,
               force: bool = False, shelf: str | None = None) -> dict:
    """Index one file. Returns {state, doc_id, detail}; never raises for a
    document's own problems (those are recorded as the document's state)."""
    p = Path(path)
    scope_p = Path(scope)
    try:
        p = p.resolve()
        st = p.stat()
    except OSError:
        return {"state": "failed", "doc_id": None, "detail": "the file is gone"}
    if _refused(p):
        return {"state": "skipped", "doc_id": None, "detail": "private"}
    try:
        rel = p.parent.relative_to(scope_p.resolve() if scope_p.is_dir() else p.parent)
        rel_s = str(rel).replace("\\", "/")
        rel_s = "" if rel_s == "." else rel_s
    except ValueError:
        rel_s = ""
    scope_root = str(scope_p.resolve() if scope_p.is_dir() else p.parent)
    kind = _ext.kind_for(p)
    prior = store.find_document(str(p))
    if (prior and not force and prior["state"] == "indexed" and prior["size"] == st.st_size
            and prior["mtime"] == st.st_mtime and prior["index_version"] == INDEX_VERSION):
        return {"state": "unchanged", "doc_id": prior["id"], "detail": None}   # same size and time: not re-read
    sha = None
    try:
        sha = file_sha256(p) if st.st_size <= _ext.caps.MAX_FILE_BYTES else None
    except OSError:
        return {"state": "failed", "doc_id": None, "detail": "the file can't be opened"}
    if store.tombstoned(sha, str(p)):
        return {"state": "skipped", "doc_id": None, "detail": "forgotten"}
    existing = store.find_document(str(p))
    if (existing and not force and existing["state"] == "indexed" and existing["sha256"] == sha
            and existing["index_version"] == INDEX_VERSION):
        store.x("UPDATE documents SET mtime=?, size=? WHERE id=?", (st.st_mtime, st.st_size, existing["id"]))
        return {"state": "unchanged", "doc_id": existing["id"], "detail": None}
    fid = store.folder_id(scope_root, rel_s)
    doc_id = store.upsert_document(str(p), sha256=sha, size=st.st_size, mtime=st.st_mtime, kind=kind,
                                   ext=p.suffix.lower().lstrip("."), scope=scope_root, folder_id=fid,
                                   title=withhold.title(safe_title(p.stem)), shelf=shelf or "open")
    if kind is None:
        store.set_state(doc_id, "skipped:unsupported", "this kind of file isn't read yet")
        return {"state": "skipped", "doc_id": doc_id, "detail": "unsupported"}
    try:
        res = procrun.run_task("extract", {"path": str(p)}, low_priority=True)
    except procrun.TaskFailed as e:
        res = _ext.media_cache_result(p) if (kind == "media" and e.kind == "unsupported") else None
        if res is None:
            if e.kind == "unsupported":
                store.set_state(doc_id, "skipped:" + e.reason[:120], e.reason)
                return {"state": "skipped", "doc_id": doc_id, "detail": e.reason}
            store.set_state(doc_id, "failed:" + _failure_text(e)[:200])
            return {"state": "failed", "doc_id": doc_id, "detail": _failure_text(e)}
    except Exception as e:  # noqa: BLE001 - recorded as the document's failure
        store.set_state(doc_id, "failed:" + (str(e) or type(e).__name__)[:200])
        return {"state": "failed", "doc_id": doc_id, "detail": str(e)}
    blocks = res["blocks"]
    title = res["title"]
    # The classifier reads the text as extracted (it never leaves this process): a document that
    # holds a key is the kind that belongs on the vault shelf.
    sample = "\n".join(b["text"] for b in blocks[:40])[:4000]
    chosen = shelf or (classify(p, title, sample) if classify else "open")
    if chosen == "vault" and not store.vault_open():
        store.set_state(doc_id, "skipped:sensitive", "looks sensitive, and the vault isn't set up")
        return {"state": "skipped", "doc_id": doc_id, "detail": "sensitive"}
    # Everything stored, embedded or quoted from here on is made from text with every key and token
    # cut out, on either shelf (see withhold).
    try:
        blocks = withhold.blocks(blocks)
        title = withhold.title(title)
    except withhold.Withheld as e:
        store.set_state(doc_id, "skipped:credential", str(e))
        return {"state": "skipped", "doc_id": doc_id, "detail": "credential"}
    except Exception:  # noqa: BLE001 - a check that cannot run is not a pass
        store.set_state(doc_id, "failed:couldn't be checked for keys")
        return {"state": "failed", "doc_id": doc_id, "detail": "couldn't be checked for keys"}
    try:
        sections = structure.build_sections(blocks, title)
        passages: list[dict] = []
        profiles: dict[int, str] = {}
        kids: dict[int, list[dict]] = {}
        for s in sections:
            kids.setdefault(s["parent"], []).append(s)
        for s in sections:
            for pa in structure.build_passages(s):
                pa["section"] = s["id"]
                passages.append(pa)
            profiles[s["id"]] = structure.section_profile(s, kids.get(s["id"], []))
        doc_profile = structure.document_profile(title, sections)
        stored = store.replace_content(doc_id, title=title, pages=res.get("pages"), shelf=chosen, blocks=blocks,
                                       sections=sections, passages=passages, doc_profile=doc_profile,
                                       section_profiles=profiles, ocr_pages=res.get("ocr_pages") or 0, sha256=sha)
        try:
            vectors.index_document_vectors(store, doc_id, sections=sections, passages=passages,
                                           doc_profile=doc_profile, section_profiles=profiles,
                                           sec_ids=stored["sections"])
        except Exception:  # noqa: BLE001 - a document without vectors is still keyword-searchable
            pass
    except _ext.CapExceeded as e:
        store.set_state(doc_id, "failed:" + str(e))
        return {"state": "failed", "doc_id": doc_id, "detail": str(e)}
    return {"state": "indexed", "doc_id": doc_id, "detail": None}


def sweep_scope(store: Store, scope: str | Path, *, recursive: bool = True, glob: str | None = None,
                kinds: set[str] | None = None, classify: Classify | None = None,
                allowed: Callable[[Path], bool] | None = None, gate=None, force: bool = False,
                on_gone: Callable[[int], None] | None = None) -> dict:
    """Index new and changed files under `scope`; purge rows of files that are
    gone. `allowed` is the consent check: a path it refuses is not read.
    `on_gone(doc_id)` removes a document whose file is gone the way a removal does (graph,
    caches, remembered searches); without it only the index rows go."""
    out = {"indexed": 0, "unchanged": 0, "failed": 0, "skipped": 0, "purged": 0}
    seen: set[str] = set()
    for p in scan_scope(scope, recursive=recursive, glob=glob, kinds=kinds):
        if allowed and not allowed(p):
            continue
        try:
            seen.add(str(p.resolve()))
        except OSError:
            continue
        if gate is not None:
            with gate():
                r = index_file(store, p, scope, classify=classify, force=force)
        else:
            r = index_file(store, p, scope, classify=classify, force=force)
        out[r["state"] if r["state"] in out else "failed"] += 1
    root = Path(scope)
    try:
        base = str(root.resolve() if root.is_dir() else root.resolve().parent)
    except OSError:
        base = str(root)
    for row in store.q("SELECT id, path FROM documents WHERE scope=?", (base,)):
        if row["path"] not in seen and not Path(row["path"]).exists():
            (on_gone or store.purge_document)(row["id"])
            out["purged"] += 1
    store.drop_empty_folders()
    try:
        vectors.refresh_folders(store)
    except Exception:  # noqa: BLE001
        pass
    return out


class Indexer:
    """A background worker that indexes queued files, one at a time."""

    def __init__(self, principal: str = OWNER, *, can_run: Callable[[], bool] | None = None,
                 classify: Classify | None = None, allowed: Callable[[Path], bool] | None = None,
                 gate=None):
        self.principal = principal
        self.can_run = can_run or (lambda: True)
        self.classify = classify
        self.allowed = allowed
        self.gate = gate
        self._q: list[tuple] = []
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop = False
        self.current: str | None = None

    def enqueue(self, scope: str | Path, **kw) -> None:
        with self._cv:
            self._q.append((str(scope), kw))
            self._cv.notify()
        self.start()

    def start(self) -> None:
        with self._cv:
            if self._thread and self._thread.is_alive():
                return
            self._stop = False
            self._thread = threading.Thread(target=self._loop, name="library-indexer", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    def pending(self) -> int:
        with self._cv:
            return len(self._q) + (1 if self.current else 0)

    def _remove_gone(self, doc_id: int) -> None:
        """A document whose file is gone is removed like any removal: graph, caches and the
        remembered last search go with its rows."""
        from agent_friday.services.library import forget
        forget.remove_document(self.principal, doc_id)

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._q and not self._stop:
                    self._cv.wait(timeout=30)
                    if not self._q:
                        return
                if self._stop:
                    return
                scope, kw = self._q.pop(0)
            while not self.can_run():
                time.sleep(2)
                if self._stop:
                    return
            self.current = scope
            try:
                sweep_scope(store_for(self.principal), scope, classify=self.classify, allowed=self.allowed,
                            gate=self.gate, on_gone=self._remove_gone, **kw)
            except Exception:  # noqa: BLE001 - one scope's failure must not stop the queue
                pass
            finally:
                self.current = None

"""The meetings archive: transcripts and notes as Library documents, in a scope of their own.

"What did we decide about the budget?" is answered by the Library's own path
(search_library, then the cited answer), over documents made from the
meetings. Two documents per meeting, both built here from the meeting record
and indexed through the Library's own pipeline:

  transcript  one block per spoken line, with its time, so a citation points at
              one line and the reader can open the recording at that moment;
  notes       the summary, the decisions, the action items and the owner's
              own bullets, each expanded line followed by the transcript lines
              it rests on.

A dedicated scope
-----------------
The documents carry ``scope = "meetings"`` and a path that is not a file the
Library reads: ``<home>/meetings/<id>/library/<part>.md``. Because no folder
consent covers such a path, ``is_archive_path`` is the one place that says
whether the Library may show it: only while the owner has chosen to make
meetings searchable (``settings.meeting_archive == "on"``). Nothing is indexed
until they choose. Choosing "off" removes everything that was indexed.

Local only, by construction
---------------------------
The archive's text is never offered to a cloud model: ``library.tools`` leaves
documents of this scope out of any cloud answer whatever file grants say, and
``search`` scopes a question to it only by the word "meetings". Deleting a
meeting, by hand or by a retention choice, removes its documents in the same
step. This module opens no connection and names no provider.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

from agent_friday.services import meeting_workspace as mw

log = logging.getLogger("friday.meetings.archive")

SCOPE = "meetings"
OWNER = "owner"
PARTS = {"transcript": "transcript.md", "notes": "notes.md"}
_ID_RE = re.compile(r"^[0-9a-f]{12}$")
CHOICES = ("on", "off")


_FILE: dict = {"key": None, "val": {}}


def _settings() -> dict:
    """The persisted settings, read straight from their file and re-read only when it changes.

    The owner's yes is what is wanted here, as persisted. The full settings loader also resolves model
    seats and may start a liveness check of the local model server, which a hook that runs when notes
    finish, or once per document in a Library search, must not do."""
    try:
        from agent_friday import core
        p = core.SETTINGS_FILE
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
        if _FILE["key"] != key:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            _FILE.update(key=key, val=data if isinstance(data, dict) else {})
        return _FILE["val"]
    except Exception:
        return {}


def choice() -> str:
    """"" until the owner chooses, then "on" or "off"."""
    v = str(_settings().get("meeting_archive") or "")
    return v if v in CHOICES else ""


def visible() -> bool:
    return choice() == "on"


def path_for(mid: str, part: str) -> Path:
    from agent_friday.services import meeting_capture as mc
    return mc._root() / mid / "library" / PARTS[part]


def is_archive_path(path) -> bool:
    """True for the path of a meeting's archive document. Judged by shape, so it holds
    whatever the platform does to the spelling."""
    try:
        parts = Path(str(path)).parts
    except (TypeError, ValueError):
        return False
    return (len(parts) >= 4 and parts[-1] in PARTS.values() and parts[-2] == "library"
            and bool(_ID_RE.fullmatch(parts[-3])) and parts[-4] == "meetings")


def covers(path) -> bool:
    """May the Library show this path? Used by the Library's consent check for archive paths."""
    return is_archive_path(path) and visible()


def is_archive_doc(row) -> bool:
    try:
        return row["scope"] == SCOPE and is_archive_path(row["path"])
    except (KeyError, TypeError, IndexError):
        return False


def _store(store=None):
    if store is not None:
        return store
    from agent_friday.services.library.store import store_for
    return store_for(OWNER)


def _has_library() -> bool:
    """Whether the owner's Library index exists yet. Taking documents out of one that was never made
    must not make it."""
    try:
        from agent_friday.services.library.store import principal_dir
        return (principal_dir(OWNER) / "library.sqlite").exists()
    except Exception:
        return False


NOT_ENCRYPTED = "Not available on this PC: the Library index isn't encrypted."


def unavailable(store=None) -> str:
    """Why meetings cannot be indexed here, or "". A meeting's transcript is encrypted in its own store, so
    the archive must not copy it into an index that is a plain file: it is allowed only when the Library
    index is actually encrypted (sqlcipher present and keyed)."""
    try:
        return "" if bool(getattr(_store(store), "encrypted", False)) else NOT_ENCRYPTED
    except Exception:  # noqa: BLE001 - an index that cannot be opened is not an encrypted one
        return NOT_ENCRYPTED


def _index_one(store, mid: str, part: str, title: str, blocks: list, force: bool = False) -> str:
    from agent_friday.services.library import structure, vectors, withhold
    from agent_friday.services.library.store import INDEX_VERSION
    from agent_friday.services.library.textclean import safe_title
    path = str(path_for(mid, part))
    sha = hashlib.sha256(json.dumps([title, [b["text"] for b in blocks]], ensure_ascii=False)
                         .encode("utf-8")).hexdigest()
    prior = store.find_document(path)
    if (prior and not force and prior["state"] == "indexed" and prior["sha256"] == sha
            and prior["index_version"] == INDEX_VERSION):
        return "unchanged"
    fid = store.folder_id(SCOPE, "")
    doc_id = store.upsert_document(
        path, sha256=sha, size=sum(len(b["text"]) for b in blocks), mtime=time.time(), kind="markdown",
        ext="md", scope=SCOPE, folder_id=fid, title=withhold.title(safe_title(title)), shelf="open")
    try:
        blocks = withhold.blocks(blocks)
        title = withhold.title(title)
    except withhold.Withheld as e:
        store.set_state(doc_id, "skipped:credential", str(e))
        return "skipped"
    except Exception:  # noqa: BLE001 - a check that cannot run is not a pass
        store.set_state(doc_id, "failed:couldn't be checked for keys")
        return "failed"
    sections = structure.build_sections(blocks, title)
    passages, profiles, kids = [], {}, {}
    for s in sections:
        kids.setdefault(s["parent"], []).append(s)
    for s in sections:
        for pa in structure.build_passages(s):
            pa["section"] = s["id"]
            passages.append(pa)
        profiles[s["id"]] = structure.section_profile(s, kids.get(s["id"], []))
    doc_profile = structure.document_profile(title, sections)
    stored = store.replace_content(doc_id, title=title, pages=None, shelf="open", blocks=blocks,
                                   sections=sections, passages=passages, doc_profile=doc_profile,
                                   section_profiles=profiles, ocr_pages=0, sha256=sha)
    try:
        vectors.index_document_vectors(store, doc_id, sections=sections, passages=passages,
                                       doc_profile=doc_profile, section_profiles=profiles,
                                       sec_ids=stored["sections"])
    except Exception:  # noqa: BLE001 - a document without vectors is still keyword-searchable
        pass
    return "indexed"


def index_meeting(m: dict, *, store=None, force: bool = False) -> dict:
    """Index (or refresh) one meeting's transcript and notes. Does nothing until the owner has said
    yes, and never while the meeting is still being recorded or transcribed."""
    if choice() != "on":
        return {"indexed": 0, "skipped": "not chosen"}
    why = unavailable(store)
    if why:
        return {"indexed": 0, "skipped": why}
    if m.get("state") in ("recording", "transcribing"):
        return {"indexed": 0, "skipped": "still being recorded"}
    mid = str(m.get("id") or "")
    if not _ID_RE.fullmatch(mid):
        return {"indexed": 0, "skipped": "no such meeting"}
    st = _store(store)
    lines = mw.spoken(m)
    docs = {}
    if lines:
        docs["transcript"] = (mw.archive_title(m, "transcript"), mw.transcript_blocks(lines))
    nb = mw.notes_blocks(m)
    if nb:
        docs["notes"] = (mw.archive_title(m, "notes"), nb)
    out = {"indexed": 0, "unchanged": 0, "removed": 0}
    for part, (title, blocks) in docs.items():
        r = _index_one(st, mid, part, title, blocks, force=force)
        if r in out:
            out[r] += 1
    for part in PARTS:
        if part not in docs:
            out["removed"] += _remove_part(st, mid, part)
    return out


def _remove_part(store, mid: str, part: str) -> int:
    doc = store.find_document(str(path_for(mid, part)))
    if not doc:
        return 0
    try:
        from agent_friday.services.library import forget
        forget.remove_document(OWNER, doc["id"])
    except Exception as e:  # noqa: BLE001 - fall back to the plain purge
        log.info("archive document %s not removed through forget: %s", doc["id"], e)
        store.purge_document(doc["id"], keep_row=False)
    return 1


def remove_meeting(mid: str, *, store=None) -> int:
    """Remove a meeting's documents from the Library. Called whenever a meeting is deleted."""
    if not _ID_RE.fullmatch(str(mid or "")) or (store is None and not _has_library()):
        return 0
    try:
        st = _store(store)
        return sum(_remove_part(st, mid, part) for part in PARTS)
    except Exception as e:  # noqa: BLE001 - never blocks a deletion
        log.info("meeting %s: archive not cleared: %s", mid, e)
        return 0


def remove_all(*, store=None) -> int:
    if store is None and not _has_library():
        return 0
    st = _store(store)
    n = 0
    for row in st.q("SELECT id, path, scope FROM documents WHERE scope=?", (SCOPE,)):
        if is_archive_path(row["path"]):
            try:
                from agent_friday.services.library import forget
                forget.remove_document(OWNER, row["id"])
            except Exception:  # noqa: BLE001
                st.purge_document(row["id"], keep_row=False)
            n += 1
    return n


def reindex_all(manager, *, store=None, force: bool = False) -> dict:
    """Index every finished meeting. Run after the owner says yes, off the request's thread."""
    out = {"meetings": 0, "indexed": 0}
    if choice() != "on":
        return out
    for row in manager.list():
        if row.get("state") in ("recording", "transcribing", "unreadable"):
            continue
        try:
            r = index_meeting(manager.get(row["id"]), store=store, force=force)
        except Exception as e:  # noqa: BLE001 - one meeting must not stop the rest
            log.info("meeting %s not indexed: %s", row.get("id"), e)
            continue
        out["meetings"] += 1
        out["indexed"] += r.get("indexed", 0)
    return out


def set_choice(value: str, *, store=None) -> dict:
    if value not in CHOICES:
        raise ValueError("unknown archive choice")
    from agent_friday.core import _save_settings
    if value == "on":
        why = unavailable(store)
        if why:
            return status(store=store) | {"refused": why}
    _save_settings({"meeting_archive": value})
    if value == "off":
        remove_all(store=store)
    return status(store=store)


def status(*, store=None) -> dict:
    n, encrypted = 0, None
    try:
        st = _store(store)
        n = st.one("SELECT count(*) n FROM documents WHERE scope=? AND state='indexed'", (SCOPE,))["n"]
        encrypted = bool(getattr(st, "encrypted", False))
    except Exception:  # noqa: BLE001 - a Library that cannot be opened has no documents to count
        pass
    return {"choice": choice(), "documents": n, "scope": SCOPE, "index_encrypted": encrypted,
            "available": encrypted is True, "unavailable_reason": "" if encrypted is True else NOT_ENCRYPTED,
            "options": [{"value": "on", "label": "Make my meetings searchable", "recommended": True},
                        {"value": "off", "label": "Not now", "recommended": False}]}

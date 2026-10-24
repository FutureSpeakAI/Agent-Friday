"""Import staging: nothing imported becomes durable before the owner answers.

North Star §8.10 (NS-8.10-2, -3) and the first-run spec §6.4 and §7.2: an import is
preview-first and staged. A source (today the LinkedIn connections export) is read
into this staging area, one JSON file per item under ``~/.friday/wiki/_staging/``
with the ``imp-`` prefix (the Library's drafts beside it use ``lib-``; the wiki graph
skips ``_staging``). Each item carries the §7.2 fields: its source and source
reference, when it was observed, a confidence, ``authority: "import"``, a sensitivity
and a status. Nothing in the people graph, memory, the knowledge graph or the wiki
changes until the owner answers:

* **accept** (or **edit**, which accepts the edited fields) runs the commit hook the
  item's kind registered, exactly once, and marks the item accepted;
* **reject** keeps a tombstone with none of the item's content, so the same item is not
  proposed again by a later import of the same file;
* **defer** keeps the item waiting.

Answers can be given per item or for a whole class (every waiting item of one source
and kind). Every answer is appended to ``imp-answers.jsonl`` (who, what, when; no item
content), and a commit that fails leaves the item waiting with the reason.

What this module does not do: it never reaches a model or the network, and it never
decides on the owner's behalf. Policy-covered auto-acceptance (§6.4 step 7, "or
policy-covered") is not offered yet.

Forgetting a person removes every waiting import item that names them, and keeps
later imports from proposing them again (``forget_registry`` store
``import_staging``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from agent_friday import paths

STATUSES = ("proposed", "accepted", "rejected", "deferred")
OPEN_STATUSES = ("proposed", "deferred")
ANSWERS = {"accept": "accepted", "edit": "accepted", "reject": "rejected", "defer": "deferred"}
MAX_ITEMS_PER_STAGE = 30000
LABEL_MAX = 160
FIELD_MAX = 300
_ID = re.compile(r"imp-[a-z0-9_]{1,24}-[0-9a-f]{16}")
_SOURCE = re.compile(r"[a-z0-9_]{1,24}")

_lock = threading.RLock()

#: kind -> {"commit": fn(payload) -> dict, "fields": tuple of editable payload keys}
_KINDS: Dict[str, dict] = {}


def register_kind(kind: str, commit: Callable[[dict], dict], editable=()) -> None:
    """Declare how an accepted item of ``kind`` becomes durable. ``commit`` gets the item's
    payload (after any edit) and returns a dict; raising leaves the item waiting."""
    _KINDS[str(kind)] = {"commit": commit, "fields": tuple(editable)}


def _ensure_kinds() -> None:
    """The kinds the shipped importers declare. A review can come in a process where no import
    ran yet, so the declarations are loaded here rather than only when an import runs."""
    if "person" not in _KINDS:
        try:
            from agent_friday.services import relationship_memory as _rm
            _rm._register_import_kind()
        except Exception:
            pass


def staging_dir() -> Path:
    return paths.friday_home() / "wiki" / "_staging"


def _answers_path() -> Path:
    return staging_dir() / "imp-answers.jsonl"


def _clean(value, limit: int = FIELD_MAX) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value if value is not None else ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _write_atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _fingerprint(source: str, kind: str, key: str) -> str:
    return hashlib.sha256(("%s\x1f%s\x1f%s" % (source, kind, key)).encode("utf-8")).hexdigest()[:16]


def _path_for(item_id: str) -> Optional[Path]:
    if not _ID.fullmatch(str(item_id or "")):
        return None
    root = os.path.realpath(staging_dir())
    full = os.path.realpath(os.path.join(root, item_id + ".json"))
    if not full.startswith(root + os.sep):
        return None
    return Path(full)


def _read(path: Path) -> Optional[dict]:
    try:
        it = json.loads(path.read_text(encoding="utf-8"))
        return it if isinstance(it, dict) else None
    except (OSError, ValueError):
        return None


def _item_files() -> List[Path]:
    d = staging_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("imp-*.json") if _ID.fullmatch(p.stem))


def _note_answer(item: dict, answer: str, by: str, result: str) -> None:
    try:
        line = {"at": time.time(), "id": item.get("id"), "source": item.get("source"),
                "kind": item.get("kind"), "answer": answer, "result": result, "by": by}
        _answers_path().parent.mkdir(parents=True, exist_ok=True)
        with open(_answers_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except OSError:
        pass


# ── staging ──────────────────────────────────────────────────────────────────

def stage(source: str, kind: str, rows: List[dict], *, source_ref: str = "") -> dict:
    """Stage ``rows`` from one import. Each row is ``{"key", "label", "payload",
    "observed_at"?, "confidence"?, "sensitivity"?, "names"?, "emails"?}``: ``key`` identifies
    the row within its source (the same row imported again is the same item), ``label`` is
    the one line the owner reads, ``names``/``emails`` say who it is about (for forgetting).

    Writes only into staging. Returns counts: staged (new or still waiting), already
    decided (accepted or rejected before), forgotten (names a person the owner asked
    Friday to forget)."""
    _ensure_kinds()
    source, kind = str(source or ""), str(kind or "")
    if not _SOURCE.fullmatch(source) or kind not in _KINDS:
        return {"ok": False, "error": "unknown import source or kind"}
    f_names, f_emails = _forgotten()
    staged = decided = forgotten = 0
    now = time.time()
    with _lock:
        for seq, row in enumerate(rows[:MAX_ITEMS_PER_STAGE]):
            key = _clean(row.get("key"), 400)
            if not key:
                continue
            names = {_norm_name(_clean(n, 120)) for n in (row.get("names") or []) if _clean(n, 120)}
            emails = {_clean(e, 200).lower() for e in (row.get("emails") or []) if _clean(e, 200)}
            if names & f_names or emails & f_emails:
                forgotten += 1
                continue
            iid = "imp-%s-%s" % (source, _fingerprint(source, kind, key))
            path = staging_dir() / (iid + ".json")
            old = _read(path)
            if old and old.get("status") in ("accepted", "rejected"):
                decided += 1
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            item = {
                "schema": 1, "id": iid, "kind": kind, "source": source,
                "source_ref": _clean(source_ref, 200), "label": _clean(row.get("label"), LABEL_MAX),
                "payload": {str(k)[:40]: (_clean(v) if not isinstance(v, (list, dict)) else v)
                            for k, v in payload.items()},
                "names": sorted(names), "emails": sorted(emails),
                "observed_at": _clean(row.get("observed_at"), 40) or None,
                "valid_from": None, "valid_until": None,
                "confidence": row.get("confidence") if isinstance(row.get("confidence"), (int, float)) else None,
                "authority": "import", "sensitivity": _clean(row.get("sensitivity"), 20) or "personal",
                "status": (old or {}).get("status") if (old or {}).get("status") in OPEN_STATUSES else "proposed",
                "staged_ts": (old or {}).get("staged_ts") or now, "updated_ts": now,
                # The row's place in its file: answers for a whole list follow the file's order,
                # so which of two rows for one person is saved first is the file's, not a hash's.
                "seq": (old or {}).get("seq", seq),
            }
            _write_atomic(path, item)
            staged += 1
    return {"ok": True, "staged": staged, "already_decided": decided, "forgotten": forgotten,
            "source": source}


# ── reading ──────────────────────────────────────────────────────────────────

def list_items(source: Optional[str] = None, status: Optional[str] = None,
               kind: Optional[str] = None) -> List[dict]:
    """Items, oldest first. ``status="open"`` means waiting (proposed or deferred)."""
    want = set(OPEN_STATUSES) if status == "open" else ({status} if status else None)
    out = []
    for p in _item_files():
        it = _read(p)
        if not it:
            continue
        if source and it.get("source") != source:
            continue
        if kind and it.get("kind") != kind:
            continue
        if want is not None and it.get("status") not in want:
            continue
        out.append(it)
    out.sort(key=lambda it: (it.get("staged_ts") or 0, it.get("seq") if isinstance(it.get("seq"), int) else 0,
                             it.get("id")))
    return out


def counts(source: Optional[str] = None) -> dict:
    c = {s: 0 for s in STATUSES}
    for it in list_items(source=source):
        c[it.get("status") if it.get("status") in c else "proposed"] += 1
    return c


def public(it: dict) -> dict:
    """What the review list shows: no payload beyond the label and its provenance."""
    return {k: it.get(k) for k in ("id", "kind", "source", "label", "status", "observed_at",
                                   "confidence", "authority", "sensitivity", "staged_ts", "error")}


# ── answering ────────────────────────────────────────────────────────────────

def review(item_id: str, answer: str, *, edits: Optional[dict] = None, by: str = "owner") -> dict:
    """Answer one item: accept, edit (accept with changed fields), reject or defer."""
    answer = str(answer or "").lower()
    status = ANSWERS.get(answer)
    path = _path_for(item_id)
    if status is None or path is None:
        return {"ok": False, "error": "say accept, edit, reject or defer, for one of the imported items"}
    with _lock:
        it = _read(path)
        if not it or it.get("status") not in OPEN_STATUSES:
            return {"ok": False, "error": "that item is not waiting for an answer"}
        if status == "rejected":
            _write_atomic(path, {"schema": 1, "id": it["id"], "kind": it.get("kind"),
                                 "source": it.get("source"), "status": "rejected",
                                 "reviewed_ts": time.time()})
            _note_answer(it, answer, by, "rejected")
            return {"ok": True, "status": "rejected"}
        if status == "deferred":
            it["status"] = "deferred"
            it["reviewed_ts"] = time.time()
            _write_atomic(path, it)
            _note_answer(it, answer, by, "deferred")
            return {"ok": True, "status": "deferred"}
        _ensure_kinds()
        spec = _KINDS.get(it.get("kind"))
        if spec is None:
            return {"ok": False, "error": "this kind of item cannot be saved yet"}
        payload = dict(it.get("payload") or {})
        if answer == "edit":
            for k, v in (edits or {}).items():
                if k in spec["fields"]:
                    payload[k] = _clean(v)
        f_names, f_emails = _forgotten()
        names = set(it.get("names") or []) | ({_norm_name(payload["name"])} if payload.get("name") else set())
        emails = set(it.get("emails") or []) | ({str(payload["email"]).lower()} if payload.get("email") else set())
        if names & f_names or emails & f_emails:
            path.unlink(missing_ok=True)
            _note_answer(it, answer, by, "forgotten")
            return {"ok": False, "error": "that person is on your forget list, so nothing was saved"}
        try:
            result = spec["commit"](payload) or {}
        except Exception as e:
            it["error"] = _clean("not saved: %s" % type(e).__name__, 120)
            _write_atomic(path, it)
            _note_answer(it, answer, by, "failed")
            return {"ok": False, "error": "it could not be saved; it is still waiting"}
        _write_atomic(path, {"schema": 1, "id": it["id"], "kind": it.get("kind"),
                             "source": it.get("source"), "status": "accepted",
                             "reviewed_ts": time.time()})
        _note_answer(it, answer, by, "accepted")
        return {"ok": True, "status": "accepted", "result": result}


def review_class(source: str, answer: str, kind: Optional[str] = None, by: str = "owner") -> dict:
    """Answer every waiting item of one source (and kind): accept, reject or defer."""
    if str(answer or "").lower() not in ("accept", "reject", "defer"):
        return {"ok": False, "error": "say accept, reject or defer for the whole list"}
    if not _SOURCE.fullmatch(str(source or "")):
        return {"ok": False, "error": "unknown import source"}
    done = failed = 0
    for it in list_items(source=source, status="open", kind=kind):
        out = review(it["id"], answer, by=by)
        if out.get("ok"):
            done += 1
        else:
            failed += 1
    return {"ok": True, "done": done, "failed": failed}


# ── forgetting ───────────────────────────────────────────────────────────────

def _norm_name(n) -> str:
    return " ".join(str(n or "").lower().replace("_", " ").split())


def _forgotten():
    """(names, emails) tombstoned by forget_person, normalised as the items store them."""
    try:
        from agent_friday.services import forget_person as fp
        return ({_norm_name(n) for n in fp.forgotten_names()},
                {str(e).strip().lower() for e in fp.forgotten_emails()})
    except Exception:
        return set(), set()


def _naming(names, emails) -> List[Path]:
    names = {_norm_name(n) for n in names if str(n).strip()}
    emails = {str(e).strip().lower() for e in emails if str(e).strip()}
    hit = []
    for p in _item_files():
        it = _read(p)
        if not it or it.get("status") not in OPEN_STATUSES:
            continue
        if set(it.get("names") or []) & names or set(it.get("emails") or []) & emails:
            hit.append(p)
    return hit


def forget_find(names, emails) -> int:
    return len(_naming(names, emails))


def forget_purge(names, emails) -> int:
    n = 0
    with _lock:
        for p in _naming(names, emails):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n

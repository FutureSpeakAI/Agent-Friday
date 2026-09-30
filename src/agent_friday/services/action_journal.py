"""Receipts for the changes Friday makes to the owner's things, and their undo.

Every organize action (a file moved, a page renamed, mail archived) writes one
receipt: what was asked, each item's before and after, what happened to it,
and what undo needs to put it back. The receipt is the action's record and its
undo handle at once; its shape follows the delivery-receipts design
(docs/design/active/goals-and-delivery-receipts.md, section 5): a `rcpt_` id,
the run it belongs to, a status, and per-item results.

Receipts live in ~/.friday/actions/receipts.jsonl, one JSON object per line,
append-only: an update (the action finished, or was undone) is a later line
for the same id, and reading folds them. While the conversation is off the
record, only what a receipt needs (the tool, the decision, the time, the id)
is written; the items and the undo data stay in this process's memory, so undo
still works for the session and nothing about the things touched reaches disk.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_MEM: dict[str, dict] = {}          # receipt id -> the full receipt (this process)
_LOADED = {"done": False}

#: How many receipts undo can reach back through, newest first.
MAX_KEPT = 500


def _path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "actions" / "receipts.jsonl"


def new_id() -> str:
    """A sortable receipt id: rcpt_<ms timestamp hex><random>."""
    return "rcpt_%011x%s" % (int(time.time() * 1000), secrets.token_hex(4))


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return off_record.skip("action_receipts")
    except Exception:
        return False


def _load() -> None:
    if _LOADED["done"]:
        return
    _LOADED["done"] = True
    p = _path()
    if not p.is_file():
        return
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-4 * MAX_KEPT:]
    except OSError:
        return
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        rid = rec.get("receipt_id")
        if rid:
            _MEM[rid] = {**_MEM.get(rid, {}), **rec}


def _append(rec: dict) -> None:
    if _off_record():
        try:
            from agent_friday.services import off_record
            rec = off_record.receipt_view(rec)
        except Exception:
            rec = {"receipt_id": rec.get("receipt_id"), "tool": rec.get("tool"),
                   "status": rec.get("status"), "at": rec.get("at")}
        rec["off_record"] = True
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


def record(tool: str, action: str, domain: str, *, items: list, summary: str,
           status: str, undo: dict | None = None, approval_id: str | None = None,
           conversation_id: str | None = None, surface: str | None = None,
           receipt_id: str | None = None, extra: dict | None = None) -> dict:
    """Write one receipt and return it."""
    rec: dict[str, Any] = {
        "receipt_id": receipt_id or new_id(), "v": 1,
        "run": {"kind": "action", "conversation_id": conversation_id, "surface": surface},
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "action": action,
        "domain": domain, "status": status, "summary": summary,
        "items": items, "undo": undo or {}, "approval_id": approval_id,
    }
    if extra:
        rec.update(extra)
    with _LOCK:
        _load()
        _MEM[rec["receipt_id"]] = rec
        _append(rec)
    return rec


def update(receipt_id: str, **fields) -> dict | None:
    """Fold new fields into a receipt (a later line in the file)."""
    with _LOCK:
        _load()
        cur = _MEM.get(receipt_id)
        if cur is None:
            return None
        cur.update(fields)
        _append({"receipt_id": receipt_id, **fields})
        return dict(cur)


def get(receipt_id: str) -> dict | None:
    with _LOCK:
        _load()
        rec = _MEM.get(receipt_id)
        return dict(rec) if rec else None


def latest(conversation_id: str | None = None, undoable: bool = True) -> dict | None:
    """The newest receipt (in this conversation, when given) that can still
    be undone."""
    with _LOCK:
        _load()
        recs = sorted(_MEM.values(), key=lambda r: r.get("receipt_id") or "", reverse=True)
    for r in recs[:MAX_KEPT]:
        if conversation_id and (r.get("run") or {}).get("conversation_id") not in (None, conversation_id):
            continue
        if undoable and (r.get("undone") or not r.get("undo") or r.get("status") not in ("complete", "partial")):
            continue
        return dict(r)
    return None


def recent(limit: int = 20) -> list[dict]:
    """The newest receipts, newest first."""
    with _LOCK:
        _load()
        recs = sorted(_MEM.values(), key=lambda r: r.get("receipt_id") or "", reverse=True)
    return [dict(r) for r in recs[:max(0, limit)]]


def public_view(rec: dict) -> dict:
    """A receipt as the page shows it: what happened, without the undo data."""
    out = {k: v for k, v in (rec or {}).items() if k != "undo"}
    out["undoable"] = bool((rec or {}).get("undo")) and not (rec or {}).get("undone") \
        and (rec or {}).get("status") in ("complete", "partial")
    return out


def reset() -> None:
    """Forget the in-memory receipts (tests)."""
    with _LOCK:
        _MEM.clear()
        _LOADED["done"] = False


def home_trash(receipt_id: str) -> Path:
    """Where a trashed item is kept until undo or an explicit empty: never a
    deletion. Beside Friday's own data, outside every folder the owner browses
    and outside the wiki, so no search, scan or graph ever lists it."""
    from agent_friday.paths import friday_home
    return friday_home() / "trash" / receipt_id


def safe_move(src: Path, dst: Path) -> None:
    """Move a file or folder, never onto an existing one. On one drive: a
    rename. A file moving to another drive is copied, checked, and only then
    removed from where it was; a folder does not move across drives."""
    import errno
    if dst.exists():
        raise FileExistsError(str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        # ERROR_NOT_SAME_DEVICE on Windows, EXDEV elsewhere.
        if getattr(e, "winerror", None) != 17 and e.errno != errno.EXDEV:
            raise
    if Path(src).is_dir():
        raise OSError("a folder cannot move to another drive")
    import shutil
    shutil.copy2(src, dst)
    if dst.stat().st_size != Path(src).stat().st_size:
        dst.unlink()
        raise OSError("the copy did not complete")
    os.unlink(src)

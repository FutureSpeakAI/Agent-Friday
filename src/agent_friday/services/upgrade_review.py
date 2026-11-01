"""What an upgrade or a restore could not sort out, shown to the owner until
the owner marks it sorted (north star §37.3-10).

Items live in ``<home>/migrations/unresolved.json``. Each one says, in plain
words, what could not be done and what to do about it:

    {"id", "code", "store", "said", "todo", "source", "first_at", "last_at",
     "count", "resolved_at"}

``code`` is a short fixed word (``inventory_unreadable``, ``rebuild_failed``,
``migration_failed``, ...), ``store`` the store or index it is about, ``said``
and ``todo`` Friday's own sentences (never file contents, paths outside the
data folder or error messages). The same code and store again is the same item
(its count goes up); an item marked sorted that comes back is open again. Items
are listed on the Doctor page and counted in the Doctor report; the owner marks
one sorted with a press on that page. Nothing here is sent anywhere.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

FILE = "unresolved.json"
MAX_ITEMS = 200
_LOCK = threading.Lock()
_WORD = set("abcdefghijklmnopqrstuvwxyz0123456789_-.")


def _path(home: Optional[Path] = None) -> Path:
    from agent_friday.paths import friday_home
    return Path(home or friday_home()) / "migrations" / FILE


def _read(p: Path) -> list:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in d if isinstance(x, dict) and x.get("id")] if isinstance(d, list) else []


def _write(p: Path, items: list) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".partial-" + uuid.uuid4().hex[:6])
    tmp.write_text(json.dumps(items, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def _word(s, n: int = 40) -> str:
    s = str(s or "").strip().lower()[:n]
    return s if s and set(s) <= _WORD else "unknown"


def _id(code: str, store: str) -> str:
    return "u-" + hashlib.sha256(("%s|%s" % (code, store)).encode()).hexdigest()[:12]


def add(code: str, store: str, said: str, todo: str = "", source: str = "upgrade",
        home: Optional[Path] = None) -> dict:
    """Record one thing the owner should look at. Returns the item."""
    code, store, source = _word(code), _word(store), _word(source, 20)
    iid = _id(code, store)
    now = time.time()
    p = _path(home)
    with _LOCK:
        items = _read(p)
        for it in items:
            if it["id"] == iid:
                it.update(said=str(said)[:400], todo=str(todo)[:400], last_at=now,
                          count=int(it.get("count") or 1) + 1, resolved_at=None, source=source)
                _write(p, items)
                return dict(it)
        it = {"id": iid, "code": code, "store": store, "said": str(said)[:400], "todo": str(todo)[:400],
              "source": source, "first_at": now, "last_at": now, "count": 1, "resolved_at": None}
        items.append(it)
        if len(items) > MAX_ITEMS:                       # the oldest sorted ones go first
            items.sort(key=lambda x: (x.get("resolved_at") is None, x.get("last_at") or 0))
            items = items[-MAX_ITEMS:]
        _write(p, items)
        return dict(it)


def listing(home: Optional[Path] = None, include_resolved: bool = False) -> list:
    items = _read(_path(home))
    if not include_resolved:
        items = [x for x in items if not x.get("resolved_at")]
    return sorted(items, key=lambda x: -(x.get("last_at") or 0))


def resolve(iid: str, home: Optional[Path] = None) -> Optional[dict]:
    """The owner marked it sorted. None when there is no such open item."""
    p = _path(home)
    with _LOCK:
        items = _read(p)
        for it in items:
            if it["id"] == iid and not it.get("resolved_at"):
                it["resolved_at"] = time.time()
                _write(p, items)
                return dict(it)
    return None


def counts(home: Optional[Path] = None) -> dict:
    """For the Doctor report: how many are open, by code. No sentences."""
    out: dict = {}
    for it in listing(home):
        out[it["code"]] = out.get(it["code"], 0) + 1
    return {"open": sum(out.values()), "by_code": out}

"""Which replies recalled a remembered thing: the influence record.

When a turn's prompt carries excerpts recalled from past conversations
(``model_router._build_memory_context_block``), one row is written here: when,
which conversation the turn belonged to, and the ids of the stored turns that
were recalled. Ids and times only, never the recalled text or the reply, so
the record shows the owner nothing they cannot already read in their own
conversations. The memory search's "why do you know this"
(services/memory_search) reads it to say how a remembered turn was used lately.

Nothing is written while the conversation is off the record. The file is
bounded (``KEEP`` rows). Leaf module: never raises to a caller.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

from agent_friday.paths import friday_home

KEEP = 2000
MAX_REFS = 12

_LOCK = threading.Lock()


def _path() -> Path:
    return friday_home() / "memory" / "recall-log.jsonl"


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return False


def _read() -> List[Dict[str, Any]]:
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def record(kind: str, refs: Iterable[Any], *, session_id: str = "") -> bool:
    """Note that these stored items were recalled into one turn's prompt."""
    ids = [str(r)[:64] for r in (refs or []) if str(r or "").strip()][:MAX_REFS]
    if not ids or _off_record():
        return False
    row = {"ts": time.time(), "kind": str(kind or "conversation")[:24], "refs": ids,
           "session": str(session_id or "")[:80]}
    p = _path()
    try:
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows = _read()
            if len(rows) > KEEP + KEEP // 4:
                tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    for r in rows[-KEEP:]:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.replace(tmp, p)
        return True
    except Exception:
        return False


def uses(kind: str, ref: str, limit: int = 10) -> Dict[str, Any]:
    """How often, and in which turns lately, this item was recalled: a count,
    the first and last time, and the newest `limit` uses (time and
    conversation id), newest first."""
    ref = str(ref or "")
    hits = [r for r in _read()
            if r.get("kind") == kind and ref and ref in (r.get("refs") or [])]
    hits.sort(key=lambda r: float(r.get("ts") or 0))
    recent = [{"ts": float(r.get("ts") or 0), "session": r.get("session") or ""}
              for r in reversed(hits[-max(1, int(limit)):])]
    return {"count": len(hits),
            "first": float(hits[0].get("ts") or 0) if hits else None,
            "last": float(hits[-1].get("ts") or 0) if hits else None,
            "recent": recent}


def forget_refs(refs: Iterable[Any]) -> int:
    """Drop these ids from every row (a deleted message leaves no trace here)."""
    gone = {str(r) for r in (refs or []) if str(r or "").strip()}
    if not gone:
        return 0
    n = 0
    try:
        with _LOCK:
            rows = _read()
            keep = []
            for r in rows:
                left = [x for x in (r.get("refs") or []) if x not in gone]
                n += len(r.get("refs") or []) - len(left)
                if left:
                    keep.append({**r, "refs": left})
            if n:
                p = _path()
                tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    for r in keep:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.replace(tmp, p)
    except Exception:
        return 0
    return n

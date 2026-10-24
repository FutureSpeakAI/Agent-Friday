"""Which replies recalled a remembered thing: the influence record.

When a turn's prompt carries excerpts recalled from past conversations
(``model_router._build_memory_context_block``), one row is written here: when,
which conversation the turn belonged to, and the ids of the stored turns that
were recalled. Ids and times only, never the recalled text or the reply, so
the record shows the owner nothing they cannot already read in their own
conversations. The memory search's "why do you know this"
(services/memory_search) reads it to say how a remembered turn was used lately.

The search_past_conversations tool (chat and voice) writes a row too, for the
matches it handed a model.

Nothing is written while the conversation is off the record (the setting,
whether or not it also stops storage). Deleting a message drops its ids;
clearing a conversation drops its messages' ids and its own rows. The file
is bounded (``KEEP`` rows), so it says what was recalled recently, not ever.
Leaf module: never raises to a caller.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from agent_friday.paths import friday_home

KEEP = 2000
MAX_REFS = 12

_LOCK = threading.Lock()


def _path() -> Path:
    return friday_home() / "memory" / "recall-log.jsonl"


def _off_record(settings: Optional[dict] = None) -> bool:
    """Is the conversation off the record? The setting itself, as conversation_fts treats an off-record
    message (never indexed), whether or not ``off_record_stops_storage`` is on: a recall made in an
    off-record conversation is not recorded either way. ``off_record.active()`` (storage stopped) also
    counts. When the settings cannot be read, nothing is recorded."""
    try:
        from agent_friday.services import off_record
        s = off_record._settings() if settings is None else (settings or {})
        return bool(s.get("off_record")) or bool(off_record.active(s))
    except Exception:
        return True


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


def record(kind: str, refs: Iterable[Any], *, session_id: str = "",
           settings: Optional[dict] = None, via: str = "") -> bool:
    """Note that these stored items were recalled into one turn's prompt (or, `via` "tool", handed to
    the model by search_past_conversations). `settings` is the turn's own settings when the caller
    already read them."""
    ids = [str(r)[:64] for r in (refs or []) if str(r or "").strip()][:MAX_REFS]
    if not ids or _off_record(settings):
        return False
    row = {"ts": time.time(), "kind": str(kind or "conversation")[:24], "refs": ids,
           "session": str(session_id or "")[:80]}
    if via:
        row["via"] = str(via)[:16]
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


def record_tool_hits(hits: Iterable[Dict[str, Any]], session_id: Any = None) -> bool:
    """search_past_conversations handed these full-text hits (services/conversation_fts) to a model:
    note their message ids, as a recall into the prompt is noted. The conversation is the tool call's
    own when `session_id` is not given."""
    ids = [str(h.get("message_id")) for h in (hits or []) if isinstance(h, dict) and h.get("message_id")]
    if not ids:
        return False
    if session_id is None:
        try:
            from agent_friday.services import agent
            session_id = agent._CURRENT_CONVERSATION.get() or ""
        except Exception:
            session_id = ""
    return record("conversation", ids, session_id=str(session_id or ""), via="tool")


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


def _rewrite(edit: Callable[[Dict[str, Any]], Any]) -> int:
    """Rewrite the record through `edit(row)` -> (row or None, how many refs it dropped)."""
    n = 0
    try:
        with _LOCK:
            keep = []
            for r in _read():
                new, dropped = edit(r)
                n += dropped
                if new is not None:
                    keep.append(new)
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


def forget_refs(refs: Iterable[Any]) -> int:
    """Drop these ids from every row (a deleted message leaves no trace here)."""
    gone = {str(r) for r in (refs or []) if str(r or "").strip()}
    if not gone:
        return 0

    def edit(r):
        refs_ = r.get("refs") or []
        left = [x for x in refs_ if x not in gone]
        return ({**r, "refs": left} if left else None), len(refs_) - len(left)
    return _rewrite(edit)


def forget_session(session_id: Any) -> int:
    """Drop every row written for this conversation's replies (it was cleared)."""
    sid = str(session_id or "")[:80]
    if not sid:
        return 0

    def edit(r):
        if (r.get("session") or "") == sid:
            return None, max(1, len(r.get("refs") or []))
        return r, 0
    return _rewrite(edit)

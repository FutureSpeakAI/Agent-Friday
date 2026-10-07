"""Off the record: nothing about the conversation is written to disk.

While `off_record` is on (and `off_record_stops_storage`, the default, has not
been turned off), a conversation, by chat or by voice, lives only in this
process's memory:

- `active()` is the one question every store asks before it writes. A store
  that would write conversation content (messages, transcripts, summaries,
  traces, trajectories, indexes, caches, journals) returns without writing.
- `remember` / `recalled` keep this session's off-record messages per
  conversation, so the model still sees the conversation while it lasts.
- `end()` drops all of it. It runs when off-record is switched off; a restart
  drops it too, because nothing was written.

The signed action receipts and governance logs are still written, but only
with what a receipt needs: the tool, its class, the decision and the time.
`receipt_view` reduces a record to that.
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
#: conversation id -> this session's off-record messages (never written).
_MEMORY: dict = {}
#: Incremented whenever private memory ends; late work cannot enter a new session.
_GENERATION = 0
#: Stores that declined a write while off-record, with a count (no content).
SKIPPED: dict = {}
#: Stores that keep their own off-record memory register a callable here and
#: it runs from `end()`, so switching off-record off empties every store.
_END_HOOKS: list = []

#: What a receipt or governance record keeps while off-record.
RECEIPT_FIELDS = ("tool", "tool_name", "name", "kind", "class", "action_class",
                  "policy_class", "risk", "tier", "ring", "decision", "status",
                  "verdict", "allowed", "ts", "time", "timestamp", "at",
                  "created_at", "decided_at", "id", "approval_id", "receipt_id",
                  "grant_id", "signature", "sig", "prev_hash", "hash", "seq",
                  "off_record")


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def stops_storage(settings: dict | None) -> bool:
    s = settings or {}
    return bool(s.get("off_record")) and bool(s.get("off_record_stops_storage", True))


def active(settings: dict | None = None) -> bool:
    """True while nothing about the conversation may be written to disk."""
    return stops_storage(_settings() if settings is None else settings)


def skip(store: str, settings: dict | None = None) -> bool:
    """`active()`, counting the declined write for `store` (no content kept)."""
    if not active(settings):
        return False
    with _LOCK:
        SKIPPED[store] = SKIPPED.get(store, 0) + 1
    return True


def _remember_locked(cid: str, message: dict) -> dict:
    message = dict(message or {})
    message.setdefault("ts", time.time())
    meta = dict(message.get("meta") or {})
    meta["off_record"] = True
    message["meta"] = meta
    _MEMORY.setdefault(str(cid), []).append(message)
    return message


def remember(cid: str, message: dict) -> dict:
    with _LOCK:
        return _remember_locked(cid, message)


def generation() -> int:
    """Identify the current private-memory lifetime without reading settings."""
    with _LOCK:
        return _GENERATION


def remember_if_active(cid: str, message: dict, *, generation: int) -> dict | None:
    """Keep delayed private output only in the private session that created it."""
    with _LOCK:
        if type(generation) is not int or generation != _GENERATION:
            return None
    # Settings resolution stays outside the memory lock. The generation check
    # below and end()'s clear share the lock, so an intervening end wins.
    if not active():
        return None
    with _LOCK:
        if generation != _GENERATION:
            return None
        return _remember_locked(cid, message)


def recalled(cid: str) -> list:
    with _LOCK:
        return list(_MEMORY.get(str(cid), []))


def on_end(fn) -> None:
    """Register a store's own "forget everything" for `end()`."""
    if fn not in _END_HOOKS:
        _END_HOOKS.append(fn)


def end() -> None:
    """Off-record is over: drop everything this session kept in memory."""
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
        _MEMORY.clear()
    for fn in list(_END_HOOKS):
        try:
            fn()
        except Exception:
            pass
    try:
        from agent_friday.core import CHAT_HISTORY
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if not (m or {}).get("off_record")]
    except Exception:
        pass


def receipt_view(record: dict) -> dict:
    """A governance record reduced to what the receipt needs (no content)."""
    out = {k: v for k, v in (record or {}).items() if k in RECEIPT_FIELDS
           and isinstance(v, (str, int, float, bool, type(None)))}
    out["off_record"] = True
    return out


def on_settings_change(before: dict, after: dict) -> None:
    """Switching off-record off ends the session's memory of it."""
    if stops_storage(before) and not bool((after or {}).get("off_record")):
        end()

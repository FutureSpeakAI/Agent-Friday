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


class _Unreadable(dict):
    """The settings could not be read. Empty, so `active()` (storage) still
    fails open as before: failing closed there would silently drop an
    on-record transcript the owner expects kept whenever the settings file
    could not be read. `excludes_learning()` (learning) reads it the other
    way: a turn whose off-record state is unknown is treated as off the
    record, and nothing is learned from it (NS-15.8-2)."""


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        s = _load_settings()
    except Exception:
        return _Unreadable()
    if s is None:
        return {}
    return s if isinstance(s, dict) else _Unreadable()


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


def excludes_learning(settings: dict | None = None) -> bool:
    """True while off-record is on: Friday learns nothing from the turn.

    NS-15.8-2: off-record content stays out of learning, introspection,
    personalization, background summaries and forensic snapshots. Unlike
    `active()` this does not depend on `off_record_stops_storage`: an owner
    who keeps the transcript still has an off-record turn, and nothing is
    inferred from it. Settings that cannot be read count as off-record (fail
    closed); `active()` keeps failing open (see `_Unreadable`). So do degraded
    settings (core._load_settings after settings.json existed but could not
    be read): the last good settings this process read or wrote for that file
    decide when there are any, otherwise the turn counts as off the record,
    because the factory defaults it would otherwise get say off_record False."""
    try:
        s = _settings() if settings is None else (settings or {})
        if isinstance(s, _Unreadable):
            return True
        if _degraded(s):
            last = _last_good()
            return True if last is None else bool(last.get("off_record"))
        return bool(s.get("off_record"))
    except Exception:
        return True


def _degraded(settings) -> bool:
    """True when `settings` came from a failed read of settings.json. A missing
    settings.json is not a failed read (core seeds the defaults)."""
    try:
        from agent_friday import core
        return bool(core._is_degraded_settings(settings))
    except Exception:
        return False


def _last_good() -> dict | None:
    """The last settings successfully read or written for the current settings
    file, or None when this process has none."""
    try:
        from agent_friday import core
        with core._SETTINGS_LAST_GOOD_LOCK:
            value = core._SETTINGS_LAST_GOOD.get("value")
            path = core._SETTINGS_LAST_GOOD.get("path")
            if value is None or path != str(core.SETTINGS_FILE):
                return None
            return dict(value)
    except Exception:
        return None


def skip_learning(learner: str, settings: dict | None = None,
                  off_record: bool | None = None) -> bool:
    """`excludes_learning()`, counting the skipped learner (no content kept).

    `off_record` is the turn's own state when the caller already knows it (a
    snapshot taken with the turn); None reads the current settings."""
    off = excludes_learning(settings) if off_record is None else bool(off_record)
    if not off:
        return False
    with _LOCK:
        key = "learn:" + str(learner)
        SKIPPED[key] = SKIPPED.get(key, 0) + 1
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

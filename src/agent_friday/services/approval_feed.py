"""Approval cards, pushed to every open Friday page as they change.

Each page (the desktop, a workspace in its own tab, the undocked chat, on
localhost or on Friday's named address, which is a relay into this same
process) holds one Server-Sent Events stream on /api/approvals/events. A card
that becomes pending is sent to all of them; a card that stops being pending
(approved, declined, expired, decided by voice, by a text reply or anywhere
else) is sent as resolved, and every page drops it.

The events are published by services/approvals.py at the three places a
card's status changes (create, decide, the expiry sweep), after its lock is
released, so what a page shows is exactly the stored card: nothing here
reads, filters or rewrites a card's content or provenance.

A page that connects or reconnects is sent the whole pending list first, so
nothing published while it was away is lost. A page that cannot keep up is
told to resync rather than silently missing an event.
"""
from __future__ import annotations

import logging
import queue as _queue
import threading

_log = logging.getLogger("friday.approval_feed")

#: Events a page may fall behind by before it is told to fetch the list again.
QUEUE_MAX = 64

_LOCK = threading.Lock()
_SUBS: set = set()


def subscribe() -> _queue.Queue:
    q: _queue.Queue = _queue.Queue(maxsize=QUEUE_MAX)
    with _LOCK:
        _SUBS.add(q)
    return q


def unsubscribe(q: _queue.Queue) -> None:
    with _LOCK:
        _SUBS.discard(q)


def subscribers() -> int:
    with _LOCK:
        return len(_SUBS)


def _put(q: _queue.Queue, event: dict) -> None:
    try:
        q.put_nowait(event)
        return
    except _queue.Full:
        pass
    # Behind: drop what it has not read and have it fetch the list again.
    try:
        while True:
            q.get_nowait()
    except _queue.Empty:
        pass
    try:
        q.put_nowait({"type": "resync"})
    except _queue.Full:
        pass


def publish(event: dict) -> None:
    """Send `event` to every connected page. Never raises."""
    try:
        with _LOCK:
            subs = list(_SUBS)
        for q in subs:
            _put(q, event)
    except Exception as e:           # a push must never break an approval
        _log.warning("approval feed publish failed: %s", e)


def card_pending(record: dict) -> None:
    publish({"type": "pending", "approval": dict(record)})


def card_resolved(record: dict) -> None:
    publish({"type": "resolved", "approval_id": record.get("approval_id"),
             "status": record.get("status"), "decided_by": record.get("decided_by"),
             "decided_at": record.get("decided_at")})


def reset() -> None:
    """Forget every subscriber (tests)."""
    with _LOCK:
        _SUBS.clear()

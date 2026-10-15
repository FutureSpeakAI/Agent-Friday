"""What Friday remembers about the owner, frozen for the length of a conversation.

The system prompt opens with blocks rendered from Friday's memory: the USER
MODEL (style, strong domains, most-used workspaces, durable facts) and the
LEARNED HEURISTICS. Both change while a conversation runs. Every chat turn
nudges the style traits and bumps a workspace counter, so a label flips from
"balanced" to "casual" or two workspaces swap places, and a save made during
the turn adds a line. One changed byte in the system prompt makes a local seat
(llama-server) re-read everything after it, and on the cloud it re-bills the
cached prefix and the replayed history.

So each block is captured the first time a conversation's prompt is built and
served from that capture on every later turn of the same conversation. A write
made during the conversation still lands on disk at once; it reaches the prompt
in the next conversation. This is the "frozen snapshot" rule from Hermes
(reference/hermes_memory_eval_2026-10-14.md, R2).

Two exceptions keep the owner in charge:

* **An owner's own change applies at once.** When the owner edits or deletes
  what Friday knows (the user-model routes, the memory panel), every snapshot
  is dropped and the next turn renders afresh: a line the owner deleted must
  not keep riding in the prompt of the chat they deleted it from.
* **A conversation left idle for IDLE_SECONDS starts a fresh snapshot.** By then
  the seat's cache has long moved on, and an old snapshot would only hide news.

Snapshots live in this process's memory only. Nothing new is written to disk:
the text is already in the stores it was rendered from, and a second copy on
disk would be one more place a deleted line could survive.

A call with no conversation (a background job, a seat warm) is its own
one-shot session and renders live.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, Optional

#: A conversation idle this long gets a fresh snapshot on its next turn.
IDLE_SECONDS = 6 * 3600

#: Conversations held at once; the least recently used is dropped first.
MAX_SCOPES = 256

_LOCK = threading.Lock()
_SNAPSHOTS: "OrderedDict[str, Dict]" = OrderedDict()
_OWNER_GENERATION = 0


def _clock() -> float:
    return time.monotonic()


def _live(scope: str, now: float) -> Optional[Dict]:
    """The scope's snapshot if it is still current; drops a stale one. Lock held."""
    snap = _SNAPSHOTS.get(scope)
    if snap is None:
        return None
    if snap["generation"] != _OWNER_GENERATION or now - snap["used"] > IDLE_SECONDS:
        _SNAPSHOTS.pop(scope, None)
        return None
    return snap


def frozen(scope: Optional[str], key: str, build: Callable[[], str]) -> str:
    """The block `key` as it was the first time `scope` asked for it.

    `build` renders the block live; it runs only when the scope has no current
    capture for `key`, and outside the lock (it may read a database). With no
    `scope`, `build` runs every time. A failing build yields "" and is not
    captured, so the next turn tries again.
    """
    if not scope:
        return _safe(build)
    scope = str(scope)
    now = _clock()
    with _LOCK:
        snap = _live(scope, now)
        if snap is not None and key in snap["blocks"]:
            snap["used"] = now
            _SNAPSHOTS.move_to_end(scope)
            return snap["blocks"][key]
        generation = _OWNER_GENERATION
    try:
        text = build()
    except Exception:
        return ""
    text = "" if text is None else str(text)
    with _LOCK:
        if generation != _OWNER_GENERATION:
            # The owner changed something while this rendered: serve it, keep
            # nothing, and let the next turn capture the new state.
            return text
        snap = _live(scope, now)
        if snap is None:
            snap = {"blocks": {}, "used": now, "generation": generation}
            _SNAPSHOTS[scope] = snap
        # First writer wins, so two racing turns agree on one capture.
        text = snap["blocks"].setdefault(key, text)
        snap["used"] = now
        _SNAPSHOTS.move_to_end(scope)
        while len(_SNAPSHOTS) > MAX_SCOPES:
            _SNAPSHOTS.popitem(last=False)
    return text


def owner_changed(reason: str = "") -> None:
    """The owner edited or deleted something Friday remembers: drop every
    snapshot so the change reaches the very next turn of every conversation."""
    global _OWNER_GENERATION
    with _LOCK:
        _OWNER_GENERATION += 1
        _SNAPSHOTS.clear()


def forget(scope: Optional[str]) -> None:
    """Drop one conversation's snapshot (the conversation was deleted)."""
    if not scope:
        return
    with _LOCK:
        _SNAPSHOTS.pop(str(scope), None)


def reset() -> None:
    """Drop everything. For tests and for a full memory erase."""
    with _LOCK:
        _SNAPSHOTS.clear()


def _safe(build: Callable[[], str]) -> str:
    try:
        text = build()
    except Exception:
        return ""
    return "" if text is None else str(text)

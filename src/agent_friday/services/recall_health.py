"""services/recall_health.py -- when long-term memory cannot be read (North Star 32.5, BD-16 FT2).

"The conversation continues. Friday says long-term recall is degraded when relevant. No
memory-dependent claim is presented as remembered fact."

Before this, every memory source that failed to read returned the same empty text as "nothing
relevant", so a turn could not tell a broken store from an empty one, and the model was free
to answer from what it believed it remembered. Each source now reports here:

    note_failure(source, exc=None)   the store could not be read this time
    note_ok(source)                  it was read (a later success clears the failure)

A source counts as degraded for WINDOW_S after its last failure unless it has read since.
`context_note()` is the line a turn's context carries while any source is degraded: it names
what could not be read and tells the model not to present anything as remembered from it.
`status()` is the Doctor's line. Nothing here reads or writes memory itself; nothing is stored
on disk (a full disk must not stop this from working).
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

#: A failure counts for this long unless the source reads successfully again.
WINDOW_S = 600.0

#: The sources and how the owner and the model hear them named.
SOURCES = {
    "past_conversations": "past conversations",
    "memory_card": "the memory card",
    "user_facts": "the facts the user has told you about themselves",
    "knowledge_graph": "the knowledge graph",
    "conversation_search": "conversation search",
}

_lock = threading.Lock()
_failed: Dict[str, Dict[str, object]] = {}


def note_failure(source: str, exc: Optional[BaseException] = None, *, now: Optional[float] = None) -> None:
    """A store could not be read. Never raises."""
    try:
        if source not in SOURCES:
            return
        kind = None
        if exc is not None:
            from agent_friday.services import failure_state
            kind = failure_state.classify(exc)
        with _lock:
            _failed[source] = {"ts": time.time() if now is None else now,
                               "kind": kind, "error": type(exc).__name__ if exc is not None else ""}
    except Exception:
        pass


def note_ok(source: str) -> None:
    """A store was read: any earlier failure of it is over. Never raises."""
    try:
        with _lock:
            _failed.pop(source, None)
    except Exception:
        pass


def degraded(*, now: Optional[float] = None) -> List[str]:
    """The sources that could not be read within the window, in SOURCES order."""
    now = time.time() if now is None else now
    with _lock:
        live = {k for k, v in _failed.items() if now - float(v.get("ts") or 0) < WINDOW_S}
    return [k for k in SOURCES if k in live]


def context_note(*, now: Optional[float] = None) -> str:
    """The turn's line while recall is degraded, or ''."""
    gone = degraded(now=now)
    if not gone:
        return ""
    names = ", ".join(SOURCES[k] for k in gone)
    return ("\n\n== RECALL DEGRADED ==\nLong-term recall is degraded right now: %s could not be read. "
            "Carry on with the conversation. Do not present anything as remembered from %s; if the "
            "answer depends on it, say that you cannot check your memory right now.\n" % (names, names))


def status(*, now: Optional[float] = None) -> Dict[str, object]:
    gone = degraded(now=now)
    with _lock:
        kinds = sorted({str(_failed[k].get("kind")) for k in gone if _failed.get(k, {}).get("kind")})
    return {"state": "degraded" if gone else "ok", "sources": gone, "kinds": kinds}


def _reset() -> None:
    """For tests."""
    with _lock:
        _failed.clear()

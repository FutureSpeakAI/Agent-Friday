"""Which conversations have just read Library text.

A reply that quotes the Library must not flow on into the places that keep or resend
what was said: the memory index, the day's summary, the voice-session distillation,
a cloud model's history. A chat turn knows it used the Library from its tool trace;
a spoken turn and a `read_file` of a Library document have no such trace where the
turn is saved. So a read leaves a short-lived mark on its conversation, and the code
that saves the turn takes the mark. Memory only; nothing here is written to disk.
"""
from __future__ import annotations

import threading
import time

WINDOW_S = 300.0
_LOCK = threading.Lock()
_MARKS: dict[str, float] = {}


def mark(conversation_id: str) -> None:
    """The conversation read Library text just now."""
    if not conversation_id:
        return
    with _LOCK:
        _MARKS[str(conversation_id)] = time.time()
        if len(_MARKS) > 256:
            for k in sorted(_MARKS, key=_MARKS.get)[:64]:
                _MARKS.pop(k, None)


def consume(conversation_id: str | None, within: float = WINDOW_S) -> bool:
    """True when the conversation read Library text within the window; the mark is taken, so the
    next turn starts clean."""
    if not conversation_id:
        return False
    with _LOCK:
        at = _MARKS.pop(str(conversation_id), None)
    return at is not None and time.time() - at <= within


def clear() -> None:
    with _LOCK:
        _MARKS.clear()

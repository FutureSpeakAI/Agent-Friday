"""services/failure_state.py -- one vocabulary for "why is this not done?" (BD-16, North Star 32).

Before this, each subsystem named its own failures: goals and plans have blockers
(services/goal_state), connectors have health states (services/connector_health), and a raw
exception reached the owner as its own text. This module is the shared classifier the release
burn-down asks for:

    external_wait     waiting on someone or something outside Friday (an approval, a reply)
    needs_user_input  only the owner can move it on
    recall_degraded   long-term memory cannot be read right now; the conversation goes on
    index_corruption  a derived index is damaged; it is rebuilt, sources are never deleted
    disk_full         the drive with Friday's data is (almost) full; background work pauses
    offline           the network or the service cannot be reached

`classify` maps an exception, a goal/plan blocker or a connector health state onto one of
these, or None when it is none of them (an ordinary error stays an ordinary error). `describe`
gives the words the owner reads and whether background work pauses. It never raises.
"""
from __future__ import annotations

import errno
import socket
import sqlite3
from typing import Any, Dict, Optional

EXTERNAL_WAIT = "external_wait"
NEEDS_USER_INPUT = "needs_user_input"
RECALL_DEGRADED = "recall_degraded"
INDEX_CORRUPTION = "index_corruption"
DISK_FULL = "disk_full"
OFFLINE = "offline"
STATES = (EXTERNAL_WAIT, NEEDS_USER_INPUT, RECALL_DEGRADED, INDEX_CORRUPTION, DISK_FULL, OFFLINE)

WORDS = {
    EXTERNAL_WAIT: "Waiting on someone or something outside Friday.",
    NEEDS_USER_INPUT: "Waiting for you: only you can move this on.",
    RECALL_DEGRADED: "Long-term recall is not available right now, so nothing is claimed from memory.",
    INDEX_CORRUPTION: "A search index is damaged and is being rebuilt; your files and notes are untouched.",
    DISK_FULL: "The drive with Friday's data is almost full, so background work is paused.",
    OFFLINE: "The network or the service cannot be reached right now.",
}
#: States in which nonessential background work waits.
PAUSES_BACKGROUND = frozenset({DISK_FULL})

#: Windows: ERROR_HANDLE_DISK_FULL (39), ERROR_DISK_FULL (112).
_WIN_DISK_FULL = frozenset({39, 112})
_DISK_FULL_ERRNOS = frozenset({errno.ENOSPC} | ({errno.EDQUOT} if hasattr(errno, "EDQUOT") else set()))
#: Cannot reach the other side at all. A reset or broken pipe is NOT offline (a local child
#: process that closed its pipe raises those too).
_OFFLINE_ERRNOS = frozenset(e for e in (getattr(errno, n, None) for n in (
    "ENETUNREACH", "ENETDOWN", "EHOSTUNREACH", "ECONNREFUSED", "ETIMEDOUT")) if e is not None)
#: Windows sockets: WSAENETDOWN, WSAENETUNREACH, WSAETIMEDOUT, WSAECONNREFUSED, WSAEHOSTUNREACH.
_WIN_OFFLINE = frozenset({10050, 10051, 10060, 10061, 10065})
#: Exception class names from HTTP libraries Friday may use, matched by name so this module
#: imports none of them. A retry wrapper (urllib3 MaxRetryError) is judged by its `reason`.
_OFFLINE_NAMES = frozenset({"ConnectError", "ConnectTimeout", "NewConnectionError",
                            "NameResolutionError", "ServerNotFoundError"})
_CORRUPT_WORDS = ("malformed", "not a database", "file is encrypted or is not a database",
                  "database disk image is malformed")
_SQLITE_FULL_WORDS = ("database or disk is full",)

#: Goal / plan blockers (services/goal_state.BLOCKERS) that are already one of the states.
_BLOCKERS = {EXTERNAL_WAIT: EXTERNAL_WAIT, NEEDS_USER_INPUT: NEEDS_USER_INPUT}
#: Connector health states (services/connector_health.STATES).
_HEALTH = {"needs_user": NEEDS_USER_INPUT}


def _from_exception(exc: BaseException, depth: int = 0) -> Optional[str]:
    if depth > 4 or exc is None:
        return None
    if isinstance(exc, OSError):
        win = getattr(exc, "winerror", None)
        if exc.errno in _DISK_FULL_ERRNOS or win in _WIN_DISK_FULL:
            return DISK_FULL
        if (isinstance(exc, (socket.gaierror, ConnectionRefusedError)) or exc.errno in _OFFLINE_ERRNOS
                or win in _WIN_OFFLINE):
            return OFFLINE
    if isinstance(exc, sqlite3.DatabaseError):
        text = str(exc).lower()
        if any(w in text for w in _SQLITE_FULL_WORDS):
            return DISK_FULL
        if any(w in text for w in _CORRUPT_WORDS):
            return INDEX_CORRUPTION
    if type(exc).__name__ in _OFFLINE_NAMES:
        return OFFLINE
    reason = getattr(exc, "reason", None)            # URLError / MaxRetryError(reason=...)
    if isinstance(reason, BaseException):
        found = _from_exception(reason, depth + 1)
        if found:
            return found
    nxt = exc.__cause__ if exc.__cause__ is not None else (
        None if getattr(exc, "__suppress_context__", False) else exc.__context__)
    return _from_exception(nxt, depth + 1) if nxt is not None else None


def classify(exc: Optional[BaseException] = None, *, blocker: Optional[str] = None,
             health: Optional[str] = None) -> Optional[str]:
    """The state for an exception, a goal/plan blocker or a connector health state; None when
    it is none of the six. Never raises."""
    try:
        if exc is not None:
            found = _from_exception(exc)
            if found:
                return found
        if blocker:
            found = _BLOCKERS.get(str(blocker))
            if found:
                return found
        if health:
            return _HEALTH.get(str(health))
    except Exception:
        return None
    return None


def describe(state: Optional[str]) -> Dict[str, Any]:
    """The owner's words for a state, and whether background work waits for it."""
    if state not in STATES:
        return {"state": None, "words": "", "pauses_background": False}
    return {"state": state, "words": WORDS[state], "pauses_background": state in PAUSES_BACKGROUND}

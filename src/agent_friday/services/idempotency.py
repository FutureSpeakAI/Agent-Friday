"""Client-supplied idempotency keys on Friday's own state-changing APIs: the same key twice runs once.

BD-11a / NS-31.1-4, NS-31.4-1. A caller that retries a POST (a page that lost its connection,
a script, a phone that resent) can send `Idempotency-Key: <token>`. Friday then runs the request
once:

  * the first request with a key runs, and a successful (2xx) JSON or text answer up to 1 MB is
    kept for 24 hours;
  * the same key again, for the same request (method, path and body), gets that answer back
    without running anything, marked `Idempotent-Replayed: true`;
  * the same key while the first is still running gets 409, "still running";
  * the same key for a different request gets 422: a key names one request;
  * an answer that was not a success (a refusal, a hold, an error) is not kept, so a retry runs:
    a coded "press again" refusal must be retried for real, and so must a failure.

Keys are scoped to the caller: the principal and the page's session token are part of the scope,
so one caller's key can never replay another caller's answer. A request without the header runs
as before. Keys live in memory (bounded, 2,000 entries): a restart forgets them, which costs at
most one repeat of a request made before the restart.

Only API state changes are covered (POST, PUT, PATCH, DELETE under /api/); streams are not kept
(a second request with the key gets 409, "already handled, its answer cannot be replayed").
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict

HEADER = "Idempotency-Key"
REPLAYED = "Idempotent-Replayed"
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TTL_S = 24 * 3600
MAX_ENTRIES = 2000
MAX_BODY = 1024 * 1024

_LOCK = threading.Lock()
#: scope -> {"fp", "state": "running"|"done"|"stream", "at", "status", "body", "mimetype"}
_SEEN: "OrderedDict[str, dict]" = OrderedDict()


def applies(method, path) -> bool:
    return str(method or "").upper() in _METHODS and str(path or "").startswith("/api/")


def valid_key(key) -> bool:
    return bool(_KEY.fullmatch(str(key or "").strip()))


def _scope(key, principal, token) -> str:
    who = hashlib.sha256(("%s|%s" % (principal or "", token or "")).encode("utf-8")).hexdigest()[:24]
    return "%s|%s" % (who, key)


def fingerprint(method, path, body: bytes) -> str:
    h = hashlib.sha256()
    h.update(str(method).upper().encode("utf-8") + b"\n" + str(path).encode("utf-8") + b"\n")
    h.update(body or b"")
    return h.hexdigest()


def _expire(now) -> None:
    while _SEEN:
        k, v = next(iter(_SEEN.items()))
        if now - v["at"] > TTL_S or len(_SEEN) > MAX_ENTRIES:
            _SEEN.popitem(last=False)
        else:
            break


def begin(key, principal, token, fp):
    """Start the request with this key. Returns (decision, entry_or_scope):
    ("run", scope)       run it; call finish(scope, ...) or forget(scope) afterwards
    ("replay", entry)    answer with the kept response
    ("running", None)    409: the first one is still running
    ("mismatch", None)   422: the key was used for a different request
    ("stream", None)     409: already handled, and its answer was a stream"""
    scope = _scope(str(key).strip(), principal, token)
    now = time.time()
    with _LOCK:
        _expire(now)
        e = _SEEN.get(scope)
        if e is None:
            _SEEN[scope] = {"fp": fp, "state": "running", "at": now}
            return "run", scope
        if e["fp"] != fp:
            return "mismatch", None
        if e["state"] == "running":
            return "running", None
        if e["state"] == "stream":
            return "stream", None
        return "replay", dict(e)


def finish(scope, status: int, body, mimetype: str, streamed: bool) -> None:
    """Keep a successful answer for replay; forget anything else so a retry runs."""
    with _LOCK:
        e = _SEEN.get(scope)
        if e is None or e["state"] != "running":
            return
        if not (200 <= int(status) < 300):
            _SEEN.pop(scope, None)
            return
        if streamed or body is None or len(body) > MAX_BODY:
            e.update(state="stream", at=time.time())
            return
        e.update(state="done", at=time.time(), status=int(status), body=bytes(body),
                 mimetype=mimetype or "application/json")


def forget(scope) -> None:
    with _LOCK:
        e = _SEEN.get(scope)
        if e is not None and e["state"] == "running":
            _SEEN.pop(scope, None)


def reset_for_tests() -> None:
    with _LOCK:
        _SEEN.clear()

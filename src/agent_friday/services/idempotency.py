"""Client-supplied idempotency keys on Friday's own state-changing APIs: the same key twice runs once.

BD-11a / NS-31.1-4, NS-31.4-1. A caller that retries a POST (a page that lost its connection,
a script, a phone that resent) can send `Idempotency-Key: <token>`. Friday then runs the request
once:

  * the first request with a key runs, and a successful (2xx) answer up to 256 KB is kept for
    24 hours (status, body and type only: headers the route set are not replayed; all kept
    answers together stay under 32 MB);
  * the same key again, for the same request (method, path and body), gets that answer back
    without running anything, marked `Idempotent-Replayed: true`;
  * the same key while the first is still running gets 409, "still running";
  * the same key for a different request gets 422: a key names one request;
  * an answer that was not a success (a refusal, a hold, an error) is not kept, so a retry runs:
    a coded "press again" refusal must be retried for real, and so must a failure.

Keys are scoped to the caller (core builds the scope from the principal, whether the request
comes from this PC and its address, whether it carries the page's valid session token, and its
session cookie), so one caller's key never replays another caller's answer. A replay is
answered before route-level checks run: it changes nothing, and only the same caller with the
same request can get it. A request without the header runs
as before. Answers live in memory (bounded, 2,000 entries).

A key outlives a restart (NS-31.1-4): beside the memory, a small file in Friday's folder
(idempotency_keys.json) keeps, for each key, only keyed digests (HMAC with this install's own
secret) of the caller-scoped key and of the request's fingerprint, its state and when. Never an
answer body, and nothing a guess about a request's words could be checked against without that
secret. After a restart the same key for the same request is not
run again: it gets 409 ("already handled before Friday restarted; its answer was not kept"), or,
when it was still running as Friday stopped, 409 saying it may have run. Losing the file only
returns to the old behaviour.

Only API state changes are covered (POST, PUT, PATCH, DELETE under /api/); streams are not kept
(a second request with the key gets 409, "already handled, its answer cannot be replayed").
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

HEADER = "Idempotency-Key"
REPLAYED = "Idempotent-Replayed"
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TTL_S = 24 * 3600
MAX_ENTRIES = 2000
MAX_BODY = 256 * 1024
#: All kept answers together never hold more than this; the oldest go first.
MAX_TOTAL_BYTES = 32 * 1024 * 1024

_LOCK = threading.Lock()
#: scope -> {"fp", "state": "running"|"done"|"stream", "at", "status", "body", "mimetype"}
_SEEN: "OrderedDict[str, dict]" = OrderedDict()
#: digest(scope) -> {"fp", "state", "at"}: what outlives a restart. No answer bodies.
_DISK: dict = {}
_DISK_LOADED = [False]
#: Set by tests; otherwise the runtime folder.
_PATH_OVERRIDE = None
#: Kept keys on disk, newest first, within TTL_S.
MAX_DISK_ENTRIES = 5000


def _path() -> Path:
    if _PATH_OVERRIDE is not None:
        return Path(_PATH_OVERRIDE)
    from agent_friday.paths import friday_home
    # In Friday's own folder (not the runtime folder, which can be moved elsewhere): erased with
    # everything else, and listed on the privacy map with the receipts.
    return Path(friday_home()) / "idempotency_keys.json"


_SECRET: list = []
_WRITE_LOCK = threading.Lock()


def _secret() -> bytes:
    """This install's own secret (core: the session secret, kept out of every export). Without it
    a per-process one: keys then do not outlive a restart, which is only the old behaviour."""
    if not _SECRET:
        try:
            from agent_friday.core import _load_or_create_secret
            _SECRET.append(str(_load_or_create_secret()).encode("utf-8"))
        except Exception:
            import secrets as _s
            _SECRET.append(_s.token_bytes(32))
    return _SECRET[0]


def _keyed(kind: str, value: str) -> str:
    import hmac
    return hmac.new(_secret(), ("idem|%s|%s" % (kind, value)).encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def _digest(scope: str) -> str:
    return _keyed("scope", scope)


def _load_disk(now) -> None:
    if _DISK_LOADED[0]:
        return
    _DISK_LOADED[0] = True
    path = _path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except Exception:
        # Busy or locked for a moment: try again on the next request, never overwrite what is there.
        _DISK_LOADED[0] = False
        return
    try:
        data = json.loads(raw)
        keys = data.get("keys") if isinstance(data, dict) else {}
    except Exception:
        keys = {}
        try:                              # unreadable: set aside, never silently replaced
            os.replace(path, path.with_name(path.name + ".unreadable"))
        except Exception:
            pass
    for h, v in (keys or {}).items():
        try:
            at = float(v.get("at") or 0)
            if now - at <= TTL_S:
                _DISK[str(h)] = {"fp": str(v.get("fp") or ""), "state": str(v.get("state") or "done"), "at": at}
        except Exception:
            continue


def _snapshot(now) -> str:
    """The kept keys as the file's text (called under _LOCK; the write happens outside it)."""
    for h in [h for h, v in _DISK.items() if now - v["at"] > TTL_S]:
        _DISK.pop(h, None)
    if len(_DISK) > MAX_DISK_ENTRIES:
        done = sorted((kv for kv in _DISK.items() if kv[1]["state"] != "running"), key=lambda kv: kv[1]["at"])
        for h, _v in done[:len(_DISK) - MAX_DISK_ENTRIES]:
            _DISK.pop(h, None)
    _SEQ[0] += 1
    return (_SEQ[0], json.dumps({"v": 1, "keys": _DISK}))


_SEQ = [0]
_WRITTEN = [0]


def _write(snap) -> None:
    """Write the file atomically, outside the request lock. A failure only loses what a restart
    would remember (the old behaviour). One server process owns the folder."""
    if not snap:
        return
    seq, text = snap
    with _WRITE_LOCK:
        if seq <= _WRITTEN[0]:
            return                        # a newer state was already written
        _WRITTEN[0] = seq
        try:
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            pass


def _save_disk(now):
    try:
        return _snapshot(now)
    except Exception:
        return None


def _note(scope, fp, state, now):
    _DISK[_digest(scope)] = {"fp": _keyed("fp", fp), "state": state, "at": now}
    return _save_disk(now)


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
    """Drop expired answers, then the oldest finished ones while over the entry or byte budget.
    A request still running is never dropped (its duplicate would run)."""
    for k in [k for k, v in _SEEN.items() if v["state"] != "running" and now - v["at"] > TTL_S]:
        _SEEN.pop(k, None)
    total = sum(len(v.get("body") or b"") for v in _SEEN.values())
    for k in [k for k, v in _SEEN.items() if v["state"] != "running"]:
        if len(_SEEN) <= MAX_ENTRIES and total <= MAX_TOTAL_BYTES:
            break
        total -= len(_SEEN[k].get("body") or b"")
        _SEEN.pop(k, None)


def begin(key, principal, token, fp):
    """Start the request with this key. Returns (decision, entry_or_scope):
    ("run", scope)       run it; call finish(scope, ...) or forget(scope) afterwards
    ("replay", entry)    answer with the kept response
    ("running", None)    409: the first one is still running
    ("mismatch", None)   422: the key was used for a different request
    ("stream", None)     409: already handled, and its answer was a stream
    ("handled", None)    409: already handled before a restart (its answer was not kept)
    ("restarted", None)  409: it was still running when Friday stopped, so it may have run"""
    scope = _scope(str(key).strip(), principal, token)
    now = time.time()
    text = None
    with _LOCK:
        _expire(now)
        _load_disk(now)
        e = _SEEN.get(scope)
        if e is None:
            d = _DISK.get(_digest(scope))
            if d is not None and now - d["at"] <= TTL_S:
                # Handled before a restart (or its answer has left memory): never run again.
                if d["fp"] != _keyed("fp", fp):
                    return "mismatch", None
                return ("restarted" if d["state"] == "running" else "handled"), None
            _SEEN[scope] = {"fp": fp, "state": "running", "at": now}
            text = _note(scope, fp, "running", now)
            out = ("run", scope)
        elif e["fp"] != fp:
            return "mismatch", None
        elif e["state"] == "running":
            return "running", None
        elif e["state"] == "stream":
            return "stream", None
        else:
            return "replay", dict(e)
    _write(text)
    return out


def finish(scope, status: int, body, mimetype: str, streamed: bool) -> None:
    """Keep a successful answer for replay; forget anything else so a retry runs."""
    with _LOCK:
        e = _SEEN.get(scope)
        if e is None or e["state"] != "running":
            return
        if not (200 <= int(status) < 300):
            _SEEN.pop(scope, None)
            _DISK.pop(_digest(scope), None)
            text = _save_disk(time.time())
        elif streamed or body is None or len(body) > MAX_BODY:
            e.update(state="stream", at=time.time())
            text = _note(scope, e["fp"], "stream", e["at"])
        else:
            e.update(state="done", at=time.time(), status=int(status), body=bytes(body),
                     mimetype=mimetype or "application/json")
            text = _note(scope, e["fp"], "done", e["at"])
    _write(text)


def forget(scope) -> None:
    text = None
    with _LOCK:
        e = _SEEN.get(scope)
        if e is not None and e["state"] == "running":
            _SEEN.pop(scope, None)
            _DISK.pop(_digest(scope), None)
            text = _save_disk(time.time())
    _write(text)


def erase() -> None:
    """Forget every kept key, in memory and on disk (the owner's erase)."""
    with _LOCK:
        _SEEN.clear()
        _DISK.clear()
        _DISK_LOADED[0] = True
    with _WRITE_LOCK:
        try:
            _path().unlink()
        except Exception:
            pass

def reset_for_tests() -> None:
    with _LOCK:
        _SEEN.clear()
        _DISK.clear()
        _DISK_LOADED[0] = False
        try:
            _path().unlink()
        except Exception:
            pass

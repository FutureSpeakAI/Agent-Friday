"""Where work orders and their handles are kept between runs (addendum CS-2).

Orders, their checkpoints and the handles they name must survive a restart, or a
long job loses its place and a cloud model's handles go dead for no reason. The
handle registry holds local references (paths, document ids), so the file is
ENCRYPTED with Friday's own keystore (``credential_store.protect``) and lives in
``~/.friday``. It is the same store that holds provider keys:

* there is no plaintext fallback. If nothing can encrypt, nothing is written and
  orders keep working in memory; ``status()`` says so, and a restart then loses
  them (which is the safe way to lose them);
* writes are debounced (a burst of checkpoints is one write) and atomic;
* a file that cannot be read back (a changed passphrase) is left alone, not
  deleted, and the registry starts empty.

Nothing here leaves the machine.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from agent_friday.services import handles as _handles

_log = logging.getLogger("friday.work_order_store")

FILE_NAME = "work_orders.enc"
VERSION = 1
SAVE_DELAY_S = 1.5

_LOCK = threading.RLock()
_WRITE_LOCK = threading.Lock()
_TIMER: list = [None]
_LOADED = [False]
_STATE = {"saved_at": 0.0, "error": "", "last_bytes": 0}


def path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / FILE_NAME


def _snapshot() -> dict:
    from agent_friday.services import work_orders
    return {"version": VERSION, "saved": round(time.time(), 3),
            "handles": _handles.snapshot(), "orders": work_orders.snapshot()}


def _serialise() -> bytes:
    """The snapshot as JSON bytes. A step may be changing a record as we read it;
    try again rather than save a torn copy."""
    for attempt in range(4):
        try:
            return json.dumps(_snapshot(), default=str).encode("utf-8")
        except RuntimeError:                      # "dictionary changed size during iteration"
            if attempt == 3:
                raise
            time.sleep(0.02)
    raise RuntimeError("unreachable")


def save_now() -> bool:
    """Write everything, encrypted. True when it reached disk."""
    with _LOCK:
        t = _TIMER[0]
        _TIMER[0] = None
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass
    try:
        with _WRITE_LOCK:          # one writer at a time: the timer and a checkpoint share a temp file
            data = _serialise()
            from agent_friday.services import credential_store
            method = credential_store.write_secret(path(), data)
            if method == "plaintext":             # cannot happen today; never allowed
                raise RuntimeError("refusing a plaintext work-order store")
            _STATE.update(saved_at=time.time(), error="", last_bytes=len(data))
        return True
    except Exception as e:                        # noqa: BLE001 - kept in memory, said so
        msg = "%s: %s" % (type(e).__name__, e)
        if msg != _STATE.get("error"):
            _log.warning("work orders could not be saved (%s); they stay in memory only", msg)
        _STATE["error"] = msg
        return False


def schedule_save() -> None:
    """Save soon, once, however many changes arrive in between."""
    with _LOCK:
        if _TIMER[0] is not None:
            return
        t = threading.Timer(SAVE_DELAY_S, save_now)
        t.daemon = True
        _TIMER[0] = t
    t.start()


def load() -> bool:
    """Bring saved handles and orders back. Safe to call twice; the second does nothing."""
    with _LOCK:
        if _LOADED[0]:
            return False
        _LOADED[0] = True
    p = path()
    try:
        if not p.exists():
            return False
        from agent_friday.services import credential_store, keystore
        blob = p.read_bytes()
        if not keystore.is_keystore_blob(blob):      # we only ever write the keystore's own format
            raise ValueError("not an encrypted work-order store")
        doc = json.loads(credential_store.unprotect(blob).decode("utf-8"))
    except Exception as e:                        # noqa: BLE001 - unreadable: start empty, keep the file
        _log.warning("saved work orders could not be read (%s: %s); starting empty",
                     type(e).__name__, e)
        _STATE["error"] = "unreadable store: %s" % type(e).__name__
        return False
    if not isinstance(doc, dict) or doc.get("version") != VERSION:
        return False
    from agent_friday.services import work_orders
    _handles._BOOTED[0] = True
    n = _handles.restore(doc.get("handles") or {})
    m = work_orders.restore(doc.get("orders") or {})
    _log.info("work orders: %d handle(s) and %d order(s) restored", n, m)
    return True


def status() -> dict:
    return {"path_exists": path().exists(), "saved_at": _STATE["saved_at"] or None,
            "error": _STATE["error"] or None, "bytes": _STATE["last_bytes"]}


def reset_for_tests() -> None:
    with _LOCK:
        t = _TIMER[0]
        _TIMER[0] = None
        _LOADED[0] = False
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass
    _STATE.update(saved_at=0.0, error="", last_bytes=0)


_handles.set_change_hook(schedule_save)

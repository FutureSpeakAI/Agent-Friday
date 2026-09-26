"""The command channel from Friday's server to its own desktop pages, and what
those pages report back about themselves.

The desktop page holds one EventSource on /api/desktop/events, the one-way
push the Knowledge graph and the code log already use. A tool that opens
something in the owner's UI sends a command to ONE desktop page, the one the
user is most likely looking at, and waits briefly for that page to say what it
did: the tool reports what the page confirmed, never what it merely sent.

Every page (the desktop, a workspace in its own tab, the undocked chat)
reports which windows are open, which is in front and which section each
shows. The situation snapshot reads those reports; it never asks a page. Only
the desktop holds the stream, since only it can open a workspace window.
"""
from __future__ import annotations

import itertools
import queue as _queue
import threading
import time
from typing import Any

#: A page that has not reported for this long is taken to be gone.
STALE_AFTER_S = 120.0
#: How long a sender waits for the page to say what it did. The page itself
#: gives up at five seconds (a large folder's scan, a slow thread fetch), so
#: a spoken request still answers well inside the voice bridge's limit.
ACK_TIMEOUT_S = 6.0

_LOCK = threading.Lock()
_CLIENTS: dict[str, dict] = {}          # client id -> record
_PENDING: dict[str, dict] = {}          # command id -> {"event", "ack"}
_SEQ = itertools.count(1)


def _record(client_id: str) -> dict:
    rec = _CLIENTS.get(client_id)
    if rec is None:
        rec = _CLIENTS[client_id] = {
            "id": client_id, "kind": "desktop", "queue": None,
            "connected_at": None, "state": {}, "state_at": 0.0,
            "manifest": None, "manifest_at": 0.0}
    return rec


# ── Connection ───────────────────────────────────────────────────────────────

def subscribe(client_id: str, kind: str = "desktop") -> _queue.Queue:
    """Register a page's event stream. A reconnect under the same id replaces
    the old queue, so a page never holds two."""
    q: _queue.Queue = _queue.Queue(maxsize=50)
    with _LOCK:
        rec = _record(client_id)
        rec.update(kind=kind or "desktop", queue=q, connected_at=time.time())
    return q


def unsubscribe(client_id: str, q: _queue.Queue) -> None:
    with _LOCK:
        rec = _CLIENTS.get(client_id)
        if rec is not None and rec.get("queue") is q:
            rec["queue"] = None


def report_state(client_id: str, state: dict) -> bool:
    """What a page says about itself: open windows, the focused one, sections,
    and now and then the manifest of what every workspace can be opened to.
    A page that is going away says {"closed": true} and is forgotten.

    Returns True when no manifest from this page is held, so it sends one.
    """
    state = dict(state or {})
    now = time.time()
    with _LOCK:
        if state.get("closed"):
            _CLIENTS.pop(client_id, None)
            return False
        rec = _record(client_id)
        manifest = state.pop("manifest", None)
        if isinstance(manifest, dict) and manifest:
            rec["manifest"], rec["manifest_at"] = manifest, now
        rec["state"], rec["state_at"] = state, now
        if state.get("kind") in ("desktop", "tab", "chat"):
            rec["kind"] = state["kind"]
        return not rec.get("manifest")


def _fresh(rec: dict, now: float) -> bool:
    return now - (rec.get("state_at") or 0) < STALE_AFTER_S


def _rank(rec: dict) -> tuple:
    st = rec.get("state") or {}
    return (rec.get("kind") == "desktop", bool(st.get("focused")),
            bool(st.get("visible", True)), rec.get("state_at") or 0.0)


def pick_client(now: float | None = None) -> dict | None:
    """The page a command should go to: a connected, reporting desktop page,
    the focused and visible one first, then the one that reported last."""
    now = now or time.time()
    with _LOCK:
        live = [r for r in _CLIENTS.values()
                if r.get("queue") is not None and _fresh(r, now)]
        return max(live, key=_rank) if live else None


# ── Commands ─────────────────────────────────────────────────────────────────

def send(actions: list, verify: dict | None = None,
         timeout: float = ACK_TIMEOUT_S) -> dict:
    """Push `actions` (the action-bus shapes fridayRunActions runs) to one page
    and wait up to `timeout` for it to say what happened.

    Returns {"delivered": False, "reason"} when no desktop page is connected,
    else {"delivered": True, "acked": bool, "ack": {...}, "page": kind}.
    """
    rec = pick_client()
    if rec is None:
        return {"delivered": False,
                "reason": "no Friday desktop page is open to show it in"}
    cmd_id = "c%d-%d" % (int(time.time()), next(_SEQ))
    waiter = {"event": threading.Event(), "ack": None}
    with _LOCK:
        _PENDING[cmd_id] = waiter
    try:
        rec["queue"].put_nowait({"type": "command", "id": cmd_id,
                                 "actions": actions, "verify": verify or {}})
    except (_queue.Full, AttributeError):
        with _LOCK:
            _PENDING.pop(cmd_id, None)
        return {"delivered": False, "reason": "the desktop page is not taking commands"}
    got = waiter["event"].wait(max(0.0, timeout))
    with _LOCK:
        _PENDING.pop(cmd_id, None)
    return {"delivered": True, "acked": bool(got), "ack": waiter["ack"] or {},
            "page": rec.get("kind")}


def broadcast(event: dict, kind: str = "chat") -> int:
    """Push one event to EVERY connected page of `kind`. Returns how many got it.

    `send` above is a COMMAND: it picks the single best page and waits for that
    page to say what it did. This is the other shape -- news, to whoever is
    looking. An approved action finishing is news: it can be true in three open
    tabs at once, none of them owes an answer, and the executor that publishes it
    must not be blocked waiting for a browser.

    Never raises and never blocks. A page whose queue is full is skipped rather
    than waited for: this is called from the approval executor, where the action
    has already happened and losing the notification must not undo it.
    """
    sent = 0
    with _LOCK:
        recs = [r for r in _CLIENTS.values()
                if r.get("kind") == kind and r.get("queue") is not None]
    for rec in recs:
        try:
            rec["queue"].put_nowait(dict(event))
            sent += 1
        except Exception:
            continue
    return sent


def ack(cmd_id: str, payload: dict | None) -> bool:
    """A page's report of what a command did. False for an unknown id."""
    with _LOCK:
        waiter = _PENDING.get(cmd_id)
        if waiter is None:
            return False
        waiter["ack"] = dict(payload or {})
    waiter["event"].set()
    return True


# ── What the pages said about themselves ────────────────────────────────────

def manifest() -> dict:
    """The newest workspace manifest any page reported: each workspace's label
    and the sections and keys its deep-link handler accepts."""
    with _LOCK:
        best = max((r for r in _CLIENTS.values() if r.get("manifest")),
                   key=lambda r: r.get("manifest_at") or 0, default=None)
        return dict(best["manifest"]) if best else {}


def state(now: float | None = None) -> dict:
    """The desktop as its pages last described it, for the situation snapshot."""
    now = now or time.time()
    with _LOCK:
        recs = [dict(r, state=dict(r.get("state") or {}),
                     takes_commands=r.get("queue") is not None)
                for r in _CLIENTS.values() if r.get("state_at")]
    if not recs:
        return {"known": False, "pages": 0}
    live = [r for r in recs if _fresh(r, now)]
    best = max(live or recs, key=_rank)
    st = best["state"]
    out: dict[str, Any] = {
        "known": True,
        "pages": len(live),
        "page": best.get("kind"),
        "age_s": round(now - (best.get("state_at") or now), 1),
        "commands": any(r["takes_commands"] for r in live),
        "visible": st.get("visible"),
        "open": list(st.get("open") or []),
        "focused": st.get("focused_window") or None,
        "chat": st.get("chat") or None,
    }
    tabs = [((r["state"].get("focused_window") or {}).get("label")
             or (r["state"].get("focused_window") or {}).get("workspace"))
            for r in live if r.get("kind") == "tab" and r is not best]
    if any(tabs):
        out["tabs"] = [t for t in tabs if t]
    if any(r.get("kind") == "chat" for r in live):
        out["chat_window"] = True
    return out


def reset() -> None:
    """Forget every page (tests)."""
    with _LOCK:
        _CLIENTS.clear()
        _PENDING.clear()

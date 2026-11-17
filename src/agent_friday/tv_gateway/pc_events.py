"""One server-sent-events feed for the owner's phone and tablet (phone workspaces parity PW-G8): ``GET /tv/v1/events``.

A device holds ONE stream instead of one per feature. Each event has a source name (``needs_you`` today: approval cards;
job and workspace sources publish through :func:`publish` as they are built), a number, and the device's own shaping:

* ``event: hello`` first: ``{rev, as_of, resume}``; ``resume`` is ``ok`` or ``reset`` (see below);
* ``id: <n>`` / ``event: <source>`` / ``data: <json>`` per event; ``Last-Event-ID`` resumes from the ring of the last 200
  events; when the device is further behind than the ring (or ahead of it: the PC restarted) it is sent ``reset`` and
  refetches its lists. A device is never left believing it is up to date;
* ``event: heartbeat`` every ``BEAT_S`` seconds; three missed and the device treats the PC as out of reach;
* the ``pc_read`` right is re-checked on every beat and every event; a revoked device is sent ``revoked`` and closed;
* at most ``MAX_STREAMS_PER_DEVICE`` streams per device; a device that cannot keep up gets ``reset`` rather than silence.

An approval card is cut down for this device the same way the cards list is (a private-detail card shows only its generic
title unless the device can approve). Nothing else a source publishes is passed through unshaped. Each rule is held by a
test in tests/unit/test_tv_pc_events.py.
"""
from __future__ import annotations

import collections
import json
import logging
import queue
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from flask import Flask, Response, g, request, stream_with_context

from agent_friday.services import approval_feed, device_pairing
from agent_friday.tv_gateway import pc_read
from agent_friday.tv_gateway.pc_common import allowed, refuse

_log = logging.getLogger(__name__)

BEAT_S = 20
RING = 200
QUEUE_MAX = 256
MAX_STREAMS_PER_DEVICE = 3

_LOCK = threading.Lock()
_RING: "collections.deque" = collections.deque(maxlen=RING)      # (id, source, raw)
_LAST_ID = 0
_SUBSCRIBERS: set = set()
_SHAPERS: Dict[str, Callable] = {}
_STREAMS: Dict[str, int] = {}
_PUMP: Optional[threading.Thread] = None


def register_source(name: str, shaper: Callable[[dict, dict], Optional[dict]]) -> None:
    """A source says how one of its raw events is shown to one device: ``shaper(raw, device) -> dict | None`` (None hides
    it from that device). A source with no shaper publishes nothing to devices."""
    _SHAPERS[name] = shaper


def publish(source: str, raw: dict) -> int:
    """Number and keep one event, and hand it to every connected stream. Never raises. Returns its number (0 if the source
    has no shaper, so nothing is kept for devices)."""
    global _LAST_ID
    if source not in _SHAPERS:
        return 0
    with _LOCK:
        _LAST_ID += 1
        event_id = _LAST_ID
        _RING.append((event_id, source, raw))
        subscribers = list(_SUBSCRIBERS)
    for subscriber in subscribers:
        try:
            subscriber.put_nowait((event_id, source, raw))
        except queue.Full:
            try:                                           # behind: drop what it has not read and tell it to refetch
                while True:
                    subscriber.get_nowait()
            except queue.Empty:
                pass
            try:
                subscriber.put_nowait((0, "reset", {}))
            except queue.Full:
                pass
    return event_id


def _approval_shaper(raw: dict, dev: dict) -> Optional[dict]:
    from agent_friday.tv_gateway import pc_approve_decide
    return pc_approve_decide._frame(raw, dev)


register_source("needs_you", _approval_shaper)


def _pump() -> None:
    """Forwards the desktop's approval feed into the hub, for as long as the process runs once a device has connected."""
    feed = approval_feed.subscribe()
    while True:
        try:
            raw = feed.get(timeout=60)
        except queue.Empty:
            continue
        if isinstance(raw, dict) and raw.get("type") in ("pending", "resolved", "resync"):
            publish("needs_you", raw)


def _ensure_pump() -> None:
    global _PUMP
    with _LOCK:
        if _PUMP is not None and _PUMP.is_alive():
            return
        _PUMP = threading.Thread(target=_pump, name="tv-events-pump", daemon=True)
        _PUMP.start()


def _since(after: Optional[int]) -> Tuple[str, List[tuple]]:
    """('ok', events newer than ``after``) or ('reset', []) when the device cannot be caught up from the ring."""
    with _LOCK:
        last, ring = _LAST_ID, list(_RING)
    if after is None:
        return "ok", []
    if after > last or (ring and after < ring[0][0] - 1) or (not ring and after < last):
        return "reset", []
    return "ok", [item for item in ring if item[0] > after]


def _line(event_id: Optional[int], name: str, data: dict) -> str:
    head = "id: %d\n" % event_id if event_id else ""
    return "%sevent: %s\ndata: %s\n\n" % (head, name, json.dumps(data, default=str))


def _still_allowed(device_id: str) -> Optional[dict]:
    dev = device_pairing.get_device(device_id) or {}
    return dev if (allowed(dev) and device_pairing.has_right(dev, pc_read.RIGHT)) else None


def register(app: Flask) -> None:
    @app.get("/tv/v1/events")
    def pc_events():
        denied = pc_read._guard("events")
        if denied is not None:
            return denied
        device_id = g.device["id"]
        try:
            after = int(request.headers.get("Last-Event-ID", "")) if request.headers.get("Last-Event-ID") else None
        except ValueError:
            after = -1                                      # not a number we issued: refetch everything
        with _LOCK:
            if _STREAMS.get(device_id, 0) >= MAX_STREAMS_PER_DEVICE:
                return refuse(429, "this device already holds the most event streams the PC allows", code="too_many_streams")
            _STREAMS[device_id] = _STREAMS.get(device_id, 0) + 1
        _ensure_pump()
        mine: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        with _LOCK:
            _SUBSCRIBERS.add(mine)                          # subscribe BEFORE reading the ring: a duplicate beats a gap
        resume, missed = _since(after)

        closed = []

        def cleanup():
            """Gives the device's slot back (stream_with_context starts the generator at once, so its finally always runs)."""
            with _LOCK:
                if closed:
                    return
                closed.append(True)
                _SUBSCRIBERS.discard(mine)
                _STREAMS[device_id] = max(0, _STREAMS.get(device_id, 1) - 1)

        def frames():
            try:
                yield _line(None, "hello", {"rev": _LAST_ID, "as_of": int(time.time()), "resume": resume})
                last_sent = after if (after is not None and after >= 0) else 0
                for event_id, source, raw in missed:
                    dev = _still_allowed(device_id)
                    if dev is None:
                        yield _line(None, "revoked", {})
                        return
                    shown = _SHAPERS[source](raw, dev)
                    if shown is not None:
                        yield _line(event_id, source, shown)
                    last_sent = event_id
                while True:
                    try:
                        event_id, source, raw = mine.get(timeout=BEAT_S)
                    except queue.Empty:
                        event_id, source, raw = 0, "heartbeat", {}
                    dev = _still_allowed(device_id)
                    if dev is None:
                        yield _line(None, "revoked", {})
                        return
                    if source in ("heartbeat", "reset"):
                        yield _line(None, source, {"as_of": int(time.time())})
                        continue
                    if event_id <= last_sent:               # already sent from the ring replay
                        continue
                    last_sent = event_id
                    shown = _SHAPERS[source](raw, dev)
                    if shown is not None:
                        yield _line(event_id, source, shown)
            finally:
                cleanup()

        return Response(stream_with_context(frames()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

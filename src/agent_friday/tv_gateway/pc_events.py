"""One server-sent-events feed for the owner's phone and tablet (phone workspaces parity PW-G8): ``GET /tv/v1/events``.

A device holds ONE stream instead of one per feature. Each event has a source name (``needs_you`` today: approval cards;
job and workspace sources publish through :func:`publish` as they are built), an id, and the device's own shaping:

* ``event: hello`` first: ``{boot, rev, as_of, resume}``; ``resume`` is ``ok`` or ``reset`` (see below);
* ``id: <boot>-<n>`` / ``event: <source>`` / ``data: <json>`` per event. ``boot`` names this run of the PC (a random
  token), so a number issued before a restart can never be mistaken for one of this run;
* ``Last-Event-ID`` resumes from the ring of the last 200 events. A device whose id names another run, is not an id
  this PC issued, is further behind than the ring, or is ahead of it is sent ``reset`` and refetches its lists; after
  a ``reset`` it receives every later event. A device is never left believing it is up to date;
* ``event: heartbeat`` every ``BEAT_S`` seconds; three missed and the device treats the PC as out of reach;
* the ``pc_read`` right is re-checked on every beat and every event; a revoked device is sent ``revoked`` and closed;
* at most ``MAX_STREAMS_PER_DEVICE`` streams per device: a new one ends the oldest (``superseded``) rather than being
  refused, so half-open connections of a phone that changed network never lock it out; a device that cannot keep up gets
  ``reset`` rather than silence.

An approval card is cut down for this device the same way the cards list is (a private-detail card shows only its
generic title unless the device can approve). Raw events live only in memory, in the ring. Each rule is held by a
test in tests/unit/test_tv_pc_events.py.
"""
from __future__ import annotations

import collections
import json
import logging
import queue
import secrets
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from flask import Flask, Response, g, request, stream_with_context

from agent_friday.services import approval_feed, device_pairing
from agent_friday.tv_gateway import pc_read
from agent_friday.tv_gateway.pc_common import allowed

_log = logging.getLogger(__name__)

BEAT_S = 20
RING = 200
QUEUE_MAX = 256
MAX_STREAMS_PER_DEVICE = 3

_LOCK = threading.Lock()
_BOOT = secrets.token_hex(6)
_RING: "collections.deque" = collections.deque(maxlen=RING)      # (n, source, raw)
_LAST = 0
_SUBSCRIBERS: set = set()
_SHAPERS: Dict[str, Callable] = {}
_STREAMS: Dict[str, List[queue.Queue]] = {}
_PUMP: Optional[threading.Thread] = None
_PUMP_STOP = threading.Event()


def register_source(name: str, shaper: Callable[[dict, dict], Optional[dict]]) -> None:
    """A source says how one of its raw events is shown to one device: ``shaper(raw, device) -> dict | None`` (None
    hides it from that device). A source with no shaper publishes nothing to devices."""
    _SHAPERS[name] = shaper


def _deliver(subscriber: "queue.Queue", item: tuple) -> None:
    try:
        subscriber.put_nowait(item)
    except queue.Full:
        try:                                               # behind: drop what it has not read and tell it to refetch
            while True:
                subscriber.get_nowait()
        except queue.Empty:
            pass
        try:
            subscriber.put_nowait((0, "reset", {}))
        except queue.Full:
            pass


def _evict(subscriber: "queue.Queue") -> None:
    """Ends a stream even when its queue is full (a half-open connection is blocked writing, so its queue fills): the
    queue is emptied first, so the ``superseded`` marker always gets in and the stream's thread always ends."""
    try:
        while True:
            subscriber.get_nowait()
    except queue.Empty:
        pass
    try:
        subscriber.put_nowait((0, "superseded", {}))
    except queue.Full:
        pass


def publish(source: str, raw: dict) -> int:
    """Number and keep one event, and hand it to every connected stream. Never raises. Returns its number (0 if the
    source has no shaper, so nothing is kept for devices). Numbering, the ring and delivery happen under one lock, so
    every stream sees events in number order however many sources publish at once (delivery never blocks)."""
    global _LAST
    if source not in _SHAPERS:
        return 0
    with _LOCK:
        _LAST += 1
        number = _LAST
        _RING.append((number, source, raw))
        for subscriber in list(_SUBSCRIBERS):
            _deliver(subscriber, (number, source, raw))
    return number


def _approval_shaper(raw: dict, dev: dict) -> Optional[dict]:
    from agent_friday.tv_gateway import pc_approve_decide
    return pc_approve_decide._frame(raw, dev)


register_source("needs_you", _approval_shaper)


def _pump() -> None:
    """Forwards the desktop's approval feed into the hub while the process runs (until stopped, in tests)."""
    feed = approval_feed.subscribe()
    try:
        while not _PUMP_STOP.is_set():
            try:
                raw = feed.get(timeout=1)
            except queue.Empty:
                continue
            if isinstance(raw, dict) and raw.get("type") in ("pending", "resolved", "resync"):
                publish("needs_you", raw)
    finally:
        approval_feed.unsubscribe(feed)


def _ensure_pump() -> None:
    global _PUMP
    with _LOCK:
        if _PUMP is not None and _PUMP.is_alive():
            return
        _PUMP_STOP.clear()
        _PUMP = threading.Thread(target=_pump, name="tv-events-pump", daemon=True)
        _PUMP.start()


def stop_pump() -> None:
    """Stops the forwarding thread (tests; the process normally keeps it for its whole life)."""
    global _PUMP
    _PUMP_STOP.set()
    thread, _PUMP = _PUMP, None
    if thread is not None:
        thread.join(timeout=5)


def _parse_id(header: Optional[str]) -> Tuple[Optional[int], bool]:
    """(number, readable): ``(None, True)`` for no header; ``(n, True)`` for an id of THIS run; ``(None, False)`` for
    any other id (another run, garbage), which means reset."""
    if not header:
        return None, True
    boot, _, number = header.partition("-")
    if boot != _BOOT or not (number.isascii() and number.isdecimal()) or len(number) > 15:
        return None, False
    return int(number), True


def _since(after: Optional[int], readable: bool) -> Tuple[str, List[tuple]]:
    """('ok', events newer than ``after``) or ('reset', []) when the device cannot be caught up from the ring."""
    if not readable:
        return "reset", []
    with _LOCK:
        last, ring = _LAST, list(_RING)
    if after is None:
        return "ok", []
    if after > last or (ring and after < ring[0][0] - 1) or (not ring and after < last):
        return "reset", []
    return "ok", [item for item in ring if item[0] > after]


def _line(event_id: Optional[str], name: str, data: dict) -> str:
    head = "id: %s\n" % event_id if event_id else ""
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
        after, readable = _parse_id(request.headers.get("Last-Event-ID"))
        _ensure_pump()
        mine: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        with _LOCK:
            streams = _STREAMS.setdefault(device_id, [])
            while len(streams) >= MAX_STREAMS_PER_DEVICE:
                _evict(streams.pop(0))                       # the oldest may be half-open: end it, keep the new one
            streams.append(mine)
            _SUBSCRIBERS.add(mine)                         # subscribe BEFORE reading the ring: a duplicate beats a gap
            at_subscribe = _LAST
        resume, missed = _since(after, readable)
        closed: List[bool] = []

        def cleanup():
            with _LOCK:
                if closed:
                    return
                closed.append(True)
                _SUBSCRIBERS.discard(mine)
                if mine in _STREAMS.get(device_id, []):
                    _STREAMS[device_id].remove(mine)

        def frames():
            try:
                yield _line(None, "hello", {"boot": _BOOT, "rev": at_subscribe, "as_of": int(time.time()),
                                            "resume": resume})
                # after a reset the device refetches everything up to now and then wants every later event
                last_sent = after if resume == "ok" and after is not None else at_subscribe
                for number, source, raw in missed:
                    dev = _still_allowed(device_id)
                    if dev is None:
                        yield _line(None, "revoked", {})
                        return
                    shown = _SHAPERS[source](raw, dev)
                    if shown is not None:
                        yield _line("%s-%d" % (_BOOT, number), source, shown)
                    last_sent = number
                while True:
                    try:
                        number, source, raw = mine.get(timeout=BEAT_S)
                    except queue.Empty:
                        number, source, raw = 0, "heartbeat", {}
                    dev = _still_allowed(device_id)
                    if dev is None:
                        yield _line(None, "revoked", {})
                        return
                    if source == "superseded":
                        yield _line(None, "superseded", {})
                        return
                    if source in ("heartbeat", "reset"):
                        if source == "reset":
                            # set BEFORE the frame goes out: it refetches, so what is published after this point is
                            # new to it (a duplicate is harmless)
                            last_sent = _LAST
                        yield _line(None, source, {"as_of": int(time.time())})
                        continue
                    if number <= last_sent:                # already sent from the ring replay
                        continue
                    last_sent = number
                    shown = _SHAPERS[source](raw, dev)
                    if shown is not None:
                        yield _line("%s-%d" % (_BOOT, number), source, shown)
            finally:
                cleanup()

        return Response(stream_with_context(frames()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

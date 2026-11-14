"""Pending queue for out-of-order peer events (Federation / Economy spec, protocol section;
roadmap F4b, row NS-40.2-11).

An authenticated event whose `parents` have not all arrived is parked here instead of being
acted on or dropped. Pure and in memory: nothing here opens a socket, fetches anything, runs
an effect or grants a permission. It only decides whether an event may wait, and which waiting
events became ready when another one arrived. The caller fetches missing parents from approved
sources itself and, on release, runs the whole local path again (grants, key status, time,
budget): a released event is still only a request.

  * only subscribed scopes (realm, job) may park; anything else is refused at once;
  * the queue is capped overall and per scope, and every entry expires at the event's own
    `expires_at` (a parked event is never kept past it); a full queue refuses, never evicts;
  * a cycle among parked events, a parent that is the event itself, and a parent already known
    under a different realm (a cross-realm chain) are refused;
  * release order is deterministic: by event id, never by arrival time.
"""
from __future__ import annotations

import time
from typing import Callable

from agent_friday.services.peer_wire_v2 import Verified

MAX_PENDING = 256
MAX_PER_SCOPE = 32

# refusal codes: a closed vocabulary, none of them carries peer data
UNSUBSCRIBED, EXPIRED, FULL, SCOPE_FULL, SELF_PARENT, CYCLE, CROSS_REALM, DUPLICATE = (
    "unsubscribed", "expired", "full", "scope_full", "self_parent", "cycle", "cross_realm", "duplicate")


class PendingQueue:
    def __init__(self, subscribed: Callable[[str, str], bool],
                 known_realm: Callable[[str], str | None] = lambda event_id: None,
                 now: Callable[[], float] = time.time,
                 max_pending: int = MAX_PENDING, max_per_scope: int = MAX_PER_SCOPE):
        self._subscribed = subscribed
        self._known_realm = known_realm        # event id -> realm of an event already recorded, or None
        self._now = now
        self._max, self._max_scope = max_pending, max_per_scope
        self._waiting: dict[str, Verified] = {}

    def __len__(self) -> int:
        self.purge()
        return len(self._waiting)

    def purge(self) -> int:
        """Drop every parked event past its own expiry; returns how many."""
        t = self._now()
        dead = [i for i, v in self._waiting.items() if t >= v.body["expires_at"]]
        for i in dead:
            del self._waiting[i]
        return len(dead)

    def _reaches(self, start: str, target: str) -> bool:
        """True if `target` is an ancestor of `start` through parked events only."""
        seen, todo = set(), [start]
        while todo:
            cur = todo.pop()
            if cur == target:
                return True
            if cur in seen or cur not in self._waiting:
                continue
            seen.add(cur)
            todo.extend(self._waiting[cur].body["parents"])
        return False

    def park(self, v: Verified) -> tuple[bool, str]:
        """(True, "parked") or (False, code). Never raises on peer data."""
        b = v.body
        if v.event_id in self._waiting:
            return False, DUPLICATE
        if not self._subscribed(b["realm"], b["job"]):
            return False, UNSUBSCRIBED
        if self._now() >= b["expires_at"]:
            return False, EXPIRED
        self.purge()
        if v.event_id in b["parents"]:
            return False, SELF_PARENT
        for p in b["parents"]:
            known = self._known_realm(p)
            if known is not None and known != b["realm"]:
                return False, CROSS_REALM
            if p in self._waiting and self._waiting[p].body["realm"] != b["realm"]:
                return False, CROSS_REALM
        if any(self._reaches(p, v.event_id) for p in b["parents"]):
            return False, CYCLE
        if len(self._waiting) >= self._max:
            return False, FULL
        if sum(1 for w in self._waiting.values() if (w.body["realm"], w.body["job"]) == (b["realm"], b["job"])) >= self._max_scope:
            return False, SCOPE_FULL
        self._waiting[v.event_id] = v
        return True, "parked"

    def arrived(self, event_id: str | None, have: Callable[[str], bool]) -> list[Verified]:
        """An event `event_id` was just recorded (or None: re-check everything now). `have(id)` says whether an event is recorded.
        Returns the parked events whose parents are now all recorded, removed from the queue, in
        event-id order, and repeats for the events those release in turn. The caller re-evaluates
        each one in full before anything is acted on."""
        released: list[Verified] = []
        recorded = {event_id} if event_id else set()
        progress = True
        while progress:
            progress = False
            self.purge()
            for i in sorted(self._waiting):
                v = self._waiting[i]
                if all(p in recorded or have(p) for p in v.body["parents"]):
                    del self._waiting[i]
                    released.append(v)
                    recorded.add(i)
                    progress = True
                    break
        return released

    def waiting_on(self) -> set[str]:
        """The parent ids parked events are still missing: what the caller may ask approved sources for."""
        self.purge()
        need = {p for v in self._waiting.values() for p in v.body["parents"]}
        return {p for p in need if p not in self._waiting}

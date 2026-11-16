"""Pending queue for out-of-order peer events (Federation / Economy spec, protocol section;
roadmap F4b, row NS-40.2-11).

An authenticated event whose parents have not all arrived waits here. The queue decides two
things only: whether an event may wait, and which waiting events are ready once another one
is recorded. The caller fetches missing parents itself and runs the whole local path again on
every released event (grants, key status, time, budget): a released event is still a request.

Each rule names the test that fails when the rule is broken.

  Pure and in memory: standard library and the wire types only.
      test_the_module_imports_nothing_that_can_reach_out
  Only subscribed scopes (realm, job) may wait.
      test_only_subscribed_scopes_may_park
  Capped overall and per scope; a full queue refuses and never evicts.
      test_a_refused_event_never_changes_the_queue
  No event waits past its own `expires_at`.
      test_nothing_is_released_at_or_after_its_expiry
  No self-parent, no cycle, no cross-realm chain.
      test_a_cycle_a_self_parent_and_a_duplicate_are_refused
  Refusals are a closed vocabulary that carries no peer data.
      test_refusal_codes_are_a_closed_vocabulary_that_carries_no_peer_data
  Release order depends on event ids, never on arrival order.
      test_release_order_is_the_same_for_every_arrival_order
  An event is released once, after all of its parents.
      test_every_release_follows_all_of_its_parents
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from agent_friday.services.peer_wire_v2 import Verified

MAX_PENDING = 256
MAX_PER_SCOPE = 32
PARKED = "parked"


class Refusal(str, Enum):
    UNSUBSCRIBED = "unsubscribed"
    EXPIRED = "expired"
    FULL = "full"
    SCOPE_FULL = "scope_full"
    SELF_PARENT = "self_parent"
    CYCLE = "cycle"
    CROSS_REALM = "cross_realm"
    DUPLICATE = "duplicate"


UNSUBSCRIBED = Refusal.UNSUBSCRIBED.value
EXPIRED = Refusal.EXPIRED.value
FULL = Refusal.FULL.value
SCOPE_FULL = Refusal.SCOPE_FULL.value
SELF_PARENT = Refusal.SELF_PARENT.value
CYCLE = Refusal.CYCLE.value
CROSS_REALM = Refusal.CROSS_REALM.value
DUPLICATE = Refusal.DUPLICATE.value

Scope = tuple[str, str]


@dataclass(frozen=True)
class _Waiting:
    """One parked event, with the four body fields the queue reads taken out once."""

    event: Verified
    scope: Scope
    parents: frozenset[str]
    expires_at: float

    @classmethod
    def of(cls, event: Verified) -> _Waiting:
        body = event.body
        scope = (body["realm"], body["job"])
        return cls(event, scope, frozenset(body["parents"]), body["expires_at"])

    @property
    def realm(self) -> str:
        return self.scope[0]


class PendingQueue:
    def __init__(self, subscribed: Callable[[str, str], bool],
                 known_realm: Callable[[str], str | None] = lambda event_id: None,
                 now: Callable[[], float] = time.time,
                 max_pending: int = MAX_PENDING, max_per_scope: int = MAX_PER_SCOPE):
        self._subscribed = subscribed
        self._known_realm = known_realm
        self._now = now
        self._max_pending = max_pending
        self._max_per_scope = max_per_scope
        self._waiting: dict[str, _Waiting] = {}

    def __len__(self) -> int:
        self.purge()
        return len(self._waiting)

    def purge(self) -> int:
        """Drop every parked event past its own expiry; returns how many."""
        now = self._now()
        expired = [event_id for event_id, entry in self._waiting.items()
                   if now >= entry.expires_at]
        for event_id in expired:
            del self._waiting[event_id]
        return len(expired)

    def park(self, event: Verified) -> tuple[bool, str]:
        """(True, "parked") or (False, refusal code)."""
        entry = _Waiting.of(event)
        refusal = self._refused_on_sight(event.event_id, entry)
        if refusal is None:
            self.purge()
            refusal = (self._refused_by_ancestry(event.event_id, entry)
                       or self._refused_by_capacity(entry))
        if refusal is not None:
            return False, refusal.value
        self._waiting[event.event_id] = entry
        return True, PARKED

    def arrived(self, event_id: str | None, have: Callable[[str], bool]) -> list[Verified]:
        """Release what became ready now that `event_id` is recorded (None: re-check everything).

        `have(id)` says whether an event is recorded. A released event counts as arrived for the
        events waiting on it, so a whole chain can leave in one call, lowest ready id first.
        """
        arrived = {event_id} if event_id else set()
        released: list[Verified] = []
        while True:
            self.purge()
            ready = min(
                (waiting_id for waiting_id, entry in self._waiting.items()
                 if all(parent in arrived or have(parent) for parent in entry.parents)),
                default=None)
            if ready is None:
                return released
            released.append(self._waiting.pop(ready).event)
            arrived.add(ready)

    def waiting_on(self) -> set[str]:
        """The parent ids no parked event supplies: what the caller may ask approved sources for."""
        self.purge()
        wanted = {parent for entry in self._waiting.values() for parent in entry.parents}
        return wanted - self._waiting.keys()

    def _refused_on_sight(self, event_id: str, entry: _Waiting) -> Refusal | None:
        if event_id in self._waiting:
            return Refusal.DUPLICATE
        if not self._subscribed(*entry.scope):
            return Refusal.UNSUBSCRIBED
        if self._now() >= entry.expires_at:
            return Refusal.EXPIRED
        return None

    def _refused_by_ancestry(self, event_id: str, entry: _Waiting) -> Refusal | None:
        if event_id in entry.parents:
            return Refusal.SELF_PARENT
        if any(self._realms_of(parent) - {entry.realm} for parent in entry.parents):
            return Refusal.CROSS_REALM
        if any(self._descends_from(parent, event_id) for parent in entry.parents):
            return Refusal.CYCLE
        return None

    def _refused_by_capacity(self, entry: _Waiting) -> Refusal | None:
        if len(self._waiting) >= self._max_pending:
            return Refusal.FULL
        in_scope = sum(1 for other in self._waiting.values() if other.scope == entry.scope)
        if in_scope >= self._max_per_scope:
            return Refusal.SCOPE_FULL
        return None

    def _realms_of(self, event_id: str) -> set[str]:
        """Every realm an event is already known under, recorded or parked."""
        recorded = self._known_realm(event_id)
        parked = self._waiting.get(event_id)
        realms = {recorded, parked.realm if parked else None}
        return realms - {None}

    def _descends_from(self, start: str, ancestor: str) -> bool:
        """True if `ancestor` is reachable from `start` through parked events only."""
        seen: set[str] = set()
        frontier = [start]
        while frontier:
            current = frontier.pop()
            if current == ancestor:
                return True
            if current in seen or current not in self._waiting:
                continue
            seen.add(current)
            frontier.extend(self._waiting[current].parents)
        return False

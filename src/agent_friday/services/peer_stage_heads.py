"""Stage head recorder for federation peer jobs (roadmap section 40, NS-40.2-12).

A stage has a chain of heads. A signed transition names the head it builds on (`prior`) and the
head it makes (`new`). The recorder decides between concurrent transitions by compare-and-swap
on `prior` alone: never by timestamp, arrival order or digest size. Two different signed heads
built on the same prior are both kept as evidence and the stage freezes until an independent
reviewer reconciles it.

Pure and in memory; it reads no clock, no network and no file.

Rules, each with the test that enforces it (tests/unit/test_peer_stage_heads.py):
  a transition on the current head advances it:
      test_a_transition_on_the_current_head_advances_it
  a transition on a stale head is refused:
      test_a_transition_on_a_stale_head_is_refused
  the same signed transition twice is a no-op:
      test_the_same_transition_twice_is_a_no_op
  two heads on one prior freeze the stage and keep both:
      test_two_heads_on_one_prior_freeze_the_stage_and_keep_both
  the outcome never depends on arrival order:
      test_every_arrival_order_of_small_cases_gives_the_same_state
  nothing advances a frozen stage:
      test_nothing_advances_a_frozen_stage
  only an independent reviewer reconciles, to a kept head:
      test_only_an_independent_reviewer_reconciles_to_a_kept_head
  imports only the standard library:
      test_the_module_imports_only_the_standard_library
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

GENESIS = ""


class Outcome(str, Enum):
    ADVANCED = "advanced"
    DUPLICATE = "duplicate"
    STALE = "stale"
    FROZEN = "frozen"
    CONFLICT = "conflict"


class ReconcileRefusal(str, Enum):
    NOT_FROZEN = "not_frozen"
    NOT_INDEPENDENT = "not_independent"
    NOT_A_KEPT_HEAD = "not_a_kept_head"


@dataclass(frozen=True)
class Transition:
    prior: str
    new: str


class StageHeads:
    """One stage's chain. `parties` hold a stake in it; a reviewer must be outside them."""

    def __init__(self, parties: frozenset[str] = frozenset()):
        self._parties = parties
        self._head = GENESIS
        self._seen: set[Transition] = set()
        self._children: dict[str, set[str]] = {}
        self._frozen = False

    @property
    def head(self) -> str:
        return self._head

    @property
    def frozen(self) -> bool:
        return self._frozen

    def kept_heads(self) -> frozenset[str]:
        """The competing heads held as evidence while frozen; empty otherwise."""
        if not self._frozen:
            return frozenset()
        return frozenset(h for kids in self._children.values() if len(kids) > 1 for h in kids)

    def apply(self, transition: Transition) -> Outcome:
        if transition in self._seen:
            return Outcome.DUPLICATE
        if self._frozen:
            return Outcome.FROZEN
        siblings = self._children.get(transition.prior, set())
        if siblings:
            self._seen.add(transition)
            siblings.add(transition.new)
            self._frozen = True
            return Outcome.CONFLICT
        if transition.prior != self._head:
            return Outcome.STALE
        self._seen.add(transition)
        self._children[transition.prior] = {transition.new}
        self._head = transition.new
        return Outcome.ADVANCED

    def reconcile(self, reviewer: str, chosen: str) -> ReconcileRefusal | None:
        """None when reconciled; otherwise the refusal."""
        if not self._frozen:
            return ReconcileRefusal.NOT_FROZEN
        if reviewer in self._parties:
            return ReconcileRefusal.NOT_INDEPENDENT
        if chosen not in self.kept_heads():
            return ReconcileRefusal.NOT_A_KEPT_HEAD
        self._head = chosen
        self._frozen = False
        self._children = {t.prior: {t.new} for t in self._seen if t.new == chosen}
        return None

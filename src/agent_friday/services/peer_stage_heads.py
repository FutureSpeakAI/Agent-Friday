"""Stage head recorder for federation peer jobs (roadmap section 40, NS-40.2-12).

A stage has a chain of heads. A signed transition names the head it builds on (`prior`) and the
head it makes (`new`). The recorder decides between concurrent transitions by compare-and-swap
on `prior` alone: never by timestamp, arrival order or digest size. Two different signed heads
built on the same prior are both kept as evidence and the stage freezes until an independent
reviewer reconciles it.

The state is a function of the set of signed transitions received and the reconciles made, never
of the order they arrived in. Every transition is kept. The head is found by walking from genesis
while each head has exactly one successor, or the successor a reviewer chose. A head with two or
more successors and no reconcile covering exactly those is a fork: the stage is frozen, `head` is
the last agreed head and `kept_heads` are the competing successors. A transition whose prior is not
on the chain changes nothing now (STALE) but is kept, so it counts if its prior later joins the chain.

Pure and in memory; it reads no clock, no network and no file.

Rules, each with the test that enforces it (tests/unit/test_peer_stage_heads.py):
  a transition on the current head advances it:
      test_a_transition_on_the_current_head_advances_it
  a transition whose prior is not on the chain leaves the head where it is:
      test_a_transition_on_a_stale_head_is_refused
  the same signed transition twice is a no-op:
      test_the_same_transition_twice_is_a_no_op
  two heads on one prior freeze the stage and keep both:
      test_two_heads_on_one_prior_freeze_the_stage_and_keep_both
  the outcome never depends on arrival order:
      test_every_arrival_order_of_small_cases_gives_the_same_state
      test_every_arrival_order_of_chains_and_forks_gives_the_same_state
  nothing advances a frozen stage:
      test_nothing_advances_a_frozen_stage
  only an independent reviewer reconciles, to a kept head:
      test_only_an_independent_reviewer_reconciles_to_a_kept_head
  a reconcile keeps the work already built on the chosen head:
      test_a_reconcile_keeps_the_work_built_on_the_chosen_head
  two heads on one prior freeze the stage whether or not a reconcile happened before:
      test_a_second_head_on_an_old_prior_freezes_even_after_a_reconcile
  a stage names its parties (there is no default that would make every reviewer independent):
      test_a_stage_must_name_its_parties
  a transition makes a new head that is neither its prior nor genesis:
      test_a_transition_cannot_loop_or_make_genesis
  a cycle through two priors freezes the stage:
      test_a_cycle_through_two_priors_freezes_instead_of_looping
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

    def __post_init__(self):
        if self.new == GENESIS or self.new == self.prior:
            raise ValueError("a transition makes a new head that is neither its prior nor genesis")


class StageHeads:
    """One stage's chain. `parties` hold a stake in it; a reviewer must be outside them."""

    def __init__(self, parties: frozenset[str]):
        parties = frozenset(parties)
        if not parties:
            raise ValueError("a stage names the parties a reviewer must be independent of")
        self._parties = parties
        self._seen: set[Transition] = set()
        self._children: dict[str, set[str]] = {}
        self._resolved: dict[str, tuple[str, frozenset[str]]] = {}  # fork head -> (chosen, the heads it was chosen from)
        self._walk()

    def _walk(self) -> None:
        node, visited = GENESIS, {GENESIS}
        while True:
            kids = self._children.get(node, set())
            if not kids:
                self._head, self._frozen, self._kept = node, False, frozenset()
                return
            if len(kids) == 1:
                (nxt,) = kids
            else:
                choice = self._resolved.get(node)
                if choice is None or choice[1] != kids:
                    break
                nxt = choice[0]
            if nxt in visited:
                break
            visited.add(nxt)
            node = nxt
        self._head, self._frozen, self._kept = node, True, frozenset(kids)

    @property
    def head(self) -> str:
        """The current head; while frozen, the last head everyone agrees on."""
        return self._head

    @property
    def frozen(self) -> bool:
        return self._frozen

    def kept_heads(self) -> frozenset[str]:
        """The competing heads held as evidence while frozen; empty otherwise."""
        return self._kept

    def apply(self, transition: Transition) -> Outcome:
        if transition in self._seen:
            return Outcome.DUPLICATE
        was_frozen, was_head = self._frozen, self._head
        self._seen.add(transition)
        self._children.setdefault(transition.prior, set()).add(transition.new)
        self._walk()
        if was_frozen:
            return Outcome.FROZEN
        if self._frozen:
            return Outcome.CONFLICT
        return Outcome.ADVANCED if self._head != was_head else Outcome.STALE

    def reconcile(self, reviewer: str, chosen: str) -> ReconcileRefusal | None:
        """None when reconciled; otherwise the refusal. The choice covers exactly the competing heads seen now: a
        further head on the same prior freezes the stage again, and so does a later fork on the chosen chain."""
        if not self._frozen:
            return ReconcileRefusal.NOT_FROZEN
        if reviewer in self._parties:
            return ReconcileRefusal.NOT_INDEPENDENT
        if chosen not in self._kept:
            return ReconcileRefusal.NOT_A_KEPT_HEAD
        self._resolved[self._head] = (chosen, self._kept)
        self._walk()
        return None

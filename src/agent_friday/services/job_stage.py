"""The paid-stage lifecycle of a peer job as a pure state machine (NS-40.3-6, NS-40.3-9, NS-40.3-10,
NS-40.3-5; spec: Event/state rules, Recording and recovery, Detached policy document). TEST CREDITS
ONLY. It moves no credit and sends nothing: it says whether a signed event is allowed to move the stage,
and what the stage then is. The issuer's journal stays the only thing that commits balances.

  offered -> agreed -> (paid: reserving -> funded) -> authorized -> working -> delivered -> in_review
  -> earned -> closed; offered -> declined; working -> stopped -> (fresh authorization) -> authorized;
  delivered/in_review -> disputed -> earned; earned -> appealed -> earned; authorized/stopped -> cancelled.

Rules and the tests that hold them (tests/unit/test_job_stage.py):
- Each event is accepted only from its authorized actor and from the states that table allows; the test
  walks EVERY (state, event, actor) and compares with an independently written table:
  test_every_state_event_and_actor_is_allowed_only_where_the_spec_table_says.
- A worker starts only after job.authorize and its own runtime grant, never from acceptance or funding alone:
  test_work_starts_only_after_authorization_and_the_providers_own_grant.
- A cancelled authorization releases the unused hold once and never debits: test_a_cancelled_authorization_releases_the_hold_once_and_debits_nothing.
- A resume reserves nothing new: test_a_resume_after_a_stop_needs_a_fresh_authorization_and_no_second_reservation.
- A clock running out never earns, resolves or pays anything: test_no_clock_ever_earns_resolves_or_pays.
- A late delivery pauses the challenged amount instead of resolving it: test_a_late_delivery_pauses_and_never_resolves.
- An allowed amount can never exceed the reserved gross: test_an_allowed_amount_never_exceeds_the_reserved_gross.
- A paid stage needs the signed policy; there is no default: test_a_paid_stage_has_no_default_policy.
- The recorder's sequence starts at 1, rises by 1 and names the previous record: test_stage_records_are_monotonic_and_only_for_allowed_transitions.
- The same event id is applied once: test_the_same_event_id_is_applied_once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent_friday.services.stage_policy import Approval, StagePolicy, TimeoutAction


class Stage(str, Enum):
    NONE = "none"
    OFFERED = "offered"
    DECLINED = "declined"
    AGREED = "agreed"
    RESERVING = "reserving"
    FUNDED = "funded"
    AUTHORIZED = "authorized"
    WORKING = "working"
    STOPPED = "stopped"
    DELIVERED = "delivered"
    IN_REVIEW = "in_review"
    DISPUTED = "disputed"
    APPEALED = "appealed"
    EARNED = "earned"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class Role(str, Enum):
    REQUESTER = "requester"
    PROVIDER = "provider"
    SETTLEMENT = "settlement"
    REVIEWER = "reviewer"
    APPEAL = "appeal"
    RECORDER = "recorder"


class Kind(str, Enum):
    OFFER = "job.offer"
    ACCEPT = "job.accept"
    DECLINE = "job.decline"
    RESERVE = "fund.reserve"
    CONFIRM = "fund.confirm"
    AUTHORIZE = "job.authorize"
    CANCEL = "authorization.cancel"
    START = "work.start"
    STOP = "work.stop"
    CANDIDATE = "work.candidate"
    RELEASE = "output.release"
    ACCEPT_REVIEW = "review.accept"
    DISPUTE = "review.dispute"
    DECIDE = "review.decide"
    APPEAL = "review.appeal"
    FINAL = "review.final"
    RECORD = "stage.record"


class Outcome(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    DUPLICATE_RESERVATION = "duplicate-reservation"
    WRONG_ACTOR = "wrong-actor"
    NOT_ALLOWED = "not-allowed"
    BAD_PARENTS = "bad-parents"
    DENIED = "denied"


R, S, K = Role, Stage, Kind
#: kind -> (authorized actors, states it may be applied from, the stage it leads to)
TABLE = {
    K.OFFER: ({R.REQUESTER}, {S.NONE}, S.OFFERED),
    K.ACCEPT: ({R.PROVIDER}, {S.OFFERED}, S.AGREED),
    K.DECLINE: ({R.PROVIDER}, {S.OFFERED}, S.DECLINED),
    K.RESERVE: ({R.REQUESTER}, {S.AGREED}, S.RESERVING),
    K.CONFIRM: ({R.SETTLEMENT}, {S.RESERVING}, S.FUNDED),
    K.AUTHORIZE: ({R.REQUESTER}, {S.AGREED, S.FUNDED, S.STOPPED}, S.AUTHORIZED),
    K.CANCEL: ({R.REQUESTER}, {S.AUTHORIZED, S.STOPPED}, S.CANCELLED),
    K.START: ({R.PROVIDER}, {S.AUTHORIZED}, S.WORKING),
    K.STOP: ({R.REQUESTER, R.PROVIDER}, {S.WORKING}, S.STOPPED),
    K.CANDIDATE: ({R.PROVIDER}, {S.WORKING}, S.WORKING),
    K.RELEASE: ({R.PROVIDER}, {S.WORKING}, S.DELIVERED),
    K.ACCEPT_REVIEW: ({R.REQUESTER}, {S.DELIVERED, S.IN_REVIEW}, S.EARNED),
    K.DISPUTE: ({R.REQUESTER}, {S.DELIVERED, S.IN_REVIEW}, S.DISPUTED),
    K.DECIDE: ({R.REVIEWER}, {S.DISPUTED, S.DELIVERED, S.IN_REVIEW}, S.EARNED),
    K.APPEAL: ({R.REQUESTER, R.PROVIDER}, {S.EARNED}, S.APPEALED),
    K.FINAL: ({R.APPEAL}, {S.APPEALED}, S.EARNED),
    K.RECORD: ({R.RECORDER}, set(Stage), None),
}
#: the only moves the recorder may record
RECORDABLE = {(S.DELIVERED, S.IN_REVIEW), (S.EARNED, S.CLOSED)}


@dataclass(frozen=True)
class StageEvent:
    kind: Kind
    actor: Role
    event_id: str
    parents: tuple = ()
    at: int = 0
    amount: int | None = None
    grant_ok: bool | None = None       # the provider's own runtime grant, or the payer's, as the local path found it
    release_ok: bool | None = None     # version, rights and recipient verified for an output release
    to: Stage | None = None            # stage.record only
    seq: int | None = None             # stage.record only
    previous: str | None = None        # stage.record only


@dataclass
class JobStage:
    paid: bool
    gross_units: int
    policy: StagePolicy | None = None
    stage: Stage = Stage.NONE
    applied: dict = field(default_factory=dict)
    candidates: int = 0
    reservation_count: int = 0
    reserved_units: int = 0
    released_hold_units: int = 0
    earned_units: int = 0
    frozen_units: int = 0
    paused: bool = False
    decided: bool = False
    stage_records: list = field(default_factory=list)
    last_stop: str | None = None

    def __post_init__(self):
        if type(self.paid) is not bool or type(self.gross_units) is not int or self.gross_units < 0:
            raise ValueError("a stage is paid or not, with a whole gross")
        if self.paid and self.gross_units == 0:
            raise ValueError("a paid stage has a gross above zero; a zero-gross stage is voluntary")

    @property
    def needs_funding(self) -> bool:
        return self.paid and self.gross_units > 0

    def apply(self, event: StageEvent) -> Outcome:
        if event.event_id in self.applied:
            return Outcome.DUPLICATE
        actors, sources, target = TABLE[event.kind]
        if event.actor not in actors:
            return Outcome.WRONG_ACTOR
        if self.stage not in sources:
            return Outcome.DUPLICATE_RESERVATION if (event.kind is Kind.RESERVE and self.reservation_count) \
                else Outcome.NOT_ALLOWED
        refusal = self._check(event)
        if refusal is not None:
            return refusal
        self._effect(event, target)
        self.applied[event.event_id] = event.kind
        return Outcome.APPLIED

    def _parent_of(self, kind: Kind):
        """The most recent applied event of this kind (a resume has a second authorization)."""
        return next((i for i, k in reversed(list(self.applied.items())) if k is kind), None)

    def _check(self, event: StageEvent):
        kind = event.kind
        if kind is Kind.RESERVE and (not self.needs_funding or self._parent_of(Kind.ACCEPT) not in event.parents):
            return Outcome.NOT_ALLOWED if not self.needs_funding else Outcome.BAD_PARENTS
        if kind is Kind.CONFIRM and self._parent_of(Kind.RESERVE) not in event.parents:
            return Outcome.BAD_PARENTS
        if kind is Kind.AUTHORIZE:
            if self.needs_funding and self.stage is Stage.AGREED:
                return Outcome.NOT_ALLOWED                       # a paid stage is funded before it is authorized
            if self.paid and self.policy is None:
                return Outcome.NOT_ALLOWED                       # no implicit policy
            wanted = [self._parent_of(Kind.ACCEPT)] + ([self._parent_of(Kind.CONFIRM)] if self.needs_funding else [])
            if self.stage is Stage.STOPPED:
                wanted.append(self.last_stop)
            if any(w is None or w not in event.parents for w in wanted):
                return Outcome.BAD_PARENTS
            if event.grant_ok is not True:
                return Outcome.DENIED                            # a missing or expired grant is a denial
        if kind is Kind.START:
            if self._parent_of(Kind.AUTHORIZE) not in event.parents:
                return Outcome.BAD_PARENTS
            if event.grant_ok is not True:
                return Outcome.DENIED                            # the provider's own grant must pass
        if kind is Kind.RELEASE and (self.candidates < 1 or event.release_ok is not True):
            return Outcome.DENIED
        if kind in (Kind.ACCEPT_REVIEW, Kind.DECIDE, Kind.FINAL, Kind.DISPUTE):
            if kind is Kind.ACCEPT_REVIEW and self.policy and self.policy.approval is Approval.INDEPENDENT_REVIEWER:
                return Outcome.NOT_ALLOWED                       # only the independent reviewer determines a payable milestone
            if kind is Kind.DECIDE and self.stage is not Stage.DISPUTED and not (
                    self.policy and self.policy.approval is Approval.INDEPENDENT_REVIEWER):
                return Outcome.NOT_ALLOWED
            amount = self.gross_units if event.amount is None else event.amount
            if type(amount) is not int or not 0 <= amount <= self.gross_units:
                return Outcome.DENIED                            # never more than the reserved gross
        if kind is Kind.APPEAL:
            if not self.decided or (self.policy and event.at > self.policy.appeal_deadline):
                return Outcome.NOT_ALLOWED
        if kind is Kind.RECORD:
            moved = (self.stage, event.to)
            if moved not in RECORDABLE:
                return Outcome.NOT_ALLOWED
            expected_previous = self.stage_records[-1][1] if self.stage_records else None
            if event.seq != len(self.stage_records) + 1 or event.previous != expected_previous:
                return Outcome.BAD_PARENTS
        return None

    def _effect(self, event: StageEvent, target):
        kind = event.kind
        if kind is Kind.RESERVE:
            self.reservation_count += 1
        elif kind is Kind.CONFIRM:
            self.reserved_units = self.gross_units
        elif kind is Kind.CANCEL:
            self.released_hold_units = self.reserved_units - self.earned_units
            self.reserved_units = 0
        elif kind is Kind.STOP:
            self.last_stop = event.event_id
        elif kind is Kind.CANDIDATE:
            self.candidates += 1
        elif kind is Kind.RELEASE:
            self.paused = bool(self.policy and event.at > self.policy.delivery_deadline)
        elif kind in (Kind.ACCEPT_REVIEW, Kind.DECIDE, Kind.FINAL):
            self.earned_units = self.gross_units if event.amount is None else event.amount
            self.frozen_units = 0
            self.decided = kind is Kind.DECIDE          # a final appeal decision ends the matter
        elif kind is Kind.DISPUTE:
            self.frozen_units = self.gross_units if event.amount is None else event.amount
        elif kind is Kind.APPEAL:
            self.frozen_units = self.earned_units
        elif kind is Kind.RECORD:
            self.stage_records.append((event.seq, event.event_id))
            self.stage = event.to
            return
        if target is not None:
            self.stage = target

    def tick(self, now: int) -> Outcome:
        """A clock passing. It may pause, refer or close; it never earns, resolves or pays (NS-40.3-10)."""
        policy = self.policy
        if policy is None or self.stage not in (Stage.DELIVERED, Stage.IN_REVIEW, Stage.DISPUTED, Stage.APPEALED):
            return Outcome.NOT_ALLOWED
        past = now > (policy.appeal_deadline if self.stage is Stage.APPEALED else policy.review_deadline)
        if not past:
            return Outcome.NOT_ALLOWED
        self.frozen_units = max(self.frozen_units, self.gross_units)      # the challenged amount is paused
        if self.stage in (Stage.DELIVERED, Stage.IN_REVIEW):
            if policy.timeout_action is TimeoutAction.REFER_TO_REVIEWER:
                self.stage = Stage.DISPUTED
            elif policy.timeout_action is TimeoutAction.CLOSE_WITHOUT_NEXT_STAGE:
                self.stage = Stage.CLOSED                                  # closed, and nothing was earned
        return Outcome.APPLIED

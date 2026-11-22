"""The paid-stage lifecycle of a peer job (NS-40.3-6, -9, -10, -5; spec Event/state rules). Pure; test credits only."""
import copy
import itertools
import json
from pathlib import Path

import pytest

from agent_friday.services import job_stage as js
from agent_friday.services import stage_policy as sp

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "wire-v2.json").read_text(encoding="utf-8"))["stage_policy"]


def policy(**over):
    d = copy.deepcopy(FIXTURE)
    d.update(over)
    return sp.parse(d)


POLICY = policy()
INDEPENDENT = policy(approval="independent_reviewer")
K, R, S, O = js.Kind, js.Role, js.Stage, js.Outcome
DELIVERY, REVIEW, APPEAL = POLICY.delivery_deadline, POLICY.review_deadline, POLICY.appeal_deadline
GROSS = 100


class Driver:
    """Applies events with fresh ids and remembers the ids by kind, so a test can name predecessors."""

    def __init__(self, pol=POLICY, paid=True, gross=GROSS):
        self.m = js.JobStage(paid, gross, pol)
        self.last = {}
        self.count = 0

    def event(self, kind, actor, parents=(), **kw):
        self.count += 1
        return js.StageEvent(kind, actor, f"e{self.count}", tuple(parents), **kw)

    def do(self, kind, actor, parents=(), expect=O.APPLIED, **kw):
        e = self.event(kind, actor, parents, **kw)
        outcome = self.m.apply(e)
        assert outcome is expect, (kind, actor, outcome)
        if outcome is O.APPLIED:
            self.last[kind] = e.event_id
        return e.event_id


def offered(d):
    d.do(K.OFFER, R.REQUESTER)


def agreed(d):
    offered(d)
    d.do(K.ACCEPT, R.PROVIDER, (d.last[K.OFFER],))


def reserving(d):
    agreed(d)
    d.do(K.RESERVE, R.REQUESTER, (d.last[K.ACCEPT],))


def funded(d):
    reserving(d)
    d.do(K.CONFIRM, R.SETTLEMENT, (d.last[K.RESERVE],))


def authorized(d):
    funded(d)
    d.do(K.AUTHORIZE, R.REQUESTER, (d.last[K.ACCEPT], d.last[K.CONFIRM]), grant_ok=True)


def working(d):
    authorized(d)
    d.do(K.START, R.PROVIDER, (d.last[K.AUTHORIZE],), grant_ok=True)


def candidate(d):
    working(d)
    d.do(K.CANDIDATE, R.PROVIDER)


def stopped(d):
    working(d)
    d.do(K.STOP, R.PROVIDER)


def delivered(d):
    working(d)
    d.do(K.CANDIDATE, R.PROVIDER)
    d.do(K.RELEASE, R.PROVIDER, release_ok=True, at=DELIVERY - 1)


def in_review(d):
    delivered(d)
    d.do(K.RECORD, R.RECORDER, to=S.IN_REVIEW, seq=1, previous=None)


def disputed(d):
    delivered(d)
    d.do(K.DISPUTE, R.REQUESTER, amount=40)


def earned_accepted(d):
    delivered(d)
    d.do(K.ACCEPT_REVIEW, R.REQUESTER)


def earned_decided(d):
    disputed(d)
    d.do(K.DECIDE, R.REVIEWER, amount=60)


def appealed(d):
    earned_decided(d)
    d.do(K.APPEAL, R.PROVIDER, at=APPEAL - 1)


def closed(d):
    earned_accepted(d)
    d.do(K.RECORD, R.RECORDER, to=S.CLOSED, seq=1, previous=None)


def cancelled(d):
    authorized(d)
    d.do(K.CANCEL, R.REQUESTER)


def declined(d):
    offered(d)
    d.do(K.DECLINE, R.PROVIDER)


STATES = {"none": (lambda d: None, S.NONE), "offered": (offered, S.OFFERED), "declined": (declined, S.DECLINED),
          "agreed": (agreed, S.AGREED), "reserving": (reserving, S.RESERVING), "funded": (funded, S.FUNDED),
          "authorized": (authorized, S.AUTHORIZED), "working": (working, S.WORKING),
          "candidate": (candidate, S.WORKING), "stopped": (stopped, S.STOPPED),
          "delivered": (delivered, S.DELIVERED), "in_review": (in_review, S.IN_REVIEW),
          "disputed": (disputed, S.DISPUTED), "earned_accepted": (earned_accepted, S.EARNED),
          "earned_decided": (earned_decided, S.EARNED), "appealed": (appealed, S.APPEALED),
          "closed": (closed, S.CLOSED), "cancelled": (cancelled, S.CANCELLED)}

# written by hand from the spec's event table, NOT read from the module
EXPECT = {
    ("none", K.OFFER): {R.REQUESTER},
    ("offered", K.ACCEPT): {R.PROVIDER}, ("offered", K.DECLINE): {R.PROVIDER},
    ("agreed", K.RESERVE): {R.REQUESTER},
    ("reserving", K.CONFIRM): {R.SETTLEMENT},
    ("funded", K.AUTHORIZE): {R.REQUESTER}, ("stopped", K.AUTHORIZE): {R.REQUESTER},
    ("authorized", K.START): {R.PROVIDER}, ("authorized", K.CANCEL): {R.REQUESTER}, ("stopped", K.CANCEL): {R.REQUESTER},
    ("working", K.STOP): {R.REQUESTER, R.PROVIDER}, ("working", K.CANDIDATE): {R.PROVIDER},
    ("candidate", K.STOP): {R.REQUESTER, R.PROVIDER}, ("candidate", K.CANDIDATE): {R.PROVIDER},
    ("candidate", K.RELEASE): {R.PROVIDER},          # a release needs a candidate first
    ("delivered", K.ACCEPT_REVIEW): {R.REQUESTER}, ("in_review", K.ACCEPT_REVIEW): {R.REQUESTER},
    ("delivered", K.DISPUTE): {R.REQUESTER}, ("in_review", K.DISPUTE): {R.REQUESTER},
    ("disputed", K.DECIDE): {R.REVIEWER},
    ("earned_decided", K.APPEAL): {R.REQUESTER, R.PROVIDER},
    ("appealed", K.FINAL): {R.APPEAL},
    ("delivered", K.RECORD): {R.RECORDER}, ("earned_accepted", K.RECORD): {R.RECORDER},
    ("earned_decided", K.RECORD): {R.RECORDER},
}


def probe(d, label, kind, actor):
    """An event with everything valid except possibly its actor and the state it meets."""
    last = d.last
    parents = {K.RESERVE: (last.get(K.ACCEPT),), K.CONFIRM: (last.get(K.RESERVE),),
               K.AUTHORIZE: (last.get(K.ACCEPT), last.get(K.CONFIRM), last.get(K.STOP)),
               K.START: (last.get(K.AUTHORIZE),)}.get(kind, ())
    extra = {}
    if kind in (K.AUTHORIZE, K.START):
        extra["grant_ok"] = True
    if kind is K.RELEASE:
        extra["release_ok"] = True
    if kind is K.RECORD:
        extra.update(to=S.CLOSED if label.startswith("earned") else S.IN_REVIEW,
                     seq=len(d.m.stage_records) + 1, previous=d.m.stage_records[-1][1] if d.m.stage_records else None)
    return d.event(kind, actor, [p for p in parents if p], at=0, **extra)


def test_every_state_event_and_actor_is_allowed_only_where_the_spec_table_says():
    checked = 0
    for label, (build, stage) in STATES.items():
        for kind, actor in itertools.product(K, R):
            d = Driver()
            build(d)
            assert d.m.stage is stage, label
            outcome = d.m.apply(probe(d, label, kind, actor))
            allowed = actor in EXPECT.get((label, kind), set())
            assert (outcome is O.APPLIED) == allowed, (label, kind.value, actor.value, outcome.value)
            checked += 1
    assert checked == len(STATES) * len(K) * len(R) > 1500


def test_a_paid_stage_walks_the_whole_sequence_to_closed():
    d = Driver()
    closed(d)
    assert d.m.stage is S.CLOSED and d.m.earned_units == GROSS and d.m.reservation_count == 1
    assert d.m.released_hold_units == 0


def test_work_starts_only_after_authorization_and_the_providers_own_grant():
    for build in (agreed, reserving, funded):
        d = Driver()
        build(d)
        e = d.event(K.START, R.PROVIDER, (d.last[K.ACCEPT],), grant_ok=True)
        assert d.m.apply(e) is O.NOT_ALLOWED and d.m.stage is not S.WORKING, "a countersignature alone never starts work"
    for grant in (False, None):
        d = Driver()
        authorized(d)
        assert d.m.apply(d.event(K.START, R.PROVIDER, (d.last[K.AUTHORIZE],), grant_ok=grant)) is O.DENIED
        assert d.m.stage is S.AUTHORIZED
    d = Driver()
    authorized(d)
    assert d.m.apply(d.event(K.START, R.PROVIDER, (d.last[K.ACCEPT],), grant_ok=True)) is O.BAD_PARENTS


def test_authorization_needs_funding_a_grant_acceptance_and_for_a_paid_stage_a_policy():
    d = Driver()
    agreed(d)
    assert d.m.apply(d.event(K.AUTHORIZE, R.REQUESTER, (d.last[K.ACCEPT],), grant_ok=True)) is O.NOT_ALLOWED, \
        "a paid stage is funded before it is authorized"
    d = Driver()
    funded(d)
    for parents, grant, outcome in (((d.last[K.ACCEPT],), True, O.BAD_PARENTS), ((d.last[K.CONFIRM],), True, O.BAD_PARENTS),
                                    ((d.last[K.ACCEPT], d.last[K.CONFIRM]), False, O.DENIED),
                                    ((d.last[K.ACCEPT], d.last[K.CONFIRM]), None, O.DENIED)):
        assert d.m.apply(d.event(K.AUTHORIZE, R.REQUESTER, parents, grant_ok=grant)) is outcome
    assert d.m.stage is S.FUNDED


def test_a_paid_stage_has_no_default_policy():
    d = Driver(pol=None)
    funded(d)
    assert d.m.apply(d.event(K.AUTHORIZE, R.REQUESTER, (d.last[K.ACCEPT], d.last[K.CONFIRM]), grant_ok=True)) is O.NOT_ALLOWED
    v = Driver(pol=None, paid=False, gross=0)
    agreed(v)
    v.do(K.AUTHORIZE, R.REQUESTER, (v.last[K.ACCEPT],), grant_ok=True)
    assert v.m.stage is S.AUTHORIZED, "a voluntary stage needs no funding and no priced policy"


def test_a_voluntary_stage_skips_the_reservation_and_a_paid_stage_cannot():
    v = Driver(paid=False, gross=0)
    agreed(v)
    assert v.m.apply(v.event(K.RESERVE, R.REQUESTER, (v.last[K.ACCEPT],))) is O.NOT_ALLOWED
    with pytest.raises(ValueError):
        js.JobStage(True, 0, POLICY)
    for bad in (("yes", 5), (True, -1), (False, 1.5)):
        with pytest.raises(ValueError):
            js.JobStage(*bad)


def test_a_cancelled_authorization_releases_the_hold_once_and_debits_nothing():
    d = Driver()
    cancelled(d)
    assert d.m.stage is S.CANCELLED and d.m.released_hold_units == GROSS and d.m.earned_units == 0
    assert d.m.reserved_units == 0
    assert d.m.apply(d.event(K.CANCEL, R.REQUESTER)) is O.NOT_ALLOWED
    assert d.m.released_hold_units == GROSS, "released once, not twice"
    assert d.m.apply(d.event(K.AUTHORIZE, R.REQUESTER, (d.last[K.ACCEPT], d.last[K.CONFIRM]), grant_ok=True)) is O.NOT_ALLOWED


def test_a_resume_after_a_stop_needs_a_fresh_authorization_and_no_second_reservation():
    d = Driver()
    stopped(d)
    assert d.m.apply(d.event(K.START, R.PROVIDER, (d.last[K.AUTHORIZE],), grant_ok=True)) is O.NOT_ALLOWED
    no_stop = d.event(K.AUTHORIZE, R.REQUESTER, (d.last[K.ACCEPT], d.last[K.CONFIRM]), grant_ok=True)
    assert d.m.apply(no_stop) is O.BAD_PARENTS, "a resume names the stop it resumes"
    first_auth = d.last[K.AUTHORIZE]
    d.do(K.AUTHORIZE, R.REQUESTER, (d.last[K.ACCEPT], d.last[K.CONFIRM], d.last[K.STOP]), grant_ok=True)
    assert d.m.apply(d.event(K.START, R.PROVIDER, (first_auth,), grant_ok=True)) is O.BAD_PARENTS, \
        "the start cites the fresh authorization, not the old one"
    d.do(K.START, R.PROVIDER, (d.last[K.AUTHORIZE],), grant_ok=True)
    assert d.m.stage is S.WORKING and d.m.reservation_count == 1
    assert d.m.apply(d.event(K.RESERVE, R.REQUESTER, (d.last[K.ACCEPT],))) is O.DUPLICATE_RESERVATION
    assert d.m.reservation_count == 1


def test_a_second_reservation_under_one_obligation_is_a_duplicate_not_a_new_hold():
    for build in (reserving, funded, authorized, working):
        d = Driver()
        build(d)
        assert d.m.apply(d.event(K.RESERVE, R.REQUESTER, (d.last[K.ACCEPT],))) is O.DUPLICATE_RESERVATION
        assert d.m.reservation_count == 1 and d.m.reserved_units in (0, GROSS)


def test_the_first_choice_closes_the_offer():
    d = Driver()
    declined(d)
    assert d.m.apply(d.event(K.ACCEPT, R.PROVIDER)) is O.NOT_ALLOWED and d.m.stage is S.DECLINED
    d = Driver()
    agreed(d)
    assert d.m.apply(d.event(K.DECLINE, R.PROVIDER)) is O.NOT_ALLOWED and d.m.stage is S.AGREED


def test_the_same_event_id_is_applied_once():
    d = Driver()
    e = d.event(K.OFFER, R.REQUESTER)
    assert d.m.apply(e) is O.APPLIED and d.m.apply(e) is O.DUPLICATE and d.m.stage is S.OFFERED
    f = d.event(K.RESERVE, R.REQUESTER)
    d.m.apply(d.event(K.ACCEPT, R.PROVIDER))
    again = js.StageEvent(K.OFFER, R.REQUESTER, e.event_id)
    assert d.m.apply(again) is O.DUPLICATE and f.event_id != e.event_id


def test_a_late_delivery_pauses_and_never_resolves():
    d = Driver()
    working(d)
    d.do(K.CANDIDATE, R.PROVIDER)
    d.do(K.RELEASE, R.PROVIDER, release_ok=True, at=DELIVERY + 1)
    assert d.m.stage is S.DELIVERED and d.m.paused is True and d.m.earned_units == 0
    e = Driver()
    delivered(e)
    assert e.m.paused is False


def test_an_output_release_needs_a_candidate_and_verified_version_rights_and_recipient():
    for candidate, release_ok in ((False, True), (True, False), (True, None)):
        d = Driver()
        working(d)
        if candidate:
            d.do(K.CANDIDATE, R.PROVIDER)
        assert d.m.apply(d.event(K.RELEASE, R.PROVIDER, release_ok=release_ok)) is O.DENIED
        assert d.m.stage is S.WORKING


def test_no_clock_ever_earns_resolves_or_pays():
    for action in sp.TimeoutAction:
        pol = policy(timeout_action=action.value)
        for label in ("delivered", "in_review", "disputed", "appealed"):
            for now in (0, REVIEW, REVIEW + 1, APPEAL + 1, 10 ** 10):
                d = Driver(pol)
                STATES[label][0](d)
                before = (d.m.earned_units, d.m.stage)
                for _ in range(3):
                    d.m.tick(now)
                    assert d.m.stage not in (S.EARNED, S.CLOSED) or (action is sp.TimeoutAction.CLOSE_WITHOUT_NEXT_STAGE
                                                                      and d.m.stage is S.CLOSED), (action, label, now)
                    assert d.m.earned_units == before[0] if label not in ("appealed",) else True
                if label in ("delivered", "in_review"):
                    assert d.m.earned_units == 0, "nothing was earned because time passed"


def test_each_timeout_action_does_only_what_it_says():
    expected = {sp.TimeoutAction.REFER_TO_REVIEWER: S.DISPUTED, sp.TimeoutAction.HOLD_DISPUTED: S.DELIVERED,
                sp.TimeoutAction.CLOSE_WITHOUT_NEXT_STAGE: S.CLOSED}
    for action, stage in expected.items():
        d = Driver(policy(timeout_action=action.value))
        delivered(d)
        assert d.m.tick(REVIEW) is O.NOT_ALLOWED and d.m.stage is S.DELIVERED, "not past the deadline yet"
        assert d.m.tick(REVIEW + 1) is O.APPLIED and d.m.stage is stage
        assert d.m.frozen_units == GROSS, "the challenged amount is paused"
        assert d.m.earned_units == 0
    d = Driver()
    working(d)
    assert d.m.tick(10 ** 10) is O.NOT_ALLOWED, "a deadline only matters once something was delivered"
    assert js.JobStage(True, 5, None).tick(1) is O.NOT_ALLOWED


def test_an_allowed_amount_never_exceeds_the_reserved_gross():
    for kind, actor, build in ((K.ACCEPT_REVIEW, R.REQUESTER, delivered), (K.DISPUTE, R.REQUESTER, delivered),
                               (K.DECIDE, R.REVIEWER, disputed)):
        for amount, outcome in ((GROSS, O.APPLIED), (0, O.APPLIED), (GROSS + 1, O.DENIED), (-1, O.DENIED),
                                (True, O.DENIED), (1.5, O.DENIED), ("5", O.DENIED), (10 ** 9, O.DENIED)):
            d = Driver()
            build(d)
            assert d.m.apply(d.event(kind, actor, amount=amount)) is outcome, (kind, amount)
    d = Driver()
    appealed(d)
    assert d.m.apply(d.event(K.FINAL, R.APPEAL, amount=GROSS + 1)) is O.DENIED
    assert d.m.apply(d.event(K.FINAL, R.APPEAL, amount=10)) is O.APPLIED and d.m.earned_units == 10


def test_a_dispute_freezes_only_the_disputed_amount():
    d = Driver()
    disputed(d)
    assert d.m.frozen_units == 40 and d.m.stage is S.DISPUTED
    d.do(K.DECIDE, R.REVIEWER, amount=60)
    assert d.m.frozen_units == 0 and d.m.earned_units == 60


def test_the_requesters_acceptance_does_not_earn_when_an_independent_reviewer_decides():
    d = Driver(INDEPENDENT)
    delivered(d)
    assert d.m.apply(d.event(K.ACCEPT_REVIEW, R.REQUESTER)) is O.NOT_ALLOWED and d.m.earned_units == 0
    d.do(K.DECIDE, R.REVIEWER, amount=70)
    assert d.m.stage is S.EARNED and d.m.earned_units == 70
    plain = Driver()
    delivered(plain)
    assert plain.m.apply(plain.event(K.DECIDE, R.REVIEWER, amount=70)) is O.NOT_ALLOWED, \
        "with a requester-approved policy the reviewer decides only a dispute"


def test_an_appeal_needs_a_decision_comes_before_the_deadline_and_happens_once():
    d = Driver()
    earned_accepted(d)
    assert d.m.apply(d.event(K.APPEAL, R.REQUESTER, at=0)) is O.NOT_ALLOWED, "nothing was decided"
    d = Driver()
    earned_decided(d)
    assert d.m.apply(d.event(K.APPEAL, R.PROVIDER, at=APPEAL + 1)) is O.NOT_ALLOWED
    d.do(K.APPEAL, R.PROVIDER, at=APPEAL)
    assert d.m.stage is S.APPEALED and d.m.frozen_units == 60
    d.do(K.FINAL, R.APPEAL, amount=60)
    assert d.m.stage is S.EARNED and d.m.frozen_units == 0
    assert d.m.apply(d.event(K.APPEAL, R.REQUESTER, at=0)) is O.NOT_ALLOWED, "the appeal decision is final"


def test_stage_records_are_monotonic_and_only_for_allowed_transitions():
    d = Driver()
    delivered(d)
    bad = [dict(to=S.IN_REVIEW, seq=2, previous=None), dict(to=S.IN_REVIEW, seq=0, previous=None),
           dict(to=S.IN_REVIEW, seq=1, previous="x"), dict(to=S.IN_REVIEW, seq=None, previous=None)]
    for fields in bad:
        assert d.m.apply(d.event(K.RECORD, R.RECORDER, **fields)) is O.BAD_PARENTS, fields
    for to in (S.EARNED, S.CLOSED, S.WORKING, S.DELIVERED, None):
        assert d.m.apply(d.event(K.RECORD, R.RECORDER, to=to, seq=1, previous=None)) is O.NOT_ALLOWED, to
    first = d.do(K.RECORD, R.RECORDER, to=S.IN_REVIEW, seq=1, previous=None)
    d.do(K.ACCEPT_REVIEW, R.REQUESTER)
    assert d.m.apply(d.event(K.RECORD, R.RECORDER, to=S.CLOSED, seq=1, previous=first)) is O.BAD_PARENTS
    d.do(K.RECORD, R.RECORDER, to=S.CLOSED, seq=2, previous=first)
    assert d.m.stage is S.CLOSED and [seq for seq, _ in d.m.stage_records] == [1, 2]
    for actor in R:
        if actor is not R.RECORDER:
            e = Driver()
            delivered(e)
            assert e.m.apply(e.event(K.RECORD, actor, to=S.IN_REVIEW, seq=1, previous=None)) is O.WRONG_ACTOR


def test_a_stage_that_ended_cannot_be_moved_by_any_event():
    for label in ("declined", "closed", "cancelled"):
        for kind, actor in itertools.product(K, R):
            d = Driver()
            STATES[label][0](d)
            outcome = d.m.apply(probe(d, label, kind, actor))
            assert outcome is not O.APPLIED or (label == "closed" and False), (label, kind, actor)
            assert d.m.stage is STATES[label][1]


def test_no_sequence_of_short_event_lists_reaches_earned_without_a_review_event():
    """Every sequence of up to three events from any plausible set, applied to a freshly delivered stage."""
    pool = [(K.ACCEPT_REVIEW, R.REQUESTER), (K.DISPUTE, R.REQUESTER), (K.DECIDE, R.REVIEWER), (K.APPEAL, R.PROVIDER),
            (K.FINAL, R.APPEAL), (K.RECORD, R.RECORDER), ("tick", None), (K.STOP, R.PROVIDER)]
    for length in (1, 2, 3):
        for seq in itertools.product(pool, repeat=length):
            d = Driver()
            delivered(d)
            had_review = False
            for kind, actor in seq:
                if kind == "tick":
                    d.m.tick(REVIEW + 5)
                else:
                    e = probe(d, "delivered" if d.m.stage is S.DELIVERED else "x", kind, actor)
                    outcome = d.m.apply(e)
                    had_review |= outcome is O.APPLIED and kind in (K.ACCEPT_REVIEW, K.DECIDE, K.FINAL)
                assert d.m.stage in set(S)
            if d.m.stage in (S.EARNED, S.APPEALED):
                assert had_review, [(k, a) for k, a in seq]
            assert d.m.earned_units == 0 or had_review


def test_the_stage_modules_are_pure_and_never_touch_money_or_the_network():
    import ast
    allowed = {"__future__", "dataclasses", "enum", "re", "agent_friday.services.stage_policy"}
    for module in (js, sp):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        assert imported <= allowed, (module.__name__, imported - allowed)

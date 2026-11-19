"""Pending queue for out-of-order peer events (roadmap F4b, row NS-40.2-11). Pure and offline."""
import pytest

try:
    from agent_friday.services import peer_event_pending as pq
    from agent_friday.services.peer_wire_v2 import Verified
except ImportError:  # red evidence: the queue is not in the tree yet
    pq = Verified = None

T0 = 1_000_000


@pytest.fixture(autouse=True)
def _exists():
    assert pq is not None, "services/peer_event_pending.py does not exist: no pending queue for out-of-order peer events"


def ev(eid, parents=(), realm="r1", job="j1", expires=T0 + 100):
    return Verified(eid, {"realm": realm, "job": job, "parents": sorted(parents), "expires_at": expires}, "sig")


def queue(now=[T0], **kw):
    now[0] = T0
    kw.setdefault("subscribed", lambda realm, job: (realm, job) == ("r1", "j1"))
    q = pq.PendingQueue(now=lambda: now[0], **kw)
    q.now = now
    return q


def test_a_child_waits_for_its_parent_and_is_released_when_it_arrives():
    q = queue()
    assert q.park(ev("b", ["a"])) == (True, "parked")
    assert q.waiting_on() == {"a"}
    assert q.arrived("a", have=lambda i: i == "a") == [ev("b", ["a"])]
    assert len(q) == 0


def test_a_chain_releases_in_dependency_order_and_ties_by_event_id_not_arrival():
    q = queue()
    for e in (ev("d", ["c"]), ev("c", ["a"]), ev("b", ["a"])):
        assert q.park(e)[0]
    out = [e.event_id for e in q.arrived("a", have=lambda i: i == "a")]
    assert out == ["b", "c", "d"]


def test_an_event_with_two_parents_waits_for_both():
    q = queue()
    q.park(ev("c", ["a", "b"]))
    assert q.arrived("a", have=lambda i: i == "a") == []
    assert [e.event_id for e in q.arrived("b", have=lambda i: i in ("a", "b"))] == ["c"]


def test_only_subscribed_scopes_may_park():
    q = queue()
    assert q.park(ev("b", ["a"], realm="other")) == (False, "unsubscribed")
    assert q.park(ev("b", ["a"], job="other")) == (False, "unsubscribed")
    assert len(q) == 0


def test_an_event_past_its_own_expiry_is_refused_and_a_parked_one_is_purged_at_expiry():
    q = queue()
    assert q.park(ev("x", ["a"], expires=T0)) == (False, "expired")
    assert q.park(ev("b", ["a"], expires=T0 + 10))[0]
    q.now[0] = T0 + 10
    assert len(q) == 0 and q.arrived("a", have=lambda i: True) == []


def test_the_queue_is_capped_overall_and_per_scope_and_never_evicts():
    q = queue(max_pending=2, max_per_scope=5)
    assert q.park(ev("a", ["z"]))[0] and q.park(ev("b", ["z"]))[0]
    assert q.park(ev("c", ["z"])) == (False, "full")
    assert len(q) == 2
    q2 = queue(max_pending=10, max_per_scope=1, subscribed=lambda r, j: True)
    assert q2.park(ev("a", ["z"]))[0]
    assert q2.park(ev("b", ["z"])) == (False, "scope_full")
    assert q2.park(ev("c", ["z"], job="j2"))[0]


def test_a_cycle_a_self_parent_and_a_duplicate_are_refused():
    q = queue()
    assert q.park(ev("a", ["b"]))[0]
    assert q.park(ev("b", ["a"])) == (False, "cycle")
    assert q.park(ev("c", ["c"])) == (False, "self_parent")
    assert q.park(ev("a", ["b"])) == (False, "duplicate")


def test_a_parent_known_under_another_realm_is_a_cross_realm_chain():
    q = queue(known_realm=lambda i: "r2" if i == "p" else None)
    assert q.park(ev("b", ["p"])) == (False, "cross_realm")
    q2 = queue(subscribed=lambda r, j: True)
    q2.park(ev("p", ["z"], realm="r2"))
    assert q2.park(ev("b", ["p"], realm="r1")) == (False, "cross_realm")


def test_refusal_codes_are_a_closed_vocabulary_that_carries_no_peer_data():
    q = queue()
    codes = {q.park(ev("b", ["a"], realm="secret-realm-name"))[1]}
    assert codes <= {"unsubscribed", "expired", "full", "scope_full", "self_parent", "cycle", "cross_realm", "duplicate", "parked"}
    assert "secret" not in "".join(codes)


def test_re_checking_with_no_arrival_releases_what_is_already_satisfied():
    q = queue()
    q.park(ev("b", ["a"]))
    assert [e.event_id for e in q.arrived(None, have=lambda i: i == "a")] == ["b"]

"""The pending queue's rules, checked over every small case instead of a few examples.

The module's docstring pairs each rule with a test name; the last test here fails if a
name in that table stops existing.
"""
import ast
import itertools
import re
from pathlib import Path

from agent_friday.services import peer_event_pending as pq
from agent_friday.services.peer_wire_v2 import Verified

T0 = 1_000_000
IDS = ("a", "b", "c", "d")
ALLOWED_IMPORTS = {"__future__", "time", "dataclasses", "enum", "typing",
                   "agent_friday.services.peer_wire_v2"}
TESTS_DIR = Path(__file__).parent


def event(event_id, parents, expires=T0 + 100, realm="r1", job="j1"):
    body = {"realm": realm, "job": job, "parents": sorted(parents), "expires_at": expires}
    return Verified(event_id, body, "sig")


def new_queue(clock, **limits):
    return pq.PendingQueue(subscribed=lambda realm, job: True, now=lambda: clock[0], **limits)


def every_family():
    """Every way to give three events parents drawn from a root 'a' and each other."""
    choices = [[frozenset(c) for n in range(1, 3) for c in itertools.combinations(IDS, n)]] * 3
    for parent_sets in itertools.product(*choices):
        yield [event(event_id, parents) for event_id, parents in zip(IDS[1:], parent_sets)]


def park_all(queue, events):
    return [e for e in events if queue.park(e)[0]]


def test_every_release_follows_all_of_its_parents():
    for family in every_family():
        for order in itertools.permutations(family):
            queue = new_queue([T0])
            parked = park_all(queue, order)
            released = queue.arrived("a", have=lambda event_id: event_id == "a")
            arrived = {"a"}
            for e in released:
                assert set(e.body["parents"]) <= arrived, (order, released)
                arrived.add(e.event_id)
            assert len({e.event_id for e in released}) == len(released)
            assert len(queue) == len(parked) - len(released)
            assert queue.arrived("a", have=lambda event_id: event_id == "a") == []


def test_release_order_is_the_same_for_every_arrival_order():
    for family in every_family():
        outcomes = set()
        for order in itertools.permutations(family):
            queue = new_queue([T0])
            if len(park_all(queue, order)) < len(family):
                continue
            released = queue.arrived("a", have=lambda event_id: event_id == "a")
            outcomes.add(tuple(e.event_id for e in released))
        assert len(outcomes) <= 1, (family, outcomes)


def test_a_refused_event_never_changes_the_queue():
    for family in every_family():
        for max_pending, max_per_scope in ((1, 9), (2, 9), (9, 1), (9, 2)):
            queue = new_queue([T0], max_pending=max_pending, max_per_scope=max_per_scope)
            held = []
            for e in family:
                accepted, _code = queue.park(e)
                if accepted:
                    held.append(e.event_id)
                assert sorted(queue._waiting) == sorted(held)
                assert len(held) <= min(max_pending, max_per_scope)


def test_nothing_is_released_at_or_after_its_expiry():
    for expiries in itertools.product((T0 + 1, T0 + 2, T0 + 3), repeat=3):
        for now in (T0, T0 + 1, T0 + 2, T0 + 3):
            clock = [T0]
            queue = new_queue(clock)
            for event_id, expires in zip(IDS[1:], expiries):
                queue.park(event(event_id, ["a"], expires=expires))
            clock[0] = now
            released = queue.arrived("a", have=lambda event_id: event_id == "a")
            assert all(e.body["expires_at"] > now for e in released)
            assert len(queue) == 0


def test_the_module_imports_nothing_that_can_reach_out():
    tree = ast.parse(Path(pq.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS


def test_every_rule_in_the_module_docstring_names_a_test_that_exists():
    named = set(re.findall(r"\btest_\w+", pq.__doc__))
    assert len(named) >= 8
    defined = set()
    for path in TESTS_DIR.glob("test_peer_event_pending*.py"):
        defined |= set(re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.M))
    assert named <= defined, named - defined

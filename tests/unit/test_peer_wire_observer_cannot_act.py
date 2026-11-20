"""NS-40.2-5: an observer can verify a peer event but never act as its addressed recipient. TEST CREDITS ONLY.

verify_event authenticates bytes and nothing more ("Returning means authenticated bytes"). Acting on an event (queueing an
effect, answering as the recipient) is a different step, and it must only ever be taken by the node the event is addressed
to. `act_on(verified, own_id)` is that step's one door: it returns the event body only when the event's recipient is
exactly `own_id`, and refuses with WireError("recipient") otherwise. What each check bites on:
  1. an observer (any node, even one that trusts the actor's key) verifies the event, and a node it is NOT addressed to
     cannot act on it, for every event type in the bundle's vectors that verifies;
  2. the addressed node gets the body, unchanged;
  3. the id must match exactly (case, whitespace, prefix, empty and non-string ids all refuse);
  4. the check can fail: with the comparison removed (a mutant) the observer would act.
The limit, stated plainly: nothing in the tree calls act_on yet (no caller acts on a v2 event), so this is the door and its
proof, not a wiring; the HTTP ingest route records evidence and acts on nothing. Project-scoped keys are not modelled.
"""
import json
from pathlib import Path

import pytest

from agent_friday.services import peer_wire_v2 as wire

BUNDLE = Path(__file__).resolve().parents[1] / "federation" / "bundle"
VECTORS = json.loads((BUNDLE / "fixtures" / "wire-v2.json").read_text(encoding="utf-8"))
TRUST = set(VECTORS["peer_keys"].values())
OBSERVER = "cd" * 32


def _raw(case):
    return bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else wire.canonical(case["envelope"])


def _verifying_cases():
    out = []
    for case in VECTORS["cases"]:
        try:
            out.append((case["name"], wire.verify_event(_raw(case), TRUST)))
        except wire.WireError:
            pass
    return out


def test_there_are_verifying_events_to_test_with():
    assert len(_verifying_cases()) >= 3


def test_an_observer_verifies_every_event_but_cannot_act_on_any_of_them():
    for name, verified in _verifying_cases():
        assert verified.body["recipient"] != OBSERVER
        with pytest.raises(wire.WireError) as e:
            wire.act_on(verified, OBSERVER)
        assert e.value.code == "recipient", name


def test_the_addressed_node_gets_the_body_unchanged():
    for name, verified in _verifying_cases():
        assert wire.act_on(verified, verified.body["recipient"]) == verified.body, name


@pytest.mark.parametrize("make", [lambda r: r.upper(), lambda r: r + " ", lambda r: " " + r, lambda r: r[:-1], lambda r: "",
                                  lambda r: None, lambda r: 7, lambda r: [r]])
def test_the_id_must_match_exactly(make):
    _, verified = _verifying_cases()[0]
    with pytest.raises(wire.WireError) as e:
        wire.act_on(verified, make(verified.body["recipient"]))
    assert e.value.code == "recipient"


def test_the_check_can_fail_with_the_comparison_removed_an_observer_would_act():
    # negative control: the real source of act_on with its comparison deleted, run, lets an observer act
    import inspect
    source = inspect.getsource(wire.act_on)
    mutant = source.replace('if not isinstance(own_id, str) or own_id != verified.body["recipient"]:', "if False:")
    assert mutant != source, "the mutation must change the source"
    namespace = {"_fail": wire._fail, "Verified": wire.Verified}
    exec(mutant, namespace)
    _, verified = _verifying_cases()[0]
    assert namespace["act_on"](verified, OBSERVER) == verified.body, "without the comparison the observer acts"
    with pytest.raises(wire.WireError):
        wire.act_on(verified, OBSERVER)

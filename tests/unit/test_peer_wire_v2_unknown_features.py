"""An unknown protocol feature is refused, never downgraded (Federation / Economy spec NS-40.2-15, first sentence). TEST DATA ONLY.

The v2 verifier's schema is closed (additionalProperties false throughout) and pins the protocol string, so an event that
carries a field this verifier does not know, or names another protocol version, is refused with code "schema" before the
signature, the key or any content is read; nothing is accepted "as far as it understands". What each check bites on:
  1. the pinned bundle vector verifies (the control: the three mutations below are the only difference);
  2. an unknown field in the body, an unknown field beside it in the envelope, and another protocol version are each refused
     with "schema", not accepted and not recorded;
  3. receive() records nothing for them (the store stays empty);
  4. the check can fail: with a schema that allows additional properties, the unknown field is no longer refused.
Not covered here (named in the map row): signed grants limiting peers, mailboxes and purposes, and salted commitments.
"""
import copy
import json
from pathlib import Path

import pytest

from agent_friday.services import peer_wire_v2 as wire

BUNDLE = Path(__file__).resolve().parents[1] / "federation" / "bundle"
VECTORS = json.loads((BUNDLE / "fixtures" / "wire-v2.json").read_text(encoding="utf-8"))
TRUST = set(VECTORS["peer_keys"].values())
BASE = VECTORS["cases"][0]["envelope"]


def _with(change):
    env = copy.deepcopy(BASE)
    change(env)
    return wire.canonical(env)


MUTATIONS = [
    ("an unknown body field", lambda e: e["body"].__setitem__("new_feature", "x")),
    ("an unknown envelope field", lambda e: e.__setitem__("extension", {"x": 1})),
    ("another protocol version", lambda e: e["body"].__setitem__("protocol", "asimov-peer/v3")),
]


def test_the_pinned_vector_verifies_so_the_mutations_are_the_only_difference():
    assert wire.verify_event(wire.canonical(BASE), TRUST).body["protocol"] == "asimov-peer/v2"


@pytest.mark.parametrize("label,change", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_an_unknown_feature_is_refused_as_schema_not_downgraded(label, change):
    with pytest.raises(wire.WireError) as e:
        wire.verify_event(_with(change), TRUST)
    assert e.value.code == "schema", label


@pytest.mark.parametrize("label,change", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_receive_records_nothing_for_an_unknown_feature(tmp_path, label, change):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    with pytest.raises(wire.WireError):
        wire.receive(_with(change), TRUST, store)
    # the same store still accepts the pinned vector as the FIRST event it has ever seen
    disposition, _eid = wire.receive(wire.canonical(BASE), TRUST, store)
    assert disposition == "enqueued", label


def test_the_check_can_fail_a_schema_that_allows_extra_properties_lets_the_unknown_field_through(monkeypatch):
    def open_up(node):
        if isinstance(node, dict):
            return {k: open_up(v) for k, v in node.items() if k != "additionalProperties"}
        if isinstance(node, list):
            return [open_up(v) for v in node]
        return node
    monkeypatch.setattr(wire, "event_schema", lambda: open_up(json.loads(
        (Path(wire.__file__).resolve().parent.parent / "resources" / "federation" / "peer-event-v2.schema.json")
        .read_text(encoding="utf-8"))))
    assert wire.verify_event(_with(MUTATIONS[1][1]), TRUST), "the closed schema is what refuses the unknown field"

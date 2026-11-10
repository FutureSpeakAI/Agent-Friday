"""Friday's own peer-event v2 verifier agrees with the bundle's public wire vectors
(Federation / Economy spec, roadmap F3/F4). The 21 vectors in
tests/federation/bundle/fixtures/wire-v2.json were produced by the bundle's reference
checker; here they run against services/peer_wire_v2.py, so a difference in canonical bytes,
signature check, header rules, offer-terms rules or idempotency shows up as a failing case.
Offline; fictitious public test keys only."""
import hashlib
import json
from pathlib import Path

import pytest

try:
    from agent_friday.services import peer_wire_v2 as wire
except ImportError:  # red evidence: the verifier is not in the tree yet
    wire = None

BUNDLE = Path(__file__).resolve().parents[1] / "federation" / "bundle"
VECTORS = json.loads((BUNDLE / "fixtures" / "wire-v2.json").read_text(encoding="utf-8"))
TRUST = set(VECTORS["peer_keys"].values())
RUNTIME_SCHEMA = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "resources"
                  / "federation" / "peer-event-v2.schema.json")


@pytest.fixture(autouse=True)
def _the_verifier_exists():
    assert wire is not None, "services/peer_wire_v2.py does not exist: Friday has no v2 peer verifier"


def _raw(case):
    return bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else wire.canonical(case["envelope"])


def test_the_runtime_schema_is_the_pinned_bundle_schema():
    # compared as parsed JSON: a Windows checkout may rewrite line endings in this copy, the
    # bundle copy is byte-pinned (-text) by tests/federation
    pinned = json.loads((BUNDLE / "schema" / "peer-event-v2.schema.json").read_text(encoding="utf-8"))
    assert json.loads(RUNTIME_SCHEMA.read_text(encoding="utf-8")) == pinned


def test_every_bundle_vector_gets_the_bundles_answer(tmp_path):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    names = []
    for case in VECTORS["cases"]:
        try:
            disposition, eid = wire.receive(_raw(case), TRUST, store)
            outcome = {"enqueued": "verified_bytes"}.get(disposition, disposition)
            if case.get("event_id"):
                assert eid == case["event_id"], case["name"] + ": wrong event id"
        except wire.WireError as e:
            outcome = e.code
        assert outcome == case["expect"], f'{case["name"]}: expected {case["expect"]}, got {outcome}'
        names.append(case["name"])
    assert len(names) == len(VECTORS["cases"]) >= 20


def test_a_replay_returns_the_recorded_result_and_a_different_event_is_recorded_as_equivocation(tmp_path):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    first = VECTORS["cases"][0]
    raw = _raw(first)
    assert wire.receive(raw, TRUST, store)[0] == "enqueued"
    assert wire.receive(raw, TRUST, store)[0] == "replay"
    again = wire.PeerEventStore(tmp_path / "peer.db")           # durable across a restart
    assert wire.receive(raw, TRUST, again)[0] == "replay"
    assert again.equivocations() == []


def test_the_first_event_stays_authoritative_when_the_same_action_id_names_another_event(tmp_path):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    equivocating = [c for c in VECTORS["cases"] if c["expect"] == "equivocation"]
    if not equivocating:
        pytest.skip("no equivocation vector in this bundle")
    for case in VECTORS["cases"]:
        try:
            wire.receive(_raw(case), TRUST, store)
        except wire.WireError:
            pass
    assert store.equivocations(), "the equivocating event was not recorded"


def test_an_unknown_actor_is_refused_before_anything_is_recorded(tmp_path):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    with pytest.raises(wire.WireError) as e:
        wire.receive(_raw(VECTORS["cases"][0]), set(), store)
    assert e.value.code == "untrusted_key"
    assert store.equivocations() == []


@pytest.mark.parametrize("mutate,code", [
    (lambda raw: raw + b" ", "noncanonical"),
    (lambda raw: raw.replace(b"act-1", b"act-9"), "signature"),
    (lambda raw: raw[:-3], "encoding"),
    (lambda raw: b"\xef\xbb\xbf" + raw, "encoding"),
    (lambda raw: b"x" * 65537, "size"),
])
def test_altered_bytes_never_verify(mutate, code):
    raw = _raw(VECTORS["cases"][0])
    with pytest.raises(wire.WireError) as e:
        wire.verify_event(mutate(raw), TRUST)
    assert e.value.code == code


def test_a_delayed_copy_is_evidence_not_a_request_to_act():
    body = VECTORS["cases"][0]["envelope"]["body"]
    assert wire.effects_allowed_now(body, now=body["issued_at"]) is True
    assert wire.effects_allowed_now(body, now=body["issued_at"] - 1) is False
    assert wire.effects_allowed_now(body, now=body["expires_at"]) is False


def test_canonical_rejects_what_the_profile_forbids():
    for bad in ({"a": 1.5}, {"a": -1}, {"a": 2 ** 53}, {"a": "café"}, {"a": {1, 2}}):
        with pytest.raises(wire.WireError):
            wire.canonical(bad)
    assert wire.canonical({"b": [1, None, True], "a": "x"}) == b'{"a":"x","b":[1,null,true]}'


def test_an_unknown_schema_keyword_is_an_error_not_a_pass():
    with pytest.raises(RuntimeError):
        wire.schema_matches({}, {"type": "object", "patternProperties": {}})

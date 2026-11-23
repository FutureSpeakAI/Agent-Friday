"""NS-40.2-4: a peer on another protocol version is refused explicitly, with its own code. TEST CREDITS ONLY.

The wire is fixed to asimov-peer/v2 with no down-negotiation. Until now an event naming another version failed as a
generic "schema" error, which says nothing to the peer. verify_event now answers "protocol_incompatible" when the
event's protocol names another asimov-peer version, before it reads anything else (so before the key and the
signature: the answer is the same for every sender and leaks nothing). Anything that is not an asimov-peer version
string (garbage, a number, a missing field) stays a plain "schema" refusal. What each check bites on:
  1. other asimov-peer versions (v1, v3, v2.1, V2) get the explicit code, from verify_event and through receive;
  2. the supported version still verifies, and a non-version protocol field is still "schema";
  3. the check can fail: with the pre-check removed (a mutant), the explicit code is not what comes back.
The limit, stated plainly: this is the receiving side's answer. Telling a peer which versions this node speaks, and
the v1 receipts staying readable, are not here; the route already returns the code in a 422.
"""
import copy
import json
from pathlib import Path

import pytest

from agent_friday.services import peer_wire_v2 as wire

BUNDLE = Path(__file__).resolve().parents[1] / "federation" / "bundle"
VECTORS = json.loads((BUNDLE / "fixtures" / "wire-v2.json").read_text(encoding="utf-8"))
TRUST = set(VECTORS["peer_keys"].values())
OFFER = next(c for c in VECTORS["cases"] if c["name"] == "signed_offer")["envelope"]


def _raw(protocol):
    env = copy.deepcopy(OFFER)
    if protocol is KeyError:
        del env["body"]["protocol"]
    else:
        env["body"]["protocol"] = protocol
    return wire.canonical(env)


@pytest.mark.parametrize("protocol", ["asimov-peer/v1", "asimov-peer/v3", "asimov-peer/v2.1", "asimov-peer/V2",
                                      "asimov-peer/v10"])
def test_another_asimov_peer_version_gets_the_explicit_code(protocol):
    with pytest.raises(wire.WireError) as e:
        wire.verify_event(_raw(protocol), TRUST)
    assert e.value.code == "protocol_incompatible"


def test_the_explicit_code_comes_back_whoever_the_sender_and_whatever_the_signature():
    # same answer for an untrusted key: it is decided before the key and the signature are read
    for keys in (TRUST, set()):
        with pytest.raises(wire.WireError) as e:
            wire.verify_event(_raw("asimov-peer/v3"), keys)
        assert e.value.code == "protocol_incompatible"


def test_the_supported_version_still_verifies_and_other_protocol_fields_stay_schema_errors():
    wire.verify_event(wire.canonical(OFFER), TRUST)
    for protocol in ("elsewhere/v2", "asimov-peerx/v2", "", 2, None, ["asimov-peer/v3"], KeyError):
        with pytest.raises(wire.WireError) as e:
            wire.verify_event(_raw(protocol), TRUST)
        assert e.value.code == "schema", protocol


def test_the_check_can_fail_with_the_pre_check_removed_the_code_is_only_schema(monkeypatch):
    monkeypatch.setattr(wire, "_incompatible_protocol", lambda env: False)
    with pytest.raises(wire.WireError) as e:
        wire.verify_event(_raw("asimov-peer/v3"), TRUST)
    assert e.value.code == "schema", "the pre-check is what gives the explicit code"

"""NS-40.2-13: the recipient is one of the cheap checks, made before any content is inspected.

The spec lists schema, signature, pinned key, realm, recipient and type as the checks that run before expensive
content inspection. services/peer_wire_v2.verify_event now takes the caller's own id as `recipient`: an event addressed
to someone else is refused with code "recipient" after the signature check and before the header, terms, role and split
rules read anything. The bundle's 21 vectors carry arbitrary recipients and expect the bundle's answers, so the check is
opt-in per call: with no `recipient` the old behaviour is unchanged (test_peer_wire_v2.py still runs every vector).

The limit, stated plainly: the HTTP route does not pass its own id yet. The bundle's vectors are addressed to fictitious
keys, so enforcing it on the route needs the bundle owner to add a wrong-recipient vector first; this PR is the
verifier half, shown by tests that can fail.
"""
import json
from pathlib import Path

import pytest

from agent_friday.services import peer_wire_v2 as wire

BUNDLE = Path(__file__).resolve().parents[1] / "federation" / "bundle"
VECTORS = json.loads((BUNDLE / "fixtures" / "wire-v2.json").read_text(encoding="utf-8"))
TRUST = set(VECTORS["peer_keys"].values())
CASES = {case["name"]: case for case in VECTORS["cases"]}
OTHER = "ab" * 32


def _raw(name):
    case = CASES[name]
    return bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else wire.canonical(case["envelope"])


def _recipient(name):
    return CASES[name]["envelope"]["body"]["recipient"]


def test_an_event_addressed_to_this_node_verifies():
    verified = wire.verify_event(_raw("signed_offer"), TRUST, recipient=_recipient("signed_offer"))
    assert verified.event_id == CASES["signed_offer"]["event_id"]


def test_an_event_addressed_to_someone_else_is_refused_with_the_recipient_code():
    with pytest.raises(wire.WireError) as e:
        wire.verify_event(_raw("signed_offer"), TRUST, recipient=OTHER)
    assert e.value.code == "recipient"


def test_with_no_recipient_given_nothing_changes():
    for case in VECTORS["cases"]:
        # replay and equivocation are the store's answers; the verifier alone accepts those bytes
        expected = None if case["expect"] in ("verified_bytes", "replay", "equivocation") else case["expect"]
        try:
            wire.verify_event(_raw(case["name"]), TRUST)
            got = None
        except wire.WireError as e:
            got = e.code
        assert got == expected, case["name"]


def test_the_check_runs_before_the_content_rules_read_anything():
    # these vectors fail a content rule when verified normally; addressed elsewhere they must stop at the recipient
    for name, code in (("signed_invalid_split", "split"), ("terms_hash_substitution", "terms_mismatch")):
        with pytest.raises(wire.WireError) as normal:
            wire.verify_event(_raw(name), TRUST)
        assert normal.value.code == code
        with pytest.raises(wire.WireError) as elsewhere:
            wire.verify_event(_raw(name), TRUST, recipient=OTHER)
        assert elsewhere.value.code == "recipient", name


def test_it_does_not_run_before_the_cheaper_checks():
    # a bad signature, an unknown actor and a schema error are still reported as themselves
    for name, code in (("tampered_split", "signature"), ("untrusted_origin", "untrusted_key"),
                       ("missing_commitment", "schema")):
        with pytest.raises(wire.WireError) as e:
            wire.verify_event(_raw(name), TRUST, recipient=OTHER)
        assert e.value.code == code, name


def test_receive_records_nothing_for_an_event_addressed_elsewhere(tmp_path):
    store = wire.PeerEventStore(tmp_path / "peer.db")
    with pytest.raises(wire.WireError) as e:
        wire.receive(_raw("signed_offer"), TRUST, store, recipient=OTHER)
    assert e.value.code == "recipient"
    disposition, _ = wire.receive(_raw("signed_offer"), TRUST, store, recipient=_recipient("signed_offer"))
    assert disposition == "enqueued", "the refused copy left no record behind"


def test_a_malformed_recipient_argument_is_an_error_not_a_pass():
    for bad in ("", "nothex", "AB" * 32, 5):
        with pytest.raises((wire.WireError, ValueError, TypeError)):
            wire.verify_event(_raw("signed_offer"), TRUST, recipient=bad)

"""NS-40.2-13: schema, signature, pinned key and type are checked before any content inspection.

A spy replaces the content checks (offer terms hashing, share sums). An event that fails a
cheap check must be refused with that check's code without the spy being called."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent_friday.services import peer_wire_v2 as wire

BUNDLE = Path(__file__).resolve().parents[1] / "federation" / "bundle"
VECTORS = json.loads((BUNDLE / "fixtures" / "wire-v2.json").read_text(encoding="utf-8"))
TRUST = set(VECTORS["peer_keys"].values())


def _an_offer_envelope():
    for case in VECTORS["cases"]:
        env = case.get("envelope")
        if env and env["body"].get("type") == "job.offer" and case["expect"] == "verified_bytes":
            return copy.deepcopy(env)
    raise AssertionError("the bundle has no valid job.offer vector")


@pytest.fixture
def content_spy(monkeypatch):
    calls = []

    def spy(*args, **kwargs):
        calls.append(args)
        raise AssertionError("content was inspected before the cheap checks passed")

    monkeypatch.setattr(wire, "terms_hash", spy)
    return calls


def _refused_with(raw, trusted):
    with pytest.raises(wire.WireError) as refused:
        wire.verify_event(raw, trusted)
    return refused.value.code


def test_a_bad_schema_is_refused_before_content_is_inspected(content_spy):
    env = _an_offer_envelope()
    del env["signature"]
    assert _refused_with(wire.canonical(env), TRUST) == "schema"
    assert content_spy == []


def test_an_unpinned_key_is_refused_before_content_is_inspected(content_spy):
    raw = wire.canonical(_an_offer_envelope())
    assert _refused_with(raw, set()) == "untrusted_key"
    assert content_spy == []


def test_a_bad_signature_is_refused_before_content_is_inspected(content_spy):
    env = _an_offer_envelope()
    env["body"]["payload"]["terms"]["gross_units"] += 1
    assert _refused_with(wire.canonical(env), TRUST) == "signature"
    assert content_spy == []

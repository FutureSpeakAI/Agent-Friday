"""The ledger's step lines carry no structured personal data.

Each step kept a raw 160-character snippet of its arguments and a
200-character snippet of its result, so an SSN, a card or account number, an
API key or a phone number a tool returned sat in the ledger (and, pinned, in
every later request and summary). Those are redacted at the point of record.
What a resume needs is kept: the tool, the identifying arguments, and the
distinct-step signature, which is computed from the raw arguments.
"""
from __future__ import annotations

from agent_friday.services import task_ledger as tl

SSN = "123-45-6789"  # pragma: allowlist secret
CARD = "4111 1111 1111 1111"  # pragma: allowlist secret
KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"  # pragma: allowlist secret
ROUTING = "021000021"  # pragma: allowlist secret


def _line(led):
    return led["done"][-1]


def test_structured_pii_is_redacted_from_step_lines():
    led = tl.new("g")
    tl.record_step(led, "lookup_customer", {"customer": "C-1042", "ssn": SSN},
                   "card %s, routing %s, key %s, call 415-555-0134" % (CARD, ROUTING, KEY))
    line = _line(led)
    for secret in (SSN, CARD, ROUTING, KEY, "415-555-0134"):
        assert secret not in line, secret
    assert "lookup_customer" in line and "C-1042" in line


def test_ordinary_identifiers_are_kept_for_resume():
    """A 13-digit timestamp and a 9-digit order number are not cards or
    routing numbers (no Luhn / ABA checksum), and a resume needs them."""
    led = tl.new("g")
    tl.record_step(led, "fetch_order", {"order": "123456789", "since": 1790369089123},
                   "order 123456789 has 3 lines")
    line = _line(led)
    assert "123456789" in line and "1790369089123" in line


def test_distinct_steps_still_tell_calls_apart():
    led = tl.new("g")
    tl.record_step(led, "lookup", {"ssn": SSN}, "ok")
    tl.record_step(led, "lookup", {"ssn": "987-65-4321"}, "ok")  # pragma: allowlist secret
    assert led["distinct_steps"] == 2


def test_the_step_in_flight_is_redacted_too(monkeypatch):
    saved = {}
    led = tl.new("g")
    monkeypatch.setattr(tl, "load", lambda tid: led)
    monkeypatch.setattr(tl, "save", lambda tid, l: saved.setdefault("l", l))
    tl.note_pending("t1", "send_payment", {"card": CARD, "invoice": "INV-77"})
    assert CARD not in led["pending"]["args"] and "INV-77" in led["pending"]["args"]

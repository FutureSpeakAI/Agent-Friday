"""A tool-output envelope's id is never read as a phone number.

Every message to a cloud model goes through the outbound PII scrub (core._scrub_pii). The
envelope's id is the first 16 hex digits of a SHA-256, and ten of them that were all digits,
the first one 2-9, read there as a US phone number: the header and the close reached the
model as ``[PII:phone:...]`` instead of the id (tests/unit/test_switch_seat_and_local_handoff.py
failed on it about once in a hundred runs). The input below is found by search, so the
all-digit case is met every time.
"""
from __future__ import annotations

import hashlib
import re

from agent_friday import core
from agent_friday.services import untrusted_input as ui

PHONE_LIKE = re.compile(r"(?<!\d)[2-9]\d{9}(?!\d)")


def _plain_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _text_whose_plain_id_is_phone_like() -> str:
    for n in range(200000):
        text = "result %d" % n
        if PHONE_LIKE.search(_plain_id(text)):
            return text
    raise AssertionError("no phone-like hash prefix in 200000 tries")


def test_the_premise_a_plain_hash_prefix_can_be_scrubbed():
    text = _text_whose_plain_id_is_phone_like()
    assert "[PII:phone" in core._scrub_pii("id " + _plain_id(text))[0]


def test_an_envelope_id_is_never_phone_like_and_survives_the_scrub():
    text = _text_whose_plain_id_is_phone_like()
    wrapped = ui.wrap(text)
    eid = ui._envelope_id(text)
    assert eid != _plain_id(text)
    assert not PHONE_LIKE.search(eid) and len(eid) == 16
    assert core._scrub_pii(wrapped)[0] == wrapped, "the scrub changed the envelope"


def test_the_id_is_stable_and_wrapping_a_wrapped_text_changes_nothing():
    text = _text_whose_plain_id_is_phone_like()
    once = ui.wrap(text)
    assert ui.wrap(text) == once and ui.wrap(once) == once
    assert ui.split(once) == (text, True)


def test_an_ordinary_id_is_the_plain_hash_prefix():
    assert ui._envelope_id("hello") == _plain_id("hello")

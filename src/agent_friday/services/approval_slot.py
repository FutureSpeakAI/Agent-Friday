"""Which approval cards wait for one subject: ids only (NS-41.3-1, SC D5.0 P5 and V12).

An approval_slot in a Liquid UI document names a subject and nothing else. The shell asks
this module for the ids and mounts its own card component for each; no card text passes
through here. Design: program/specs/DESIGN_NS-41.3-1_approval_slot_route_2026-11-16.md.

Rules and the tests that hold them (tests/unit/test_approval_slot.py, tests/api/test_approval_slot_route.py):
- Only pending cards for the exact subject (and kind, when given) are listed:
  test_only_pending_cards_for_the_exact_subject_are_listed.
- At most MAX_IDS, oldest first: test_more_than_the_cap_gives_the_oldest.
- A malformed subject or kind is refused with a closed code, never echoed:
  test_a_malformed_subject_is_refused_without_an_echo.
- Ids only, no card text: test_the_answer_holds_only_ids_and_no_card_text.
- Reading writes nothing: test_reading_the_slot_writes_nothing.
"""
from __future__ import annotations

import re
from typing import List, Optional

from agent_friday.services import approvals

MAX_IDS = 20
_SUBJECT = re.compile(r"^([a-z_]{1,32}):([A-Za-z0-9_.-]{1,80})$")
_KIND = re.compile(r"^[a-z0-9_.-]{1,64}$")


class BadSlot(ValueError):
    """`code` is one of bad_subject, bad_kind: a fixed word, never the caller's text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_subject(subject) -> tuple:
    match = _SUBJECT.fullmatch(subject) if isinstance(subject, str) else None
    if not match:
        raise BadSlot("bad_subject")
    return match.group(1), match.group(2)


def pending_ids(subject, kind: Optional[str] = None) -> List[str]:
    subject_type, subject_id = parse_subject(subject)
    if kind is not None and not (isinstance(kind, str) and _KIND.fullmatch(kind)):
        raise BadSlot("bad_kind")
    cards = [c for c in approvals.list_approvals(status="pending", subject_type=subject_type, kind=kind)
             if c.get("subject_id") == subject_id and c.get("lifecycle") == "pending"]
    cards.sort(key=lambda c: c.get("created_at") or 0)
    return [c["approval_id"] for c in cards[:MAX_IDS]]

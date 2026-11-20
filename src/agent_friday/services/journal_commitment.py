"""The detached journal commitment for a posting group (roadmap section 40, NS-40.4-6). TEST
CREDITS ONLY.

The journal keeps signed deltas. For exchange, a group becomes records with exactly `account`,
`direction` (debit for a negative delta, credit for a positive one) and `amount` (a positive safe
integer), one per account, sorted by ASCII account id, zero lines omitted. The commitment is the
SHA-256 of those records in the v2 canonical encoding. The discriminator
`asimov-issuer-entry/v1` and the envelope's record_hash binding belong to the envelope, not to
the hashed bytes; no bundle vector pins this digest yet, so only the records are pinned (by the
bundle's detached_posting vector) and the digest is pinned here by its construction.

Rules, each with the test that enforces it (tests/unit/test_journal_commitment.py):
  records match the bundle's detached_posting vector:
      test_the_records_match_the_bundles_detached_posting_vector
  records are sorted by account with zero lines omitted:
      test_records_are_sorted_and_zero_lines_are_omitted
  a group that is not zero-sum, not whole, or out of bounds is refused:
      test_an_unbalanced_fractional_or_out_of_bounds_group_is_refused
  a non-ASCII or empty account id is refused:
      test_a_non_ascii_or_empty_account_is_refused
  the digest is the SHA-256 of the canonical records and ignores input order:
      test_the_digest_is_the_canonical_hash_and_ignores_input_order
  a changed amount or account changes the digest:
      test_any_change_to_the_group_changes_the_digest
  imports only the standard library and the wire module:
      test_the_module_imports_only_the_standard_library_and_the_wire_module
"""
from __future__ import annotations

import hashlib

from agent_friday.services import peer_wire_v2 as wire

MAX_UNITS = 2**53 - 1


class NotCommittable(ValueError):
    """The posting group cannot be turned into a commitment."""


def _check(postings: dict) -> None:
    if not postings:
        raise NotCommittable("empty group")
    for account, delta in postings.items():
        if not isinstance(account, str) or not account.isascii() or not account.isprintable():
            raise NotCommittable("account ids are printable ASCII")
        if not account:
            raise NotCommittable("account ids are not empty")
        if type(delta) is not int or abs(delta) > MAX_UNITS:
            raise NotCommittable("amounts are whole safe integers")
    if sum(postings.values()) != 0:
        raise NotCommittable("the group is not zero-sum")


def records(postings: dict) -> list[dict]:
    _check(postings)
    return [
        {"account": account, "direction": "credit" if delta > 0 else "debit", "amount": abs(delta)}
        for account, delta in sorted(postings.items())
        if delta != 0
    ]


def commitment(postings: dict) -> str:
    return hashlib.sha256(wire.canonical(records(postings))).hexdigest()

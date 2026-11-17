"""Checking a signed economic schedule as signed (NS-40.4-9, NS-40.4-10). TEST CREDITS ONLY.

A schedule lists, per award, the whole-unit rows each beneficiary gets. The verifier checks the
ARITHMETIC OF THE ROWS AS SIGNED and never rebuilds a split from weights: a contributor split that the
largest-weight rule would not have produced still verifies if its rows add up, because the rows are
what was signed. (`issuer_journal.allocate` is for the party that writes a schedule, not for this.)

Rules and the tests that hold them (tests/unit/test_economic_schedule.py):
- The bundle's schedule and hash pass: test_the_bundle_schedule_and_its_hash_pass.
- Rows are sorted and unique, add up to the award's gross, and no beneficiary's units fall from one
  award to the next: test_each_kind_of_broken_schedule_is_named.
- The operator takes at most the cumulative floor of its basis points (never over 1%); contributors
  get the rest: test_the_operator_never_takes_more_than_the_cumulative_floor.
- Signed rows are honoured, not rebuilt: test_signed_rows_are_honoured_not_rebuilt_from_weights and
  test_the_verifier_never_calls_the_allocator.
- A later revision keeps every earlier award exactly:
  test_a_later_revision_never_takes_a_unit_from_a_signed_row.
"""
from __future__ import annotations

import hashlib
import json

from agent_friday.services.issuer_journal import MAX_UNITS, operator_share

PROTOCOL = "asimov-economic-schedule/v1"
OPERATOR_ROLE = "operator"


def schedule_hash(schedule: dict) -> str:
    encoded = json.dumps(schedule, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def verify_schedule(schedule) -> list:
    """Every problem found, in plain words; an empty list means the rows hold as signed."""
    if not isinstance(schedule, dict) or schedule.get("protocol") != PROTOCOL:
        return ["this is not an economic schedule"]
    bps = schedule.get("operator_bps")
    awards = schedule.get("awards")
    if type(bps) is not int or not 0 <= bps <= 100:
        return ["the operator's basis points must be a whole number from 0 to 100"]
    if not isinstance(awards, list) or not awards:
        return ["a schedule has at least one award"]
    problems, previous, previous_gross = [], None, 0
    for number, award in enumerate(awards, 1):
        label = f"award {number}"
        rows = award.get("shares") if isinstance(award, dict) else None
        gross = award.get("gross_units") if isinstance(award, dict) else None
        if type(gross) is not int or not 0 <= gross <= MAX_UNITS or not isinstance(rows, list) or not rows:
            problems.append(f"{label}: gross units and rows must be whole numbers and a list")
            continue
        try:
            keys = [(r["beneficiary"], r["role"]) for r in rows]
            units = [r["units"] for r in rows]
        except (KeyError, TypeError):
            problems.append(f"{label}: a row needs a beneficiary, a role and units")
            continue
        if any(type(u) is not int or u < 0 or u > MAX_UNITS for u in units):
            problems.append(f"{label}: units are whole numbers, never negative")
            continue
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            problems.append(f"{label}: rows must be sorted and each beneficiary and role appear once")
        if sum(units) != gross:
            problems.append(f"{label}: the rows add up to {sum(units)}, not the gross {gross}")
        if gross < previous_gross:
            problems.append(f"{label}: the gross falls from {previous_gross} to {gross}")
        current = dict(zip(keys, units))
        if previous is not None:
            if current.keys() != previous.keys():
                problems.append(f"{label}: the beneficiaries differ from the award before")
            for key, before in previous.items():
                if current.get(key, 0) < before:
                    problems.append(f"{label}: {key[0]} ({key[1]}) loses units that an earlier award gave")
        operator = sum(u for (_, role), u in current.items() if role == OPERATOR_ROLE)
        if operator > operator_share(gross, bps):
            problems.append(f"{label}: the operator takes {operator}, over its cumulative floor of "
                            f"{operator_share(gross, bps)}")
        if (gross - operator) * 100 < gross * 99:
            problems.append(f"{label}: contributors receive under 99% of the gross")
        previous, previous_gross = current, gross
    return problems


def verify_revision(previous: dict, following: dict) -> list:
    """A later revision of the same schedule must keep every earlier award exactly as signed."""
    problems = verify_schedule(following)
    if verify_schedule(previous):
        return problems + ["the earlier schedule does not hold"]
    for field in ("issuer", "work_order", "job", "stage"):
        if previous.get(field) != following.get(field):
            problems.append(f"{field} changed between revisions")
    if type(following.get("revision")) is not int or following["revision"] <= previous["revision"]:
        problems.append("the revision must rise")
    kept = following.get("awards", [])[:len(previous["awards"])]
    if kept != previous["awards"]:
        problems.append("an award that was already signed was changed or removed")
    return problems

"""Checking a signed economic schedule as signed (NS-40.4-9, NS-40.4-10). TEST CREDITS ONLY.

A schedule lists, per award, the whole-unit rows each beneficiary gets. The verifier checks the shape the bundle's
economic-schedule-v1 schema defines FIRST (closed fields, identifying fields present, patterns, enum roles,
counts), then the ARITHMETIC OF THE ROWS AS SIGNED, and never rebuilds a split from weights: a contributor split
that the largest-weight rule would not have produced still verifies if its rows add up, because the rows are what
was signed. (`issuer_journal.allocate` is for the party that writes a schedule, not for this.)

It is a verifier of signed peer data, so it never raises on input: every problem is a named string in a list.
Checking the full award against the wire `terms.shares` and `terms.gross_units`, and `stop_awards` against the
rows, needs the terms and the policy and is left to the caller (services/job_terms.py does the first).

Rules and the tests that hold them (tests/unit/test_economic_schedule.py):
- The bundle's schedule and hash pass: test_the_bundle_schedule_and_its_hash_pass.
- The schedule names its protocol, issuer, work order, job, stage and revision, and the schema is closed:
  test_the_spec_rules_that_were_not_checked_are_checked.
- Rows are sorted and unique, add up to the award's gross, and no beneficiary's units fall from one award to the
  next; cumulative gross STRICTLY increases and milestone ids are unique:
  test_each_kind_of_broken_schedule_is_named, test_the_spec_rules_that_were_not_checked_are_checked.
- A zero-price volunteer stage is one award of gross zero with empty shares: test_a_zero_price_volunteer_stage_is_valid.
- The operator takes at most the cumulative floor of its basis points (never over 1%); contributors get the rest:
  test_the_operator_never_takes_more_than_the_cumulative_floor.
- Signed rows are honoured, not rebuilt: test_signed_rows_are_honoured_not_rebuilt_from_weights and
  test_the_verifier_never_calls_the_allocator.
- A later revision keeps every earlier award exactly: test_a_later_revision_never_takes_a_unit_from_a_signed_row.
- It never raises on peer input: test_a_verifier_of_signed_peer_data_never_raises.
- The hash is over the Federation canonical bytes: test_the_hash_is_over_the_federation_canonical_bytes_not_a_serializer_of_its_own.
- Roles are a closed set: test_roles_are_a_closed_set.
"""
from __future__ import annotations

import hashlib
import re
from enum import Enum

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.issuer_journal import MAX_UNITS, operator_share

PROTOCOL = "asimov-economic-schedule/v1"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
_FIELDS = {"protocol", "issuer", "work_order", "job", "stage", "revision", "operator_bps", "awards"}
_AWARD_FIELDS = {"milestone", "gross_units", "shares"}
_ROW_FIELDS = {"beneficiary", "role", "units"}
MAX_AWARDS, MAX_ROWS = 65, 128
_FAILURES = (TypeError, ValueError, KeyError, IndexError, AttributeError, RecursionError, OverflowError)


class Role(str, Enum):
    CREATOR = "creator"
    IMPROVER = "improver"
    WORKER = "worker"
    COMPUTE = "compute"
    REVIEWER = "reviewer"
    OPERATOR = "operator"
    OTHER = "other"


_ROLES = {r.value for r in Role}


def schedule_hash(schedule: dict) -> str:
    """SHA-256 of the Federation canonical bytes (peer_wire_v2.canonical). It raises WireError for anything that form
    refuses (a float, a negative number, a non-ASCII string): such a schedule has no hash."""
    return hashlib.sha256(wire.canonical(schedule)).hexdigest()


def _whole(value, high=MAX_UNITS) -> bool:
    return type(value) is int and 0 <= value <= high


def _name(value) -> bool:
    return isinstance(value, str) and bool(_NAME.fullmatch(value))


def _shape(schedule) -> list:
    """The bundle schema's rules, as plain problems. Empty means the document has the shape the rest may rely on."""
    if not isinstance(schedule, dict) or set(schedule) != _FIELDS:
        return ["this is not an economic schedule (the fields must be exactly the schema's)"]
    problems = []
    if schedule["protocol"] != PROTOCOL:
        problems.append("this is not an economic schedule (wrong protocol)")
    for field in ("issuer", "work_order", "job", "stage"):
        if not _name(schedule[field]):
            problems.append(f"{field} must be a short plain identifier")
    if not _whole(schedule["revision"]):
        problems.append("the revision must be a whole number")
    if not _whole(schedule["operator_bps"], 100):
        problems.append("the operator's basis points must be a whole number from 0 to 100")
    awards = schedule["awards"]
    if not isinstance(awards, list) or not 1 <= len(awards) <= MAX_AWARDS:
        problems.append(f"a schedule has from 1 to {MAX_AWARDS} awards")
        return problems
    for number, award in enumerate(awards, 1):
        label = f"award {number}"
        if not isinstance(award, dict) or set(award) != _AWARD_FIELDS:
            problems.append(f"{label}: must have exactly a milestone, a gross and shares")
            continue
        if not _name(award["milestone"]):
            problems.append(f"{label}: the milestone must be a short plain identifier")
        if not _whole(award["gross_units"]):
            problems.append(f"{label}: gross units must be a whole number")
        rows = award["shares"]
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            problems.append(f"{label}: shares must be a list of at most {MAX_ROWS} rows")
            continue
        for row in rows:
            if (not isinstance(row, dict) or set(row) != _ROW_FIELDS or not _name(row["beneficiary"])
                    or not isinstance(row["role"], str) or row["role"] not in _ROLES or not _whole(row["units"])):
                problems.append(f"{label}: every row needs a plain beneficiary, a known role and whole units")
                break
    return problems


def verify_schedule(schedule) -> list:
    """Every problem found, in plain words; an empty list means the rows hold as signed. Never raises."""
    try:
        return _verify(schedule)
    except _FAILURES:
        return ["the schedule is malformed"]


def _verify(schedule) -> list:
    problems = _shape(schedule)
    if problems:
        return problems
    bps = schedule["operator_bps"]
    awards = schedule["awards"]
    seen_milestones = set()
    previous, previous_gross = None, None
    for number, award in enumerate(awards, 1):
        label = f"award {number}"
        gross, rows = award["gross_units"], award["shares"]
        if award["milestone"] in seen_milestones:
            problems.append(f"{label}: the milestone id is used twice")
        seen_milestones.add(award["milestone"])
        keys = [(r["beneficiary"], r["role"]) for r in rows]
        units = [r["units"] for r in rows]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            problems.append(f"{label}: rows must be sorted and each beneficiary and role appear once")
        if sum(units) != gross:
            problems.append(f"{label}: the rows add up to {sum(units)}, not the gross {gross}")
        if gross == 0 and (len(awards) != 1 or rows):
            problems.append(f"{label}: a zero gross is only the single volunteer award, with empty shares")
        if previous_gross is not None and gross <= previous_gross:
            problems.append(f"{label}: the cumulative gross must strictly increase "
                            f"(it goes from {previous_gross} to {gross})")
        current = dict(zip(keys, units))
        if previous is not None:
            if current.keys() != previous.keys():
                problems.append(f"{label}: the beneficiaries differ from the award before")
            for key, before in previous.items():
                if current.get(key, 0) < before:
                    problems.append(f"{label}: {key[0]} ({key[1]}) loses units that an earlier award gave")
        operator = sum(u for (_, role), u in current.items() if role == Role.OPERATOR.value)
        if operator > operator_share(gross, bps):
            problems.append(f"{label}: the operator takes {operator}, over its cumulative floor of "
                            f"{operator_share(gross, bps)}")
        if (gross - operator) * 100 < gross * 99:
            problems.append(f"{label}: contributors receive under 99% of the gross")
        previous, previous_gross = current, gross
    return problems


def verify_revision(previous, following) -> list:
    """A later revision of the same schedule must keep every earlier award exactly as signed. Never raises."""
    try:
        problems = verify_schedule(following)
        if verify_schedule(previous):
            return problems + ["the earlier schedule does not hold"]
        for field in ("issuer", "work_order", "job", "stage"):
            if previous.get(field) != following.get(field):
                problems.append(f"{field} changed between revisions")
        if not _whole(following.get("revision")) or following["revision"] <= previous["revision"]:
            problems.append("the revision must rise")
        kept = following.get("awards", [])[:len(previous["awards"])]
        if kept != previous["awards"]:
            problems.append("an award that was already signed was changed or removed")
        return problems
    except _FAILURES:
        return ["the schedule is malformed"]

"""Who is who in a paid job, and whether the money agrees (NS-40.3-1, NS-40.3-11; spec: Terms and consent,
Pilot job rules). TEST CREDITS ONLY: pure, nothing is signed, reserved or moved.

`check_participants` takes the signed terms' `participants` (seven keys, all required) and refuses a job whose
requester and provider are the same key, or whose reviewer or appeal authority is not distinct and
independent of both parties. (Keys can only show independence of KEY, not of operator or owner: a human
check of who sits behind a key stays a separate step.)

`reconcile` lays the signed terms, the detached settlement schedule, the policy's schedule hash, the payer's
reservation and the issuer's own reserved amount side by side and returns every disagreement: any mismatch
rejects the job. The shares must be the schedule's exact final allocation, the schedule must hold as signed
(services/economic_schedule), and the issuer's authoritative state must show exactly the reserved amount.

Rules and the tests that hold them (tests/unit/test_job_terms.py):
- Seven roles, all required, hex keys, none repeated by accident of format:
  test_the_bundle_participants_pass_and_a_malformed_set_is_refused.
- Requester and provider differ; reviewer and appeal are distinct and independent of both:
  test_the_separation_rules_hold_for_every_pair_of_roles.
- Matching terms, schedule, hash, reservation and issuer state reconcile; each single mismatch is named:
  test_a_matching_job_reconciles_and_each_single_mismatch_is_named.
- Every combination of two mismatches is also rejected, and nothing is repaired:
  test_any_combination_of_mismatches_is_rejected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from agent_friday.services import economic_schedule as schedule_mod

_HEX64 = re.compile(r"[a-f0-9]{64}")
ROLES = ("requester", "provider", "recorder", "reviewer", "appeal", "settlement", "release")


class TermsError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Participants:
    requester: str
    provider: str
    recorder: str
    reviewer: str
    appeal: str
    settlement: str
    release: str


def parse_participants(document) -> Participants:
    if not isinstance(document, dict) or set(document) != set(ROLES):
        raise TermsError("roles")
    if not all(isinstance(v, str) and _HEX64.fullmatch(v) for v in document.values()):
        raise TermsError("key")
    return Participants(**document)


def check_participants(p: Participants) -> None:
    """Raises TermsError with a closed code when the separation of roles does not hold."""
    if p.requester == p.provider:
        raise TermsError("requester_is_provider")
    if p.reviewer == p.appeal:
        raise TermsError("reviewer_is_appeal")
    for role in ("reviewer", "appeal"):
        if getattr(p, role) in (p.requester, p.provider):
            raise TermsError(f"{role}_is_a_party")


class Mismatch(str, Enum):
    SHARES_NOT_GROSS = "shares-do-not-add-up-to-the-gross"
    SCHEDULE_INVALID = "schedule-does-not-hold-as-signed"
    SCHEDULE_ISSUER = "schedule-names-another-issuer"
    SCHEDULE_HASH = "schedule-hash-differs"
    SCHEDULE_GROSS = "schedule-gross-differs-from-the-terms"
    SCHEDULE_SHARES = "shares-are-not-the-schedules-final-allocation"
    RESERVATION_ISSUER = "reservation-names-another-issuer"
    RESERVATION_SCOPE = "reservation-names-another-realm-or-stage"
    RESERVATION_AMOUNT = "reservation-is-not-the-gross"
    ISSUER_STATE = "issuer-state-does-not-show-the-reservation"


@dataclass(frozen=True)
class Reservation:
    issuer: str
    realm: str
    stage: str
    amount: int


def _rows(shares) -> list:
    return sorted((s["beneficiary"], s["role"], s["units"]) for s in shares)


def reconcile(terms: dict, schedule: dict, declared_schedule_hash: str, reservation: Reservation,
              issuer_reserved_units: int) -> list:
    """Every disagreement, in a fixed order; an empty list means the money agrees everywhere."""
    found = []
    shares = terms["shares"]
    if sum(s["units"] for s in shares) != terms["gross_units"]:
        found.append(Mismatch.SHARES_NOT_GROSS)
    if schedule_mod.verify_schedule(schedule):
        found.append(Mismatch.SCHEDULE_INVALID)
    else:
        final = schedule["awards"][-1]
        if schedule.get("issuer") != terms["issuer"]:
            found.append(Mismatch.SCHEDULE_ISSUER)
        if final["gross_units"] != terms["gross_units"]:
            found.append(Mismatch.SCHEDULE_GROSS)
        if _rows(final["shares"]) != _rows(shares):
            found.append(Mismatch.SCHEDULE_SHARES)
    if schedule_mod.schedule_hash(schedule) != declared_schedule_hash:
        found.append(Mismatch.SCHEDULE_HASH)
    if reservation.issuer != terms["issuer"]:
        found.append(Mismatch.RESERVATION_ISSUER)
    if (reservation.realm, reservation.stage) != (terms["realm"], terms["stage"]):
        found.append(Mismatch.RESERVATION_SCOPE)
    if reservation.amount != terms["gross_units"]:
        found.append(Mismatch.RESERVATION_AMOUNT)
    if issuer_reserved_units != reservation.amount:
        found.append(Mismatch.ISSUER_STATE)
    return found

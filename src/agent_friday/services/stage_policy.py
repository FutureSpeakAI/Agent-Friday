"""The machine-readable stage policy of a paid job (NS-40.3-3, NS-40.3-10, NS-40.6-1; spec: Detached
policy document and Pilot job rules). TEST CREDITS ONLY: this decides nothing and moves nothing.

`parse` takes the signed policy document (the bundle's stage-policy-v1 schema) and returns a frozen
`StagePolicy`, or raises `PolicyError` with a closed code. No default is filled in: a missing field is a
refusal, so there is no implicit developer-choice fallback.

Rules and the tests that hold them (tests/unit/test_stage_policy.py):
- The bundle's policy parses, and any unknown field, missing field, wrong type or bool-as-integer is refused:
  test_the_bundle_policy_parses_and_each_kind_of_bad_document_is_refused.
- Deadlines are absolute UTC instants in strictly increasing order (delivery, review, appeal):
  test_deadlines_must_strictly_increase.
- The award list is ordered, each cumulative amount no smaller than the one before, no milestone twice:
  test_stop_awards_are_ordered_cumulative_and_unique.
- A policy that names an independent reviewer path needs one in the terms, and an offer with no review
  deadline, no reviewer or no fallback is refused: test_an_offer_needs_a_review_deadline_a_reviewer_and_a_fallback.
- The policy's schedule hash must be the schedule's: test_the_policy_binds_the_exact_schedule.
- timeout_action is one of three closed actions and none of them can name a payment:
  test_a_timeout_action_can_only_be_one_of_the_closed_three.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

PROTOCOL = "asimov-stage-policy/v1"
MAX_INSTANT = 253402300799
MAX_UNITS = 2 ** 53 - 1
MAX_RETENTION = 315360000
_HEX64 = re.compile(r"[a-f0-9]{64}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
_ZONE = re.compile(r"[A-Za-z0-9_+./-]{1,64}")
_FIELDS = ("protocol", "economic_schedule_hash", "approval", "acceptance_mode", "evidence", "stop_awards",
           "delivery_deadline", "review_deadline", "appeal_deadline", "calendar_id", "timezone",
           "timeout_action", "artifact_retention_seconds")


class Approval(str, Enum):
    REQUESTER = "requester"
    INDEPENDENT_REVIEWER = "independent_reviewer"


class AcceptanceMode(str, Enum):
    DELIVERY = "delivery"
    SIGNED_ACCEPTANCE = "signed_acceptance"
    VERIFIED_TESTS = "verified_tests"
    INTEGRATION = "integration"
    REVIEW_DETERMINATION = "review_determination"


class TimeoutAction(str, Enum):
    """What a missed deadline may do. None of them grants a use or a payment the milestone lacks."""
    REFER_TO_REVIEWER = "refer_to_reviewer"
    HOLD_DISPUTED = "hold_disputed"
    CLOSE_WITHOUT_NEXT_STAGE = "close_without_next_stage"


class PolicyError(ValueError):
    """`code` is a fixed word; the document's own text is never echoed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StopAward:
    milestone: str
    cumulative_units: int


@dataclass(frozen=True)
class StagePolicy:
    economic_schedule_hash: str
    approval: Approval
    acceptance_mode: AcceptanceMode
    evidence: tuple
    stop_awards: tuple
    delivery_deadline: int
    review_deadline: int
    appeal_deadline: int
    calendar_id: str
    timezone: str
    timeout_action: TimeoutAction
    artifact_retention_seconds: int


def _whole(value, low, high, code):
    if type(value) is not int or not low <= value <= high:
        raise PolicyError(code)
    return value


def _enum(kind, value, code):
    if not isinstance(value, str):
        raise PolicyError(code)
    try:
        return kind(value)
    except ValueError:
        raise PolicyError(code) from None


def parse(document) -> StagePolicy:
    if not isinstance(document, dict):
        raise PolicyError("not_a_policy")
    if set(document) != set(_FIELDS):
        raise PolicyError("fields")
    if document["protocol"] != PROTOCOL:
        raise PolicyError("protocol")
    digest = document["economic_schedule_hash"]
    if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
        raise PolicyError("schedule_hash")
    approval = _enum(Approval, document["approval"], "approval")
    mode = _enum(AcceptanceMode, document["acceptance_mode"], "acceptance_mode")
    evidence = document["evidence"]
    if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 64 or len(set(map(str, evidence))) != len(evidence)
            or not all(isinstance(e, str) and _HEX64.fullmatch(e) for e in evidence)):
        raise PolicyError("evidence")
    awards = document["stop_awards"]
    if not isinstance(awards, list) or len(awards) > 64:
        raise PolicyError("stop_awards")
    parsed, seen, floor = [], set(), 0
    for item in awards:
        if not isinstance(item, dict) or set(item) != {"milestone", "cumulative_units"}:
            raise PolicyError("stop_awards")
        name = item["milestone"]
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in seen:
            raise PolicyError("stop_awards")
        units = _whole(item["cumulative_units"], 0, MAX_UNITS, "stop_awards")
        if units < floor:
            raise PolicyError("stop_awards")
        seen.add(name)
        floor = units
        parsed.append(StopAward(name, units))
    delivery = _whole(document["delivery_deadline"], 0, MAX_INSTANT, "deadline")
    review = _whole(document["review_deadline"], 0, MAX_INSTANT, "deadline")
    appeal = _whole(document["appeal_deadline"], 0, MAX_INSTANT, "deadline")
    if not delivery < review < appeal:
        raise PolicyError("deadline_order")
    calendar, zone = document["calendar_id"], document["timezone"]
    if not isinstance(calendar, str) or not _NAME.fullmatch(calendar):
        raise PolicyError("calendar")
    if not isinstance(zone, str) or not _ZONE.fullmatch(zone):
        raise PolicyError("timezone")
    return StagePolicy(digest, approval, mode, tuple(evidence), tuple(parsed), delivery, review, appeal, calendar, zone,
                       _enum(TimeoutAction, document["timeout_action"], "timeout_action"),
                       _whole(document["artifact_retention_seconds"], 0, MAX_RETENTION, "retention"))


def check_against_terms(policy: StagePolicy, *, schedule_hash: str, has_independent_reviewer: bool,
                        has_fallback_procedure: bool) -> None:
    """What the offer's terms must also hold. An offer without an exact review deadline, an independent
    reviewer or a fallback review procedure is refused (NS-40.6-1); no default fills the gap."""
    if policy.economic_schedule_hash != schedule_hash:
        raise PolicyError("schedule_mismatch")
    if not has_independent_reviewer:
        raise PolicyError("no_independent_reviewer")
    if not has_fallback_procedure:
        raise PolicyError("no_fallback_procedure")

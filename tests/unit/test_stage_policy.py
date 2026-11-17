"""The signed stage policy of a paid job (NS-40.3-3, NS-40.3-10, NS-40.6-1). Test credits only."""
import copy
import json
from pathlib import Path

import pytest

from agent_friday.services import stage_policy as sp

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "wire-v2.json").read_text(encoding="utf-8"))["stage_policy"]
SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "schema"
                     / "stage-policy-v1.schema.json").read_text(encoding="utf-8"))


def doc(**over):
    d = copy.deepcopy(FIXTURE)
    d.update(over)
    return d


def code_of(document):
    with pytest.raises(sp.PolicyError) as e:
        sp.parse(document)
    return e.value.code


def test_the_bundle_policy_parses_and_each_kind_of_bad_document_is_refused():
    policy = sp.parse(FIXTURE)
    assert policy.approval is sp.Approval.REQUESTER and policy.acceptance_mode is sp.AcceptanceMode.VERIFIED_TESTS
    assert policy.stop_awards == (sp.StopAward("rehearsal", 20),) and policy.timeout_action is sp.TimeoutAction.REFER_TO_REVIEWER
    assert set(FIXTURE) == set(SCHEMA["required"]) == set(SCHEMA["properties"]), "the parser follows the bundle schema"
    missing = {k: v for k, v in FIXTURE.items() if k != "timeout_action"}
    bad = {
        "not a dict": ([], "not_a_policy"), "missing field": (missing, "fields"),
        "unknown field": (doc(extra=1), "fields"), "protocol": (doc(protocol="asimov-stage-policy/v2"), "protocol"),
        "hash": (doc(economic_schedule_hash="x" * 64), "schedule_hash"),
        "hash type": (doc(economic_schedule_hash=5), "schedule_hash"),
        "approval": (doc(approval="owner"), "approval"), "approval type": (doc(approval=None), "approval"),
        "mode": (doc(acceptance_mode="vibes"), "acceptance_mode"),
        "evidence empty": (doc(evidence=[]), "evidence"), "evidence short": (doc(evidence=["ab"]), "evidence"),
        "evidence dup": (doc(evidence=["a" * 64, "a" * 64]), "evidence"),
        "evidence big": (doc(evidence=[f"{i:064x}" for i in range(65)]), "evidence"),
        "calendar": (doc(calendar_id=" "), "calendar"), "timezone": (doc(timezone="UTC time"), "timezone"),
        "timeout": (doc(timeout_action="pay_anyway"), "timeout_action"),
        "retention": (doc(artifact_retention_seconds=315360001), "retention"),
        "retention bool": (doc(artifact_retention_seconds=True), "retention"),
        "deadline float": (doc(delivery_deadline=1.5), "deadline"),
        "deadline bool": (doc(delivery_deadline=True), "deadline"),
        "deadline big": (doc(appeal_deadline=253402300800), "deadline"),
        "deadline negative": (doc(delivery_deadline=-1), "deadline"),
    }
    for name, (document, code) in bad.items():
        assert code_of(document) == code, name


def test_deadlines_must_strictly_increase():
    base = FIXTURE["delivery_deadline"]
    for d, r, a in ((100, 100, 200), (100, 200, 200), (200, 100, 300), (300, 200, 100), (100, 300, 200)):
        assert code_of(doc(delivery_deadline=d, review_deadline=r, appeal_deadline=a)) == "deadline_order", (d, r, a)
    assert sp.parse(doc(delivery_deadline=base, review_deadline=base + 1, appeal_deadline=base + 2))


def test_stop_awards_are_ordered_cumulative_and_unique():
    ok = [{"milestone": "a", "cumulative_units": 5}, {"milestone": "b", "cumulative_units": 5},
          {"milestone": "c", "cumulative_units": 9}]
    assert [a.cumulative_units for a in sp.parse(doc(stop_awards=ok)).stop_awards] == [5, 5, 9]
    assert sp.parse(doc(stop_awards=[])).stop_awards == ()
    for awards in ([{"milestone": "a", "cumulative_units": 9}, {"milestone": "b", "cumulative_units": 5}],
                   [{"milestone": "a", "cumulative_units": 1}, {"milestone": "a", "cumulative_units": 2}],
                   [{"milestone": "a", "cumulative_units": -1}], [{"milestone": "a", "cumulative_units": 1.0}],
                   [{"milestone": "a", "cumulative_units": 2 ** 53}], [{"milestone": "", "cumulative_units": 1}],
                   [{"milestone": "a"}], [{"milestone": "a", "cumulative_units": 1, "x": 1}], "a",
                   [{"milestone": "a", "cumulative_units": 1}] * 2, [{"milestone": str(i), "cumulative_units": i}
                                                                     for i in range(65)]):
        assert code_of(doc(stop_awards=awards)) == "stop_awards", awards


def test_a_timeout_action_can_only_be_one_of_the_closed_three():
    assert {a.value for a in sp.TimeoutAction} == {"refer_to_reviewer", "hold_disputed", "close_without_next_stage"}
    for value in ("refer_to_reviewer", "hold_disputed", "close_without_next_stage"):
        assert sp.parse(doc(timeout_action=value)).timeout_action.value == value
    for value in ("grant_publication", "pay", "extend", "", None, 3):
        assert code_of(doc(timeout_action=value)) == "timeout_action"


def test_the_policy_binds_the_exact_schedule():
    policy = sp.parse(FIXTURE)
    sp.check_against_terms(policy, schedule_hash=FIXTURE["economic_schedule_hash"], has_independent_reviewer=True,
                           has_fallback_procedure=True)
    with pytest.raises(sp.PolicyError) as e:
        sp.check_against_terms(policy, schedule_hash="0" * 64, has_independent_reviewer=True, has_fallback_procedure=True)
    assert e.value.code == "schedule_mismatch"


def test_an_offer_needs_a_review_deadline_a_reviewer_and_a_fallback():
    policy = sp.parse(FIXTURE)
    h = FIXTURE["economic_schedule_hash"]
    for reviewer, fallback, code in ((False, True, "no_independent_reviewer"), (True, False, "no_fallback_procedure")):
        with pytest.raises(sp.PolicyError) as e:
            sp.check_against_terms(policy, schedule_hash=h, has_independent_reviewer=reviewer, has_fallback_procedure=fallback)
        assert e.value.code == code
    no_review_deadline = {k: v for k, v in FIXTURE.items() if k != "review_deadline"}
    assert code_of(no_review_deadline) == "fields", "no default fills a missing review deadline"


def test_an_error_never_echoes_the_document():
    canary = "CANARY-" + "z" * 40
    for document in (doc(approval=canary), doc(calendar_id=canary + " "), doc(economic_schedule_hash=canary),
                     doc(timeout_action=canary)):
        assert canary not in str(code_of(document))

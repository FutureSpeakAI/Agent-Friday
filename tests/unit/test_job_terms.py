"""Who is who in a paid job and whether the money agrees (NS-40.3-1, NS-40.3-11). Pure; test credits only."""
import copy
import itertools
import json
from pathlib import Path

import pytest

from agent_friday.services import economic_schedule as es
from agent_friday.services import job_terms as jt

FIX = Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
WIRE = json.loads((FIX / "wire-v2.json").read_text(encoding="utf-8"))
ECON = json.loads((FIX / "economy-vectors.json").read_text(encoding="utf-8"))
OFFER_TERMS = next(c["envelope"]["body"]["payload"]["terms"] for c in WIRE["cases"]
                   if c.get("envelope", {}).get("body", {}).get("type") == "job.offer")


def key(n):
    return f"{n:064x}"


GOOD = {role: key(i + 1) for i, role in enumerate(jt.ROLES)}


def good_terms():
    return {"issuer": "test.one", "realm": "realm-1", "job": "j.example", "stage": "design", "gross_units": 100,
            "shares": [{"beneficiary": "editor", "role": "creator", "units": 99},
                       {"beneficiary": "operator", "role": "operator", "units": 1}]}


def world():
    schedule = copy.deepcopy(ECON["schedule"])
    return dict(terms=good_terms(), schedule=schedule, declared=es.schedule_hash(schedule),
                reservation=jt.Reservation("test.one", "realm-1", "design", 100), issuer_units=100)


def run(w):
    return jt.reconcile(w["terms"], w["schedule"], w["declared"], w["reservation"], w["issuer_units"])


def test_the_bundle_participants_pass_and_a_malformed_set_is_refused():
    p = jt.parse_participants(OFFER_TERMS["participants"])
    jt.check_participants(p)
    assert len({getattr(p, r) for r in jt.ROLES}) == 7
    missing = {k: v for k, v in GOOD.items() if k != "release"}
    for bad in (missing, {**GOOD, "extra": key(9)}, {**GOOD, "requester": "abc"}, {**GOOD, "provider": "A" * 64},
                {**GOOD, "recorder": 5}, {**GOOD, "appeal": None}, [], None, "x"):
        with pytest.raises(jt.TermsError):
            jt.parse_participants(bad)


def test_the_separation_rules_hold_for_every_pair_of_roles():
    forbidden = {frozenset(p) for p in (("requester", "provider"), ("reviewer", "appeal"), ("reviewer", "requester"),
                                       ("reviewer", "provider"), ("appeal", "requester"), ("appeal", "provider"))}
    refused = 0
    for a, b in itertools.combinations(jt.ROLES, 2):
        same = dict(GOOD)
        same[b] = same[a]
        participants = jt.parse_participants(same)
        if frozenset((a, b)) in forbidden:
            with pytest.raises(jt.TermsError):
                jt.check_participants(participants)
            refused += 1
        else:
            jt.check_participants(participants)
    assert refused == len(forbidden) == 6


def test_the_separation_errors_are_closed_codes():
    codes = set()
    for a, b in itertools.combinations(jt.ROLES, 2):
        same = dict(GOOD)
        same[b] = same[a]
        try:
            jt.check_participants(jt.parse_participants(same))
        except jt.TermsError as e:
            codes.add(e.code)
    assert codes == {"requester_is_provider", "reviewer_is_appeal", "reviewer_is_a_party", "appeal_is_a_party"}


def test_a_matching_job_reconciles_and_each_single_mismatch_is_named():
    assert run(world()) == []
    cases = {}

    def edit(name, fn, code):
        w = world()
        fn(w)
        cases[name] = (w, code)

    M = jt.Mismatch
    edit("shares", lambda w: w["terms"]["shares"][0].update(units=98), M.SHARES_NOT_GROSS)
    edit("schedule broken", lambda w: w["schedule"]["awards"][0]["shares"][0].update(units=9), M.SCHEDULE_INVALID)
    edit("schedule issuer", lambda w: w["schedule"].update(issuer="other"), M.SCHEDULE_ISSUER)
    edit("hash", lambda w: w.update(declared="0" * 64), M.SCHEDULE_HASH)
    edit("gross", lambda w: w["terms"].update(gross_units=101), M.SCHEDULE_GROSS)
    edit("final allocation", lambda w: w["terms"].update(shares=[
        {"beneficiary": "editor", "role": "creator", "units": 98}, {"beneficiary": "operator", "role": "operator", "units": 2}]),
         M.SCHEDULE_SHARES)
    edit("reservation issuer", lambda w: w.update(reservation=jt.Reservation("other", "realm-1", "design", 100)),
         M.RESERVATION_ISSUER)
    edit("reservation realm", lambda w: w.update(reservation=jt.Reservation("test.one", "realm-2", "design", 100)),
         M.RESERVATION_SCOPE)
    edit("reservation stage", lambda w: w.update(reservation=jt.Reservation("test.one", "realm-1", "stage-2", 100)),
         M.RESERVATION_SCOPE)
    edit("reservation amount", lambda w: w.update(reservation=jt.Reservation("test.one", "realm-1", "design", 99),
                                                  issuer_units=99), M.RESERVATION_AMOUNT)
    edit("issuer state", lambda w: w.update(issuer_units=0), M.ISSUER_STATE)
    edit("issuer state over", lambda w: w.update(issuer_units=101), M.ISSUER_STATE)
    for name, (w, code) in cases.items():
        found = run(w)
        assert code in found, (name, found)
    # the isolating ones name exactly one thing
    for name in ("hash", "reservation realm", "reservation stage", "issuer state", "issuer state over", "reservation issuer"):
        assert run(cases[name][0]) == [cases[name][1]], name


MUTATIONS = [
    lambda w: w["terms"]["shares"][0].update(units=98),
    lambda w: w["schedule"]["awards"][0]["shares"][0].update(units=9),
    lambda w: w.update(declared="0" * 64),
    lambda w: w["terms"].update(gross_units=101),
    lambda w: w.update(reservation=jt.Reservation("other", "realm-1", "design", 100)),
    lambda w: w.update(reservation=jt.Reservation("test.one", "realm-2", "design", 100)),
    lambda w: w.update(reservation=jt.Reservation("test.one", "realm-1", "design", 99)),
    lambda w: w.update(issuer_units=7),
]


def test_any_combination_of_mismatches_is_rejected():
    checked = 0
    for size in range(1, len(MUTATIONS) + 1):
        for chosen in itertools.combinations(range(len(MUTATIONS)), size):
            w = world()
            for i in chosen:
                MUTATIONS[i](w)
            before = copy.deepcopy((w["terms"], w["schedule"], w["declared"], w["issuer_units"]))
            assert run(w), chosen
            assert before == (w["terms"], w["schedule"], w["declared"], w["issuer_units"]), "nothing is repaired in place"
            checked += 1
    assert checked == 2 ** len(MUTATIONS) - 1


def test_a_schedule_that_does_not_hold_is_never_compared_further():
    w = world()
    w["schedule"]["awards"] = []
    assert jt.Mismatch.SCHEDULE_INVALID in run(w)


def test_the_terms_module_never_reaches_the_network_or_a_journal():
    import ast
    tree = ast.parse(Path(jt.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert imported <= {"__future__.annotations", "re", "dataclasses.dataclass", "enum.Enum",
                        "agent_friday.services.economic_schedule",
                        "agent_friday.services.peer_wire_v2"}, imported


def test_the_schedule_must_be_for_this_job_and_stage():
    """The clean case pairs the schedule with terms for the same job and stage; another job or stage is named."""
    w = world()
    assert (w["terms"]["job"], w["terms"]["stage"]) == (w["schedule"]["job"], w["schedule"]["stage"])
    for field in ("job", "stage"):
        w = world()
        w["schedule"][field] = "elsewhere"
        w["declared"] = es.schedule_hash(w["schedule"])
        assert jt.Mismatch.SCHEDULE_SCOPE in run(w), field
    w = world()
    w["terms"]["job"] = "j.other"
    assert run(w) == [jt.Mismatch.SCHEDULE_SCOPE]


@pytest.mark.parametrize("field,value", [("beneficiary", "someone-else"), ("role", "operator")])
def test_a_share_paid_to_the_wrong_beneficiary_or_role_is_named(field, value):
    w = world()
    w["terms"]["shares"][0][field] = value
    assert run(w) == [jt.Mismatch.SCHEDULE_SHARES]


@pytest.mark.parametrize("edit", [
    lambda w: w["terms"]["shares"][0].update(units=99.0),
    lambda w: w["terms"]["shares"][1].update(units=True),
    lambda w: w["terms"].update(gross_units=100.0),
    lambda w: w.update(reservation=jt.Reservation("test.one", "realm-1", "design", 100.0)),
    lambda w: w.update(issuer_units=100.0),
    lambda w: w["terms"]["shares"][0].update(units="99"),
], ids=["share-float", "share-bool", "gross-float", "reservation-float", "issuer-float", "share-text"])
def test_an_amount_that_is_not_a_whole_number_of_units_is_named(edit):
    w = world()
    edit(w)
    assert jt.Mismatch.UNITS_NOT_WHOLE in run(w)


@pytest.mark.parametrize("schedule", [{}, {"awards": [{"gross_units": 1.5}]}, "not a schedule"])
def test_a_schedule_with_no_hash_is_reported_not_raised(schedule):
    w = world()
    w["schedule"] = schedule
    found = run(w)
    assert jt.Mismatch.SCHEDULE_INVALID in found and jt.Mismatch.SCHEDULE_HASH in found

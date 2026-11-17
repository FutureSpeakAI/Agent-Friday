"""A signed economic schedule is checked as signed, never rebuilt from weights (NS-40.4-9/-10)."""
import ast
import copy
import json
from pathlib import Path

import pytest

from agent_friday.services import economic_schedule as es
from agent_friday.services import issuer_journal as ij

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "economy-vectors.json").read_text(encoding="utf-8"))


def schedule():
    return copy.deepcopy(VECTORS["schedule"])


def test_the_bundle_schedule_and_its_hash_pass():
    assert es.verify_schedule(schedule()) == []
    assert es.schedule_hash(schedule()) == VECTORS["schedule_hash"]
    changed = schedule()
    changed["revision"] = 2
    assert es.schedule_hash(changed) != VECTORS["schedule_hash"]


def mutate(fn):
    s = schedule()
    fn(s)
    return es.verify_schedule(s)


def test_each_kind_of_broken_schedule_is_named():
    def lose_units(s):
        s["awards"][0]["shares"].insert(0, {"beneficiary": "agent", "role": "creator", "units": 0})
        s["awards"][1]["shares"].insert(0, {"beneficiary": "agent", "role": "creator", "units": 95})
        s["awards"][1]["shares"][1].update(units=4)

    cases = {
        "adds up": (lambda s: s["awards"][0]["shares"][0].update(units=9), "add up to 9"),
        "unsorted": (lambda s: s["awards"][1]["shares"].reverse(), "sorted"),
        "duplicate": (lambda s: s["awards"][0]["shares"].append(dict(s["awards"][0]["shares"][0])), "once"),
        "loses units": (lose_units, "loses units"),
        "gross falls": (lambda s: s["awards"][1].update(gross_units=5), "strictly increase"),
        "other beneficiaries": (lambda s: s["awards"][1]["shares"].append(
            {"beneficiary": "zed", "role": "creator", "units": 0}), "beneficiaries differ"),
        "negative": (lambda s: s["awards"][0]["shares"][0].update(units=-1), "whole units"),
        "float": (lambda s: s["awards"][0]["shares"][0].update(units=10.0), "whole units"),
        "bool": (lambda s: s["awards"][0]["shares"][1].update(units=False), "whole units"),
        "no rows": (lambda s: s["awards"][0].update(shares=[]), "add up to 0"),
        "missing field": (lambda s: s["awards"][0]["shares"][0].pop("role"), "every row needs"),
        "no awards": (lambda s: s.update(awards=[]), "from 1 to"),
        "bad bps": (lambda s: s.update(operator_bps=101), "basis points"),
        "string bps": (lambda s: s.update(operator_bps="100"), "basis points"),
        "wrong protocol": (lambda s: s.update(protocol="other"), "not an economic schedule"),
    }
    for name, (fn, needle) in cases.items():
        problems = mutate(fn)
        assert any(needle in p for p in problems), (name, problems)
    assert mutate(lose_units) and [p for p in mutate(lose_units) if "loses units" not in p] == []
    assert es.verify_schedule(None) and es.verify_schedule([]) and es.verify_schedule("x")


def test_the_operator_never_takes_more_than_the_cumulative_floor():
    def greedy(s):
        s["awards"][1]["shares"][0].update(units=98)
        s["awards"][1]["shares"][1].update(units=2)
    problems = mutate(greedy)
    assert any("cumulative floor" in p for p in problems) and any("99%" in p for p in problems)

    def waived(s):
        s["awards"][1]["shares"][0].update(units=100)
        s["awards"][1]["shares"][1].update(units=0)
    assert mutate(waived) == [], "a waived fee returns to the contributors"
    assert any("cumulative floor" in p for p in mutate(lambda s: s.update(operator_bps=0)))


def test_signed_rows_are_honoured_not_rebuilt_from_weights():
    s = schedule()
    s["awards"][0]["shares"] = [{"beneficiary": "agent", "role": "creator", "units": 3},
                                {"beneficiary": "editor", "role": "creator", "units": 7},
                                {"beneficiary": "operator", "role": "operator", "units": 0}]
    s["awards"][1]["shares"] = [{"beneficiary": "agent", "role": "creator", "units": 3},
                                {"beneficiary": "editor", "role": "creator", "units": 96},
                                {"beneficiary": "operator", "role": "operator", "units": 1}]
    weighted = ij.allocate({"agent": 1, "editor": 4}, 10)
    assert weighted != {"agent": 3, "editor": 7}, "the allocation rule would have split this differently"
    assert es.verify_schedule(s) == []


def test_the_verifier_never_calls_the_allocator():
    tree = ast.parse(Path(es.__file__).read_text(encoding="utf-8"))
    names = {getattr(n.func, "id", getattr(n.func, "attr", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "allocate" not in names and "match_lots" not in names
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "allocate" not in imported


def test_a_later_revision_never_takes_a_unit_from_a_signed_row():
    first = schedule()
    second = schedule()
    second["revision"] = 2
    second["awards"].append({"milestone": "bonus", "gross_units": 200, "shares": [
        {"beneficiary": "editor", "role": "creator", "units": 198},
        {"beneficiary": "operator", "role": "operator", "units": 2}]})
    assert es.verify_revision(first, second) == []
    edited = copy.deepcopy(second)
    edited["awards"][0]["shares"][0]["units"] = 9
    edited["awards"][0]["shares"][1]["units"] = 1
    assert any("already signed" in p for p in es.verify_revision(first, edited))
    removed = copy.deepcopy(second)
    removed["awards"] = removed["awards"][1:]
    assert any("already signed" in p for p in es.verify_revision(first, removed))
    same = copy.deepcopy(second)
    same["revision"] = 1
    assert any("revision must rise" in p for p in es.verify_revision(first, same))
    other = copy.deepcopy(second)
    other["job"] = "other"
    assert any("job changed" in p for p in es.verify_revision(first, other))
    assert es.verify_revision({"protocol": "x"}, second)


# ── second-review findings (PR #920), written red first ───────────────────────────────────────────

def award(milestone, gross, *rows):
    return {"milestone": milestone, "gross_units": gross, "shares": [
        {"beneficiary": b, "role": r, "units": u} for b, r, u in rows]}


def test_a_zero_price_volunteer_stage_is_valid():
    """Finding 1: the spec defines empty shares and one terminal award totalling zero."""
    volunteer = schedule()
    volunteer["awards"] = [award("done", 0)]
    assert es.verify_schedule(volunteer) == []
    for bad in ([award("done", 0), award("more", 5, ("editor", "creator", 5))],
                [award("a", 5, ("editor", "creator", 5)), award("done", 0)],
                [award("done", 0, ("editor", "creator", 0))]):
        broken = schedule()
        broken["awards"] = bad
        assert es.verify_schedule(broken), bad


def test_the_spec_rules_that_were_not_checked_are_checked():
    """Finding 2: cumulative gross strictly increases, milestone ids are unique, and the schedule names itself."""
    same_gross = schedule()
    same_gross["awards"].append(copy.deepcopy(same_gross["awards"][-1]))
    same_gross["awards"][-1]["milestone"] = "other"
    assert any("strictly" in p for p in es.verify_schedule(same_gross)), es.verify_schedule(same_gross)
    duplicate = schedule()
    duplicate["awards"][1]["milestone"] = duplicate["awards"][0]["milestone"]
    assert any("milestone" in p for p in es.verify_schedule(duplicate))
    for field in ("milestone",):
        missing = schedule()
        del missing["awards"][0][field]
        assert es.verify_schedule(missing)
    for field in ("issuer", "work_order", "job", "stage", "revision", "protocol"):
        gone = schedule()
        del gone[field]
        assert es.verify_schedule(gone), field
    for field, value in (("issuer", ""), ("job", "a b"), ("stage", 5), ("work_order", None), ("revision", -1),
                         ("revision", True), ("revision", 1.5)):
        bad = schedule()
        bad[field] = value
        assert es.verify_schedule(bad), (field, value)
    extra = schedule()
    extra["note"] = "x"
    assert es.verify_schedule(extra), "the schema is closed"
    bad_role = schedule()
    bad_role["awards"][0]["shares"][0]["role"] = "boss"
    assert es.verify_schedule(bad_role)
    too_many = schedule()
    too_many["awards"] = [award(f"m{i}", i + 1, ("a", "creator", i + 1)) for i in range(66)]
    assert es.verify_schedule(too_many)


def test_a_verifier_of_signed_peer_data_never_raises():
    """Finding 3: a missing revision and a beneficiary that is a number beside text both raised."""
    first = schedule()
    no_revision = schedule()
    del no_revision["revision"]
    assert es.verify_revision(first, no_revision) and es.verify_revision(no_revision, first)
    mixed = schedule()
    mixed["awards"][0]["shares"][0]["beneficiary"] = 7
    assert es.verify_schedule(mixed)
    import random
    rng = random.Random(920)
    garbage = [None, 0, -1, 2 ** 70, 1.5, True, [], {}, "", "x" * 300, [None], {"a": [1]}, ["a", 1]]
    for _ in range(600):
        s = schedule()
        target = rng.choice([s, s["awards"], s["awards"][0], s["awards"][0]["shares"], s["awards"][0]["shares"][0]])
        if isinstance(target, dict) and target:
            target[rng.choice(list(target))] = rng.choice(garbage)
        elif isinstance(target, list) and target:
            target[rng.randrange(len(target))] = rng.choice(garbage)
        assert isinstance(es.verify_schedule(s), list)
        assert isinstance(es.verify_revision(schedule(), s), list)
        assert isinstance(es.verify_revision(s, schedule()), list)
    for hostile in (None, [], "x", 5, {"awards": 5}, {"awards": [5]}, {"awards": [{"shares": 5}]}):
        assert es.verify_schedule(hostile) and es.verify_revision(hostile, hostile)


def test_the_hash_is_over_the_federation_canonical_bytes_not_a_serializer_of_its_own(monkeypatch):
    """Finding 4: a float hashed here where the canonical form raises."""
    import hashlib
    from agent_friday.services import peer_wire_v2 as wire
    seen = []
    real = wire.canonical
    monkeypatch.setattr(wire, "canonical", lambda item: seen.append(item) or real(item))
    assert es.schedule_hash(schedule()) == hashlib.sha256(real(schedule())).hexdigest() == VECTORS["schedule_hash"]
    assert seen, "schedule_hash must call the Federation canonical function"
    monkeypatch.undo()
    floaty = schedule()
    floaty["operator_bps"] = 1.0
    with pytest.raises(wire.WireError):
        es.schedule_hash(floaty)
    negative = schedule()
    negative["revision"] = -1
    with pytest.raises(wire.WireError):
        es.schedule_hash(negative)


def test_roles_are_a_closed_set():
    assert {r.value for r in es.Role} == {"creator", "improver", "worker", "compute", "reviewer", "operator", "other"}

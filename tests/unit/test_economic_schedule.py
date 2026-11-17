"""A signed economic schedule is checked as signed, never rebuilt from weights (NS-40.4-9/-10)."""
import ast
import copy
import json
from pathlib import Path

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
        "gross falls": (lambda s: s["awards"][1].update(gross_units=5), "gross falls"),
        "other beneficiaries": (lambda s: s["awards"][1]["shares"].append(
            {"beneficiary": "zed", "role": "creator", "units": 0}), "beneficiaries differ"),
        "negative": (lambda s: s["awards"][0]["shares"][0].update(units=-1), "never negative"),
        "float": (lambda s: s["awards"][0]["shares"][0].update(units=10.0), "whole numbers"),
        "bool": (lambda s: s["awards"][0]["shares"][1].update(units=False), "whole numbers"),
        "no rows": (lambda s: s["awards"][0].update(shares=[]), "whole numbers and a list"),
        "missing field": (lambda s: s["awards"][0]["shares"][0].pop("role"), "needs a beneficiary"),
        "no awards": (lambda s: s.update(awards=[]), "at least one award"),
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

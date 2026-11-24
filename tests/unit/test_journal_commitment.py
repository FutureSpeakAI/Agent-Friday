"""Tests for services/journal_commitment.py (NS-40.4-6); the rules are in its docstring."""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import re

import pytest

from agent_friday.services import journal_commitment as jc
from agent_friday.services import peer_wire_v2 as wire

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
VECTORS = json.loads((FIXTURES / "economy-vectors.json").read_text(encoding="utf-8"))
VECTOR = VECTORS["detached_posting"]


def test_the_records_match_the_bundles_detached_posting_vector():
    assert jc.records({"alice": -9, "bob": 9}) == VECTOR["journal"]


def test_records_are_sorted_and_zero_lines_are_omitted():
    out = jc.records({"zed": 3, "alice": -5, "mid": 0, "bob": 2})
    assert [r["account"] for r in out] == ["alice", "bob", "zed"]


@pytest.mark.parametrize(
    "postings",
    [{}, {"a": 1}, {"a": -1, "b": 2}, {"a": 1.5, "b": -1.5}, {"a": True, "b": -1},
     {"a": jc.MAX_UNITS + 1, "b": -(jc.MAX_UNITS + 1)}],
)
def test_an_unbalanced_fractional_or_out_of_bounds_group_is_refused(postings):
    with pytest.raises(jc.NotCommittable):
        jc.records(postings)


@pytest.mark.parametrize("account", ["", "café", "tab\there"])
def test_a_non_ascii_or_empty_account_is_refused(account):
    with pytest.raises(jc.NotCommittable):
        jc.records({account: 1, "b": -1})


def test_the_digest_is_the_canonical_hash_and_ignores_input_order():
    first = {"alice": -9, "bob": 9, "carol": 0}
    second = {"bob": 9, "alice": -9}
    expected = hashlib.sha256(wire.canonical(VECTOR["journal"])).hexdigest()
    assert jc.commitment(first) == jc.commitment(second) == expected


def test_any_change_to_the_group_changes_the_digest():
    base = jc.commitment({"alice": -9, "bob": 9})
    assert jc.commitment({"alice": -8, "bob": 8}) != base
    assert jc.commitment({"alice": -9, "carl": 9}) != base


def test_the_module_imports_only_the_standard_library_and_the_wire_module():
    tree = ast.parse(pathlib.Path(jc.__file__).read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"__future__", "hashlib", "agent_friday.services"}


def test_every_rule_in_the_docstring_names_a_test_that_exists():
    own = pathlib.Path(__file__)
    found = re.findall(r"(?<![a-z_/])(test_[a-z_0-9]+)", jc.__doc__)
    named = set(found) - {own.stem}
    body = ast.parse(own.read_text(encoding="utf-8")).body
    assert len(named) == 7 and named <= {n.name for n in body if isinstance(n, ast.FunctionDef)}

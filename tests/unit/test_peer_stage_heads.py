"""Tests for services/peer_stage_heads.py (NS-40.2-12); the rules are in its docstring."""
from __future__ import annotations

import ast
import itertools
import pathlib
import re

import pytest

from agent_friday.services import peer_stage_heads as psh
from agent_friday.services.peer_stage_heads import (
    GENESIS, Outcome, ReconcileRefusal, StageHeads, Transition,
)

T = Transition


def test_a_transition_on_the_current_head_advances_it():
    s = StageHeads()
    assert s.apply(T(GENESIS, "a")) is Outcome.ADVANCED
    assert s.apply(T("a", "b")) is Outcome.ADVANCED
    assert s.head == "b" and not s.frozen


def test_a_transition_on_a_stale_head_is_refused():
    s = StageHeads()
    s.apply(T(GENESIS, "a"))
    s.apply(T("a", "b"))
    assert s.apply(T("zzz", "c")) is Outcome.STALE
    assert s.head == "b" and not s.frozen


def test_the_same_transition_twice_is_a_no_op():
    s = StageHeads()
    s.apply(T(GENESIS, "a"))
    assert s.apply(T(GENESIS, "a")) is Outcome.DUPLICATE
    assert s.head == "a" and not s.frozen


def test_two_heads_on_one_prior_freeze_the_stage_and_keep_both():
    s = StageHeads()
    s.apply(T(GENESIS, "a"))
    assert s.apply(T(GENESIS, "b")) is Outcome.CONFLICT
    assert s.frozen and s.kept_heads() == {"a", "b"}


@pytest.mark.parametrize("heads", [("a", "b"), ("b", "a"), ("x", "y"), ("big" * 9, "s")])
def test_every_arrival_order_of_small_cases_gives_the_same_state(heads):
    states = set()
    for order in itertools.permutations(heads):
        s = StageHeads()
        for h in order:
            s.apply(T(GENESIS, h))
        states.add((s.frozen, s.kept_heads()))
    assert states == {(True, frozenset(heads))}


def test_nothing_advances_a_frozen_stage():
    s = StageHeads()
    s.apply(T(GENESIS, "a"))
    s.apply(T(GENESIS, "b"))
    assert s.apply(T("a", "c")) is Outcome.FROZEN
    assert s.apply(T("b", "c")) is Outcome.FROZEN


def test_only_an_independent_reviewer_reconciles_to_a_kept_head():
    s = StageHeads(parties=frozenset({"alice", "bob"}))
    assert s.reconcile("carol", "a") is ReconcileRefusal.NOT_FROZEN
    s.apply(T(GENESIS, "a"))
    s.apply(T(GENESIS, "b"))
    assert s.reconcile("alice", "a") is ReconcileRefusal.NOT_INDEPENDENT
    assert s.reconcile("carol", "elsewhere") is ReconcileRefusal.NOT_A_KEPT_HEAD
    assert s.frozen
    assert s.reconcile("carol", "b") is None
    assert not s.frozen and s.head == "b"
    assert s.apply(T(GENESIS, "a")) is Outcome.DUPLICATE
    assert s.apply(T("b", "c")) is Outcome.ADVANCED


def test_the_module_imports_only_the_standard_library():
    tree = ast.parse(pathlib.Path(psh.__file__).read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import)]
    names = {a.name.split(".")[0] for n in imports for a in n.names}
    froms = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    names |= {n.module.split(".")[0] for n in froms}
    assert names <= {"__future__", "dataclasses", "enum"}


def test_every_rule_in_the_docstring_names_a_test_that_exists():
    own = pathlib.Path(__file__)
    found = re.findall(r"(?<![a-z_/])(test_[a-z_0-9]+)", psh.__doc__)
    named = set(found) - {own.stem}
    assert len(named) == 8
    body = ast.parse(own.read_text(encoding="utf-8")).body
    here = {n.name for n in body if isinstance(n, ast.FunctionDef)}
    assert named <= here

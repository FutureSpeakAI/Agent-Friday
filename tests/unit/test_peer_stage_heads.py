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
P = frozenset({"alice", "bob"})


def test_a_transition_on_the_current_head_advances_it():
    s = StageHeads(P)
    assert s.apply(T(GENESIS, "a")) is Outcome.ADVANCED
    assert s.apply(T("a", "b")) is Outcome.ADVANCED
    assert s.head == "b" and not s.frozen


def test_a_transition_on_a_stale_head_is_refused():
    s = StageHeads(P)
    s.apply(T(GENESIS, "a"))
    s.apply(T("a", "b"))
    assert s.apply(T("zzz", "c")) is Outcome.STALE
    assert s.head == "b" and not s.frozen


def test_the_same_transition_twice_is_a_no_op():
    s = StageHeads(P)
    s.apply(T(GENESIS, "a"))
    assert s.apply(T(GENESIS, "a")) is Outcome.DUPLICATE
    assert s.head == "a" and not s.frozen


def test_two_heads_on_one_prior_freeze_the_stage_and_keep_both():
    s = StageHeads(P)
    s.apply(T(GENESIS, "a"))
    assert s.apply(T(GENESIS, "b")) is Outcome.CONFLICT
    assert s.frozen and s.kept_heads() == {"a", "b"}


@pytest.mark.parametrize("heads", [("a", "b"), ("b", "a"), ("x", "y"), ("big" * 9, "s")])
def test_every_arrival_order_of_small_cases_gives_the_same_state(heads):
    states = set()
    for order in itertools.permutations(heads):
        s = StageHeads(P)
        for h in order:
            s.apply(T(GENESIS, h))
        states.add((s.frozen, s.kept_heads()))
    assert states == {(True, frozenset(heads))}


CHAINS_AND_FORKS = [
    # (transitions, reconcile to, transitions applied after the reconcile)
    ([T(GENESIS, "a"), T("a", "b"), T(GENESIS, "c")], "a", [T("a", "d"), T("b", "e"), T("c", "f")]),
    ([T(GENESIS, "a"), T("a", "b"), T("b", "c"), T("b", "x")], "c", [T("a", "y"), T("c", "d")]),
    ([T(GENESIS, "a"), T("a", "b"), T(GENESIS, "c"), T("c", "d")], "c", [T("d", "e"), T("b", "z")]),
]


@pytest.mark.parametrize("received,chosen,later", CHAINS_AND_FORKS)
def test_every_arrival_order_of_chains_and_forks_gives_the_same_state(received, chosen, later):
    """Every order of the same signed transitions, the same reconcile, then the same later ones: one state."""
    results = set()
    for order in itertools.permutations(received):
        s = StageHeads(P)
        for t in order:
            s.apply(t)
        before = (s.head, s.frozen, s.kept_heads())
        refusal = s.reconcile("carol", chosen)
        after = []
        for t in later:
            after.append((s.apply(t), s.head, s.frozen, s.kept_heads()))
        results.add((before, refusal, tuple(after)))
    assert len(results) == 1, results


def test_a_reconcile_keeps_the_work_built_on_the_chosen_head():
    s = StageHeads(P)
    for t in (T(GENESIS, "a"), T("a", "b"), T(GENESIS, "c")):
        s.apply(t)
    assert s.reconcile("carol", "a") is None
    assert s.head == "b" and not s.frozen
    assert s.apply(T("a", "d")) is Outcome.CONFLICT
    assert s.frozen and s.kept_heads() == {"b", "d"}


def test_a_second_head_on_an_old_prior_freezes_even_after_a_reconcile():
    s = StageHeads(P)
    for t in (T(GENESIS, "a"), T("a", "b"), T("b", "c"), T("b", "x")):
        s.apply(t)
    assert s.reconcile("carol", "c") is None and s.head == "c"
    assert s.apply(T("a", "y")) is Outcome.CONFLICT
    assert s.frozen and s.kept_heads() == {"b", "y"}


def test_a_stage_must_name_its_parties():
    with pytest.raises(TypeError):
        StageHeads()
    with pytest.raises(ValueError):
        StageHeads(frozenset())


@pytest.mark.parametrize("prior,new", [("a", "a"), ("a", GENESIS), (GENESIS, GENESIS)])
def test_a_transition_cannot_loop_or_make_genesis(prior, new):
    with pytest.raises(ValueError):
        T(prior, new)


def test_a_cycle_through_two_priors_freezes_instead_of_looping():
    s = StageHeads(P)
    for t in (T(GENESIS, "a"), T("a", "b")):
        s.apply(t)
    assert s.apply(T("b", "a")) is Outcome.CONFLICT
    assert s.frozen and s.head == "b"


def test_nothing_advances_a_frozen_stage():
    s = StageHeads(P)
    s.apply(T(GENESIS, "a"))
    s.apply(T(GENESIS, "b"))
    assert s.apply(T("a", "c")) is Outcome.FROZEN
    assert s.apply(T("b", "c")) is Outcome.FROZEN


def test_only_an_independent_reviewer_reconciles_to_a_kept_head():
    s = StageHeads(P)
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

"""Tests for services/stage_escrow.py (NS-40.4-7, NS-40.4-8); the rules are in its docstring."""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from agent_friday.services import stage_escrow as se
from agent_friday.services.issuer_journal import IssuerJournal

ISSUER = "TESTP"


@pytest.fixture
def journal(tmp_path):
    j = IssuerJournal(tmp_path / "j.sqlite", ISSUER, 1000)
    fund = {"issuer": ISSUER, "effect": "fund", "action": "a-fund",
            "postings": {"treasury": -500, se.avail("alice"): 500}}
    assert j.execute(fund) == "posted"
    return j


def test_countersigning_terms_builds_no_command():
    assert se.countersign({"stage": "s1", "price": 100}) is None


def test_reserve_moves_available_into_escrow_once(journal):
    cmd = se.reserve(ISSUER, "s1", "alice", 200, "a1")
    assert journal.execute(cmd) == "posted"
    assert journal.execute(cmd) == "duplicate"
    b = journal.balances()
    assert b[se.avail("alice")] == 300 and b[se.escrow("s1", "alice")] == 200


def test_a_reserve_beyond_available_is_refused(journal):
    assert journal.execute(se.reserve(ISSUER, "s1", "alice", 501, "a1")) == "insufficient"
    assert journal.balances()[se.avail("alice")] == 500


def test_an_earned_share_is_credited_once_from_escrow(journal):
    journal.execute(se.reserve(ISSUER, "s1", "alice", 200, "a1"))
    pay = se.pay_out(ISSUER, "s1", "alice", "bob", 80, "a2")
    assert journal.execute(pay) == "posted"
    assert journal.execute(se.pay_out(ISSUER, "s1", "alice", "bob", 80, "a3")) == "duplicate"
    b = journal.balances()
    assert b[se.avail("bob")] == 80 and b[se.escrow("s1", "alice")] == 120


def test_two_payees_of_one_stage_are_both_paid(journal):
    journal.execute(se.reserve(ISSUER, "s1", "alice", 200, "a1"))
    assert journal.execute(se.pay_out(ISSUER, "s1", "alice", "bob", 80, "a2")) == "posted"
    assert journal.execute(se.pay_out(ISSUER, "s1", "alice", "carol", 70, "a3")) == "posted"
    b = journal.balances()
    assert b[se.avail("bob")] == 80 and b[se.avail("carol")] == 70


def test_a_disputed_share_is_held_not_paid(journal):
    journal.execute(se.reserve(ISSUER, "s1", "alice", 200, "a1"))
    assert journal.execute(se.hold_disputed(ISSUER, "s1", "alice", "bob", 50, "a2")) == "posted"
    b = journal.balances()
    assert b[se.held("s1", "bob")] == 50 and se.avail("bob") not in b


def test_cancel_releases_the_unused_escrow(journal):
    journal.execute(se.reserve(ISSUER, "s1", "alice", 200, "a1"))
    journal.execute(se.pay_out(ISSUER, "s1", "alice", "bob", 80, "a2"))
    assert journal.execute(se.cancel(ISSUER, "s1", "alice", 120, "a3")) == "posted"
    b = journal.balances()
    assert b[se.avail("alice")] == 420 and se.escrow("s1", "alice") not in b


def test_the_view_separates_available_escrow_and_sale(journal):
    journal.execute(se.reserve(ISSUER, "s1", "alice", 200, "a1"))
    journal.execute(se.reserve_for_sale(ISSUER, "x9", "alice", 50, "a2"))
    v = se.view(journal.balances(), "alice")
    assert v.available == 250
    assert v.escrow_by_stage == (("s1", 200),)
    assert v.sale_by_exchange == (("x9", 50),)
    assert se.view(journal.balances(), "bob") == se.PartyView(0, (), (), ())


@pytest.mark.parametrize("amount", range(0, 6))
def test_every_command_is_zero_sum_for_small_amounts(amount):
    builders = [se.reserve(ISSUER, "s", "p", amount, "a"),
                se.pay_out(ISSUER, "s", "p", "q", amount, "a"),
                se.hold_disputed(ISSUER, "s", "p", "q", amount, "a"),
                se.cancel(ISSUER, "s", "p", amount, "a"),
                se.reserve_for_sale(ISSUER, "x", "p", amount, "a")]
    for cmd in builders:
        assert sum(cmd["postings"].values()) == 0
        assert all(type(v) is int for v in cmd["postings"].values())


def test_the_module_imports_only_the_standard_library():
    tree = ast.parse(pathlib.Path(se.__file__).read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import)]
    froms = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    names = {a.name.split(".")[0] for n in imports for a in n.names}
    names |= {n.module.split(".")[0] for n in froms}
    assert names <= {"__future__", "dataclasses"}


def test_every_rule_in_the_docstring_names_a_test_that_exists():
    own = pathlib.Path(__file__)
    found = re.findall(r"(?<![a-z_/])(test_[a-z_0-9]+)", se.__doc__)
    named = set(found) - {own.stem}
    body = ast.parse(own.read_text(encoding="utf-8")).body
    here = {n.name for n in body if isinstance(n, ast.FunctionDef)}
    assert len(named) == 10 and named <= here

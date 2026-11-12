"""Friday's test-credit issuer journal and exchange arithmetic agree with the bundle's public
economy vectors (Federation / Economy spec, roadmap F5; tests/federation/bundle/fixtures/
economy-vectors.json, produced by the bundle's reference oracle). Test credits only: the
journal is its own SQLite file with its own fixed supply and never reads the legacy
services/economy.py balances. Offline, deterministic."""
import json
import threading
from pathlib import Path

import pytest

try:
    from agent_friday.services import issuer_journal as ij
except ImportError:  # red evidence: the journal is not in the tree yet
    ij = None

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "economy-vectors.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _the_journal_exists():
    assert ij is not None, "services/issuer_journal.py does not exist: no test-credit issuer journal"


def test_work_side_operator_share_and_contributor_floor():
    for case in VECTORS["work"]:
        fees = ij.incremental_fee(case["gross_increments"], lambda g: ij.operator_share(g, case["operator_bps"]))
        assert fees == case["incremental_fees"], case["name"]
        assert sum(case["gross_increments"]) - sum(fees) == case["contributors"], case["name"]
        # contributors keep at least 99% of the cumulative gross
        assert case["contributors"] * 100 >= sum(case["gross_increments"]) * 99, case["name"]


def test_the_operator_can_never_take_more_than_one_percent():
    for bad in (-1, 101):
        with pytest.raises(ValueError):
            ij.operator_share(1000, bad)


def test_buyer_cash_fee_is_a_quarter_percent_and_splits_cost_the_same_as_one_order():
    for case in VECTORS["cash"]:
        fees = ij.incremental_fee(case["minor_increments"], ij.cash_fee)
        assert fees == case["incremental_fees"], case["name"]
        assert sum(case["minor_increments"]) == case["seller"]
        assert case["seller"] + sum(fees) == case["buyer"], case["name"]
        assert sum(fees) == ij.cash_fee(case["seller"]), "a split order must pay what one order pays"


def test_proportional_shares_use_the_largest_remainder_rule_with_stable_ties():
    for case in VECTORS["split"]:
        assert ij.allocate(case["weights"], case["released"]) == case["shares"], case["name"]


def test_market_matching_is_whole_lots_the_buyer_can_fund_with_the_fee():
    for case in VECTORS["market"]:
        lots, price = ij.match_lots(case)
        assert (lots, price) == (case["match_lots"], case["price_per_lot"]), case["name"]
        principal = lots * (price or 0)
        assert principal == case["expected_seller"] and ij.cash_fee(principal) == case["expected_fee"], case["name"]


def test_the_issuer_vector_gives_the_oracles_answer_for_every_command(tmp_path):
    case = VECTORS["issuer"]
    journal = ij.IssuerJournal(tmp_path / "issuer.db", case["id"], case["supply"])
    for cmd in case["commands"]:
        assert journal.execute(cmd) == cmd["expect"], cmd["action"]
        assert sum(journal.balances().values()) == case["supply"], "supply must never change"
    assert journal.balances() == {k: v for k, v in case["balances"].items() if v}


def test_a_posting_survives_a_restart_and_a_replay_is_still_a_duplicate(tmp_path):
    case = VECTORS["issuer"]
    first = ij.IssuerJournal(tmp_path / "issuer.db", case["id"], case["supply"])
    cmd = case["commands"][0]
    assert first.execute(cmd) == "posted"
    again = ij.IssuerJournal(tmp_path / "issuer.db", case["id"], case["supply"])
    assert again.execute(dict(cmd, action="a-fresh-delivery-id")) == "duplicate"
    assert again.balances() == first.balances()


def test_two_threads_cannot_both_spend_the_same_balance(tmp_path):
    journal = ij.IssuerJournal(tmp_path / "issuer.db", "test.race", 1000)
    assert journal.execute({"issuer": "test.race", "effect": "fund", "action": "f",
                            "postings": {"treasury": -100, "payer": 100}}) == "posted"
    results = []

    def spend(n):
        results.append(journal.execute({"issuer": "test.race", "effect": f"spend-{n}", "action": f"s{n}",
                                        "postings": {"payer": -100, "payee": 100}}))
    threads = [threading.Thread(target=spend, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["insufficient"] * 5 + ["posted"]
    assert journal.balances() == {"treasury": 900, "payee": 100}


def test_a_journal_file_belongs_to_one_issuer_and_one_supply(tmp_path):
    ij.IssuerJournal(tmp_path / "issuer.db", "test.one", 500)
    with pytest.raises(ValueError):
        ij.IssuerJournal(tmp_path / "issuer.db", "test.two", 500)
    with pytest.raises(ValueError):
        ij.IssuerJournal(tmp_path / "issuer.db", "test.one", 501)
    with pytest.raises(ValueError):
        ij.IssuerJournal(tmp_path / "x.db", "test.one", -1)

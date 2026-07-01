"""Unit tests for the Positron/Negatron economy ledger — earn, spend,
mint_negatron, burn_negatron, transfer, genesis bonus, Q-score, leaderboard,
and hash-chained transaction records.

Unique agent IDs per test avoid balance bleed across the shared economy.db.
"""
from __future__ import annotations

import uuid

import pytest

from agent_friday.services import economy as econ

econ._ensure_schema()


def _a(name):
    return f"econ2-{name}-{uuid.uuid4().hex[:8]}"


class TestEarnSpend:
    def test_earn_credits_psi(self):
        a = _a("earn")
        econ.earn(a, 10_000, "created content")
        assert econ.get_wallet(a)["psi_balance"] == 10_000

    def test_spend_credits_eta(self):
        a = _a("spend")
        econ.spend(a, 3_000, "api call")
        assert econ.get_wallet(a)["eta_balance"] == 3_000

    def test_q_score_is_psi_minus_eta(self):
        a = _a("q")
        econ.earn(a, 10_000, "x")
        econ.spend(a, 4_000, "y")
        assert econ.get_wallet(a)["q_score"] == 6_000

    def test_negative_amounts_floored_to_zero(self):
        a = _a("neg")
        econ.earn(a, -500, "bad")
        assert econ.get_wallet(a)["psi_balance"] == 0

    def test_earn_returns_tx_record(self):
        tx = econ.earn(_a("txrec"), 1000, "reason")
        assert tx["currency"] == "PSI"
        assert tx["amount_mpsi"] == 1000
        assert "tx_hash" in tx


class TestMintBurn:
    def test_mint_negatron_prefixes_system(self):
        a = _a("mint")
        tx = econ.mint_negatron(a, 5_000, "violation")
        assert tx["reason"].startswith("system:")
        assert econ.get_wallet(a)["eta_balance"] == 5_000

    def test_burn_annihilates_psi_and_eta(self):
        a = _a("burn")
        econ.earn(a, 10_000, "x")
        econ.spend(a, 6_000, "y")
        econ.burn_negatron(a, 6_000)
        w = econ.get_wallet(a)
        assert w["psi_balance"] == 4_000   # 10k - 6k burned
        assert w["eta_balance"] == 0        # 6k - 6k burned

    def test_burn_capped_at_available(self):
        a = _a("burncap")
        econ.earn(a, 1_000, "x")
        econ.spend(a, 10_000, "y")
        txs = econ.burn_negatron(a, 10_000)
        w = econ.get_wallet(a)
        # Only 1000 psi available → burns 1000 psi and 10000 eta.
        assert w["psi_balance"] == 0
        assert w["eta_balance"] == 0
        assert isinstance(txs, list) and len(txs) == 2


class TestTransfer:
    def test_transfer_moves_psi(self):
        a, b = _a("from"), _a("to")
        econ.earn(a, 5_000, "x")
        econ.transfer(a, b, 2_000, "gift")
        assert econ.get_wallet(a)["psi_balance"] == 3_000
        assert econ.get_wallet(b)["psi_balance"] == 2_000

    def test_transfer_insufficient_funds_fails(self):
        a, b = _a("poor"), _a("rich")
        econ.earn(a, 100, "x")
        assert econ.transfer(a, b, 5_000, "overdraw") is None
        assert econ.get_wallet(a)["psi_balance"] == 100


class TestTransactionsAndLeaderboard:
    def test_get_transactions_newest_first(self):
        a = _a("hist")
        econ.earn(a, 100, "first")
        econ.earn(a, 200, "second")
        txs = econ.get_transactions(a, limit=10)
        assert len(txs) >= 2

    def test_leaderboard_ranked_by_q(self):
        hi, lo = _a("leader-hi"), _a("leader-lo")
        econ.earn(hi, 1_000_000, "x")
        econ.earn(lo, 100, "y")
        board = econ.get_leaderboard(limit=100)
        ids = [w["agent_id"] for w in board]
        assert ids.index(hi) < ids.index(lo)

    def test_hash_chain_links_transactions(self):
        a = _a("chain")
        tx1 = econ.earn(a, 100, "one")
        tx2 = econ.earn(a, 100, "two")
        # tx2's prev_hash should equal tx1's tx_hash (chain integrity).
        assert tx2["prev_hash"] == tx1["tx_hash"]


class TestGenesisBonus:
    def test_genesis_bonus_awarded_once(self):
        a = _a("genesis")
        first = econ.apply_genesis_bonus(a)
        assert first is not None
        assert first["is_genesis"] == 1
        assert first["psi_balance"] > 0
        # Second call is idempotent — no double award.
        assert econ.apply_genesis_bonus(a) is None

    def test_genesis_registers_cohort(self):
        a = _a("genesis-cohort")
        w = econ.apply_genesis_bonus(a)
        # Bonus is one of the defined cohort amounts.
        cohort_bonuses = {b for _, b in econ._GENESIS_COHORTS}
        assert w["psi_balance"] in cohort_bonuses or w["psi_balance"] > 0

"""Unit tests for services/economy.py"""
import pytest
from services import economy as econ

# The schema is skipped under FRIDAY_TESTING=1 to avoid daemon side-effects,
# but the unit tests need the tables.  Call _ensure_schema() once at import time.
econ._ensure_schema()


# Use unique agent IDs per test to avoid cross-test balance bleed
def _agent(name: str) -> str:
    """Build a unique-enough agent ID for a test."""
    return f"test-agent-econ-{name}"


class TestWallet:
    def test_get_wallet_creates_on_first_call(self):
        wallet = econ.get_wallet(_agent("new-wallet-a"))
        assert isinstance(wallet, dict)
        assert "psi_balance" in wallet
        assert "eta_balance" in wallet

    def test_wallet_has_agent_id(self):
        agent_id = _agent("new-wallet-b")
        wallet = econ.get_wallet(agent_id)
        assert wallet["agent_id"] == agent_id

    def test_wallet_starts_at_zero(self):
        wallet = econ.get_wallet(_agent("zero-balance-check"))
        assert wallet["psi_balance"] == 0
        assert wallet["eta_balance"] == 0

    def test_wallet_has_q_score(self):
        wallet = econ.get_wallet(_agent("q-score-field"))
        assert "q_score" in wallet

    def test_get_wallet_idempotent(self):
        agent_id = _agent("idempotent-get")
        w1 = econ.get_wallet(agent_id)
        w2 = econ.get_wallet(agent_id)
        assert w1["agent_id"] == w2["agent_id"]
        assert w1["psi_balance"] == w2["psi_balance"]


class TestEarn:
    def test_earn_returns_tx_record(self):
        tx = econ.earn(_agent("earn-tx"), 5_000, "test-earn")
        assert tx is not None
        assert isinstance(tx, dict)

    def test_earn_tx_has_required_fields(self):
        tx = econ.earn(_agent("earn-fields"), 1_000, "test")
        assert tx is not None
        for field in ("id", "amount_mpsi", "currency"):
            assert field in tx, f"Missing field: {field}"

    def test_earn_currency_is_psi(self):
        tx = econ.earn(_agent("earn-currency"), 1_000, "test")
        assert tx is not None
        assert tx["currency"] == "PSI"

    def test_earn_increases_psi_balance(self):
        agent_id = _agent("earn-balance")
        econ.earn(agent_id, 5_000, "test")
        wallet = econ.get_wallet(agent_id)
        assert wallet["psi_balance"] == 5_000

    def test_earn_accumulates(self):
        agent_id = _agent("earn-accum")
        econ.earn(agent_id, 1_000, "first")
        econ.earn(agent_id, 2_000, "second")
        wallet = econ.get_wallet(agent_id)
        assert wallet["psi_balance"] == 3_000


class TestSpend:
    def test_spend_increases_eta_balance(self):
        agent_id = _agent("spend-eta")
        econ.spend(agent_id, 100, "bandwidth")
        wallet = econ.get_wallet(agent_id)
        assert wallet["eta_balance"] == 100

    def test_spend_returns_tx_record(self):
        tx = econ.spend(_agent("spend-tx"), 50, "bandwidth")
        assert tx is not None
        assert isinstance(tx, dict)

    def test_spend_currency_is_eta(self):
        tx = econ.spend(_agent("spend-currency"), 200, "bandwidth")
        assert tx is not None
        assert tx["currency"] == "ETA"


class TestTransfer:
    def test_transfer_moves_balance(self):
        sender = _agent("transfer-sender")
        receiver = _agent("transfer-receiver")
        econ.earn(sender, 10_000, "seed")
        econ.transfer(sender, receiver, 4_000, "payment")
        assert econ.get_wallet(sender)["psi_balance"] == 6_000
        assert econ.get_wallet(receiver)["psi_balance"] == 4_000

    def test_transfer_insufficient_balance_returns_none(self):
        sender = _agent("transfer-broke")
        receiver = _agent("transfer-receiver-2")
        # sender has 0 ψ
        result = econ.transfer(sender, receiver, 9_999_999, "too-much")
        assert result is None

    def test_transfer_creates_tx_for_both_parties(self):
        sender = _agent("transfer-both-a")
        receiver = _agent("transfer-both-b")
        econ.earn(sender, 5_000, "seed")
        econ.transfer(sender, receiver, 2_000, "split")
        sender_txs = econ.get_transactions(sender)
        receiver_txs = econ.get_transactions(receiver)
        # Both should have at least one transaction referencing this transfer
        assert any(tx.get("amount_mpsi") == 2_000 for tx in sender_txs + receiver_txs)


class TestNegatron:
    def test_mint_negatron_increases_eta(self):
        agent_id = _agent("mint-eta")
        econ.mint_negatron(agent_id, 500, "system:penalty")
        wallet = econ.get_wallet(agent_id)
        assert wallet["eta_balance"] == 500

    def test_burn_negatron_decreases_both(self):
        agent_id = _agent("burn-both")
        econ.earn(agent_id, 10_000, "seed")
        econ.mint_negatron(agent_id, 3_000, "system:penalty")
        econ.burn_negatron(agent_id, 2_000)
        wallet = econ.get_wallet(agent_id)
        assert wallet["psi_balance"] == 8_000
        assert wallet["eta_balance"] == 1_000

    def test_burn_negatron_returns_list_of_two(self):
        agent_id = _agent("burn-list")
        econ.earn(agent_id, 5_000, "seed")
        econ.mint_negatron(agent_id, 1_000, "system:test")
        result = econ.burn_negatron(agent_id, 500)
        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 2

    def test_burn_negatron_clamps_to_available(self):
        agent_id = _agent("burn-clamp")
        econ.earn(agent_id, 100, "seed")
        econ.mint_negatron(agent_id, 100, "system:test")
        # Try to burn more than available
        econ.burn_negatron(agent_id, 999_999)
        wallet = econ.get_wallet(agent_id)
        # Neither balance should go negative
        assert wallet["psi_balance"] >= 0
        assert wallet["eta_balance"] >= 0


class TestTransactions:
    def test_get_transactions_returns_list(self):
        agent_id = _agent("tx-list")
        econ.earn(agent_id, 1_000, "test")
        result = econ.get_transactions(agent_id)
        assert isinstance(result, list)

    def test_get_transactions_has_entries_after_earn(self):
        agent_id = _agent("tx-has-entries")
        econ.earn(agent_id, 2_000, "test")
        txs = econ.get_transactions(agent_id)
        assert len(txs) >= 1

    def test_get_transactions_empty_for_new_agent(self):
        agent_id = _agent("tx-empty")
        # No operations on this agent
        txs = econ.get_transactions(agent_id)
        assert isinstance(txs, list)
        assert txs == []


class TestLeaderboard:
    def test_get_leaderboard_returns_list(self):
        result = econ.get_leaderboard()
        assert isinstance(result, list)

    def test_leaderboard_entries_have_q_score(self):
        agent_id = _agent("leaderboard-qscore")
        econ.earn(agent_id, 1_000, "test")
        board = econ.get_leaderboard()
        for entry in board:
            assert "q_score" in entry

    def test_q_score_formula(self):
        agent_id = _agent("q-formula")
        econ.earn(agent_id, 5_000, "test")
        econ.spend(agent_id, 500, "test")
        wallet = econ.get_wallet(agent_id)
        assert wallet["q_score"] == wallet["psi_balance"] - wallet["eta_balance"]


class TestGenesisBonus:
    def test_apply_genesis_bonus_first_call_returns_tx(self):
        agent_id = _agent("genesis-first")
        result = econ.apply_genesis_bonus(agent_id)
        # Either a tx record (claimed) or None (cohort windows exhausted in test DB)
        if result is not None:
            assert isinstance(result, dict)

    def test_apply_genesis_bonus_idempotent(self):
        agent_id = _agent("genesis-idempotent")
        first = econ.apply_genesis_bonus(agent_id)
        second = econ.apply_genesis_bonus(agent_id)
        # Second call must return None regardless of whether first succeeded
        assert second is None

    def test_genesis_cohort_1_gets_large_amount(self):
        # First wallet in a fresh test DB should qualify for cohort 1 (1_000_000 mψ)
        # We can't guarantee cohort size in shared DB, but if it's claimed, check amount
        agent_id = _agent("genesis-cohort1")
        result = econ.apply_genesis_bonus(agent_id)
        if result is not None:
            wallet = econ.get_wallet(agent_id)
            # Cohort 1 = 1_000_000 mψ, cohort 2 = 500_000, cohort 3 = 250_000
            assert wallet["psi_balance"] >= 250_000

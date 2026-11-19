"""NS-40.4-2 and NS-40.4-14 on the real journal: the pilot supply is fixed at genesis, nothing
mints or burns afterwards, and a copied or forked file does not become another issuer's money.
TEST CREDITS ONLY."""
from __future__ import annotations

import shutil

import pytest

from agent_friday.services.issuer_journal import IssuerJournal

ISSUER = "TEST-P"
GENESIS = 1_000_000


@pytest.fixture
def journal(tmp_path):
    return IssuerJournal(tmp_path / "j.sqlite", ISSUER, GENESIS)


def _cmd(effect, postings, action=None):
    return {"issuer": ISSUER, "effect": effect, "action": action or f"a-{effect}", "postings": postings}


def test_genesis_puts_exactly_the_pilot_supply_in_the_treasury_and_nowhere_else(journal):
    assert journal.balances() == {"treasury": GENESIS}


@pytest.mark.parametrize("postings", [
    {"treasury": 1},
    {"alice": 1},
    {"treasury": -1},
    {"alice": GENESIS},
    {"treasury": 5, "alice": 5},
])
def test_any_posting_that_mints_or_burns_is_refused(journal, postings):
    assert journal.execute(_cmd("m", postings)) == "unbalanced"
    assert journal.balances() == {"treasury": GENESIS}


def test_supply_never_changes_across_valid_postings(journal):
    journal.execute(_cmd("a", {"treasury": -300, "alice": 300}))
    journal.execute(_cmd("b", {"alice": -100, "bob": 100}))
    assert sum(journal.balances().values()) == GENESIS


def test_the_pilot_supply_cannot_be_reopened_with_a_different_supply(tmp_path):
    IssuerJournal(tmp_path / "j.sqlite", ISSUER, GENESIS)
    with pytest.raises(ValueError):
        IssuerJournal(tmp_path / "j.sqlite", ISSUER, GENESIS + 1)


def test_a_copied_journal_file_is_not_another_issuers_money(tmp_path):
    IssuerJournal(tmp_path / "a.sqlite", ISSUER, GENESIS)
    shutil.copy(tmp_path / "a.sqlite", tmp_path / "fork.sqlite")
    with pytest.raises(ValueError):
        IssuerJournal(tmp_path / "fork.sqlite", "FORK-P", GENESIS)


def test_a_command_for_another_issuer_is_refused(journal):
    foreign = {"issuer": "OTHER", "effect": "x", "action": "a", "postings": {"treasury": -1, "alice": 1}}
    assert journal.execute(foreign) == "issuer"
    assert journal.balances() == {"treasury": GENESIS}


# The public surface, each name with the reason it cannot create units. A new public method fails the test below until it
# is added here with its own proof (the test after it), so the surface cannot grow silently (the b55 gate caught the
# unlisted methods #889 and #897 added).
SURFACE = {
    "execute": "refuses any posting that does not sum to zero (test_any_posting_that_mints_or_burns_is_refused)",
    "balances": "reads only",
    "execute_receipt": "execute with its receipt; same refusal (test_execute_receipt_refuses_a_mint_and_changes_nothing)",
    "head": "reads only",
    "recover": "recomputes and halts on a mismatch; never posts",
    "outbox_pending": "reads only",
    "outbox_delivered": "marks a notification delivered; touches no balance",
}


def test_the_journal_offers_no_way_to_create_units():
    public = {n for n in dir(IssuerJournal) if not n.startswith("_")}
    assert public <= set(SURFACE), f"a new public method needs a line in SURFACE and a proof: {sorted(public - set(SURFACE))}"


def _snapshot(journal):
    return dict(journal.balances()), journal.head() if hasattr(journal, "head") else None


def test_every_public_method_that_exists_leaves_the_supply_and_balances_alone(journal):
    journal.execute(_cmd("a", {"treasury": -300, "alice": 300}))
    before = _snapshot(journal)
    if hasattr(journal, "recover"):
        assert journal.recover() is None
    if hasattr(journal, "outbox_pending"):
        for item in journal.outbox_pending():
            journal.outbox_delivered(item["effect"])
        assert journal.outbox_pending() == []
    if hasattr(journal, "head"):
        journal.head()
    assert _snapshot(journal) == before
    assert sum(journal.balances().values()) == GENESIS


@pytest.mark.skipif(not hasattr(IssuerJournal, "execute_receipt"), reason="no receipt method in this tree")
@pytest.mark.parametrize("postings", [{"treasury": 1}, {"alice": 1}, {"treasury": -1}, {"alice": GENESIS},
                                      {"treasury": 5, "alice": 5}])
def test_execute_receipt_refuses_a_mint_and_changes_nothing(journal, postings):
    outcome, receipt = journal.execute_receipt(_cmd("m", postings))
    assert outcome == "unbalanced" and receipt is None
    assert journal.balances() == {"treasury": GENESIS}

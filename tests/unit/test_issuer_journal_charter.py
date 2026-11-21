"""A journal opened by charter is bound to it (NS-40.4-1, design A.3 and A.4 test 4 and 9)."""
import pytest

from agent_friday.services import issuer_journal as ij
from agent_friday.services import issuer_registry as reg

KEY = "ab" * 32


def charter(**over):
    base = dict(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=1000,
                treasury_account="treasury", cash_claim=False, genesis_authority=KEY,
                created="2026-11-16T00:00:00Z")
    return reg.IssuerCharter(**{**base, **over})


def test_a_charter_journal_starts_with_the_whole_supply_in_the_treasury(tmp_path):
    journal = ij.IssuerJournal(tmp_path / "j.sqlite", charter=charter())
    assert journal.issuer == "test.one" and journal.balances() == {"treasury": 1000}


def test_reopening_with_the_same_charter_works_and_keeps_postings(tmp_path):
    path = tmp_path / "j.sqlite"
    first = ij.IssuerJournal(path, charter=charter())
    assert first.execute({"issuer": "test.one", "effect": "e1", "action": "a1",
                          "postings": {"treasury": -5, "alice": 5}}) == "posted"
    assert ij.IssuerJournal(path, charter=charter()).balances() == {"treasury": 995, "alice": 5}


@pytest.mark.parametrize("other", [dict(unit_code="OTHERP"), dict(precision=2), dict(cash_claim=True),
                                   dict(genesis_authority="cd" * 32), dict(created="2026-11-17T00:00:00Z")])
def test_a_journal_is_refused_by_a_charter_that_differs_in_any_field(tmp_path, other):
    path = tmp_path / "j.sqlite"
    ij.IssuerJournal(path, charter=charter())
    with pytest.raises(ValueError, match="another charter"):
        ij.IssuerJournal(path, charter=charter(**other))


def test_a_journal_is_refused_by_another_supply_or_issuer(tmp_path):
    path = tmp_path / "j.sqlite"
    ij.IssuerJournal(path, charter=charter())
    with pytest.raises(ValueError, match="different supply"):
        ij.IssuerJournal(path, charter=charter(genesis_supply=999))
    with pytest.raises(ValueError, match="another issuer"):
        ij.IssuerJournal(path, charter=charter(issuer_id="test.two"))


def test_a_charter_journal_cannot_be_opened_without_its_charter(tmp_path):
    path = tmp_path / "j.sqlite"
    ij.IssuerJournal(path, charter=charter())
    with pytest.raises(ValueError, match="another charter"):
        ij.IssuerJournal(path, "test.one", 1000)


def test_a_journal_made_without_a_charter_cannot_be_claimed_by_one(tmp_path):
    path = tmp_path / "j.sqlite"
    ij.IssuerJournal(path, "test.one", 1000)
    with pytest.raises(ValueError, match="without a charter"):
        ij.IssuerJournal(path, charter=charter())
    assert ij.IssuerJournal(path, "test.one", 1000).balances() == {"treasury": 1000}


def test_a_charter_and_an_issuer_together_are_refused(tmp_path):
    with pytest.raises(TypeError):
        ij.IssuerJournal(tmp_path / "j.sqlite", "test.one", 1000, charter=charter())

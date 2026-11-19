"""NS-40.4-14: registrations, signatures and acceptance create no monetary balance. TEST CREDITS ONLY.

Trust is not money. #847 showed the journal has no way to create units and #892 showed 1,000 resource readings leave a
journal unchanged. This file covers the rest of the row: registering charters (this issuer and many others), signing
and verifying heads and receipts, pinning heads, accepting and withdrawing an issuer, and reading a copy of the
journal all leave the journal's rows and balances exactly as they were. The journal is compared as a full row dump of
every table (not file bytes, which a read may legitimately touch). Honest about what bites today: the registry, the pins and
the accepted list write their own files, so for them this is a regression guard that fails the day one of them is wired to
the journal; only verifying (a duplicate execute) and reading a copy touch the journal itself. The comparison is shown able
to fail (Q2): a real posting changes the dump.

Limit, stated plainly: it covers the operations that exist in the tree today (registry, authority, pins, accepted list,
read-only view); a new trust feature must add itself to ACTIONS below.
"""
import sqlite3

import pytest

from agent_friday.services import accepted_issuers as acc
from agent_friday.services import issuer_authority as auth
from agent_friday.services import issuer_registry as reg
from agent_friday.services import peer_wire_v2 as wire

SEED = bytes(range(32))
KEY = auth.Ed25519Key(SEED)
PUBLIC = KEY.public_key
CHARTER = reg.IssuerCharter(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=100,
                            treasury_account="treasury", cash_claim=False, genesis_authority=PUBLIC,
                            created="2026-11-16T00:00:00Z")


def dump(path):
    db = sqlite3.connect(str(path))
    try:
        return {row[0]: db.execute(f'SELECT * FROM "{row[0]}" ORDER BY 1, 2').fetchall()
                for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")}
    finally:
        db.close()


def other_charter(n):
    return reg.IssuerCharter(issuer_id=f"test.other{n}", unit_code=f"T{n}", precision=0, genesis_supply=10**6,
                             treasury_account="treasury", cash_claim=False, genesis_authority=PUBLIC,
                             created="2026-11-16T00:00:00Z")


@pytest.fixture
def authority(tmp_path):
    path = tmp_path / "j.sqlite"
    with auth.Authority(path, CHARTER, KEY) as a:
        for n in range(3):
            a.execute({"issuer": "test.one", "effect": f"e{n}", "action": f"act-{n}",
                       "postings": {"treasury": -5, "alice": 5}})
        yield a, path


def _register_many(a, path, tmp_path):
    registry = reg.IssuerRegistry(tmp_path / "registry")
    for n in range(50):
        registry.register(other_charter(n))
    registry.register(CHARTER)
    registry.get("test.one")


def _verify_everything(a, path, tmp_path):
    digest = reg.charter_digest(CHARTER)
    last = auth.verify_chain(a.heads(), PUBLIC, digest)
    assert last.seq >= 3
    outcome, signed = a.execute({"issuer": "test.one", "effect": "e0", "action": "act-0",
                                 "postings": {"treasury": -5, "alice": 5}})
    assert outcome.value == "duplicate"           # a repeat of a posted effect posts nothing new
    if signed is not None:
        auth.verify_receipt(signed, PUBLIC, digest)


def _pin_heads(a, path, tmp_path):
    pins = auth.HeadPins(tmp_path / "pins.json")
    digest = reg.charter_digest(CHARTER)
    for signed_head in a.heads():
        raw, signature = signed_head
        pins.accept(raw, signature, PUBLIC, digest)


def _accept_and_withdraw(a, path, tmp_path):
    accepted = acc.AcceptedIssuers(tmp_path / "accepted.json", lambda data: KEY.sign(data), PUBLIC)
    accepted.accept(reg.IssuerRef.of(CHARTER), acc.Press(), 1)
    accepted.withdraw("test.one")


def _read_a_copy(a, path, tmp_path):
    view = auth.ReadOnlyView(path)
    view.balances()
    view.heads()
    view.as_of_seq()


ACTIONS = [_register_many, _verify_everything, _pin_heads, _accept_and_withdraw, _read_a_copy]


@pytest.mark.parametrize("action", ACTIONS, ids=lambda f: f.__name__.strip("_"))
def test_the_action_leaves_every_journal_row_and_every_balance_as_it_was(action, authority, tmp_path):
    a, path = authority
    before_rows, before_balances = dump(path), dict(a.balances())
    action(a, path, tmp_path)
    assert dump(path) == before_rows
    assert dict(a.balances()) == before_balances
    assert sum(a.balances().values()) == CHARTER.genesis_supply, "the supply is still exactly the genesis supply"


def test_all_of_them_together_change_nothing(authority, tmp_path):
    a, path = authority
    before = dump(path)
    for action in ACTIONS:
        action(a, path, tmp_path)
    assert dump(path) == before


def test_the_check_can_fail_a_real_posting_changes_the_dump_and_the_balances(authority):
    a, path = authority
    before, balances = dump(path), dict(a.balances())
    outcome, _ = a.execute({"issuer": "test.one", "effect": "e-real", "action": "act-real",
                            "postings": {"treasury": -1, "bob": 1}})
    assert outcome.value == "posted"
    assert dump(path) != before and dict(a.balances()) != balances
    assert sum(a.balances().values()) == CHARTER.genesis_supply, "a posting moves units; it never adds any"


def test_wire_signatures_by_the_issuers_key_are_not_postings(authority):
    a, path = authority
    before = dump(path)
    for n in range(20):
        wire.canonical({"n": n})
        KEY.sign(wire.canonical({"n": n}))
    assert dump(path) == before

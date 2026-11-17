"""Authority history: a chain of keys, each handed on by the one before (NS-40.4-1, design A-7 and A-8, PR 4)."""
import pytest

from agent_friday.services import issuer_authority as auth
from agent_friday.services import issuer_authority_chain as chain
from agent_friday.services import issuer_registry as reg

SEEDS = [bytes([n] * 32) for n in (1, 2, 3, 4)]
KEYS = [auth.Ed25519Key(seed, generation=i) for i, seed in enumerate(SEEDS)]


def charter():
    return reg.IssuerCharter(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=100,
                             treasury_account="treasury", cash_claim=False, genesis_authority=KEYS[0].public_key,
                             created="2026-11-16T00:00:00Z")


def history(starts=(10, 50)):
    first = chain.genesis(charter())
    second = chain.hand_over(KEYS[0], 1, KEYS[1].public_key, starts[0], "rotated")
    third = chain.hand_over(KEYS[1], 2, KEYS[2].public_key, starts[1], "rotated again")
    return [first, second, third]


def head(seq, generation, signer=None, issuer="test.one", digest=None):
    signer = signer or KEYS[generation]
    h = auth.Head(issuer, digest or reg.charter_digest(charter()), generation, seq, "a" * 64, "0" * 64)
    raw = h.canonical_bytes()
    return raw, signer.sign(auth.HEAD_PREFIX + raw)


def test_a_valid_chain_passes_and_each_kind_of_break_fails():
    records = history()
    assert [r.generation for r in chain.verify_authority_chain(charter(), records)] == [0, 1, 2]
    assert chain.verify_authority_chain(charter(), records[:1])[0].public_key == KEYS[0].public_key
    assert [r.generation for r in chain.verify_authority_chain(charter(), list(reversed(records)))] == [0, 1, 2]
    first, second, third = records
    breaks = {
        "a missing link": [first, third],
        "a reused generation": [first, second, second],
        "a wrong signer": [first, chain.hand_over(KEYS[3], 1, KEYS[1].public_key, 10, "rotated"), third],
        "no signature": [first, chain.AuthorityRecord(1, KEYS[1].public_key, 10, "rotated"), third],
        "a signature over other words": [first, chain.AuthorityRecord(
            1, KEYS[1].public_key, 11, "rotated", second.signed_by_previous), third],
        "not starting at generation 0": [second, third],
        "generation 0 is not the charter's key": [chain.AuthorityRecord(0, KEYS[3].public_key, 1, "genesis"), second],
        "a signed generation 0": [chain.AuthorityRecord(0, KEYS[0].public_key, 1, "genesis", "00" * 64), second],
        "starting before the one it replaces": [first, chain.hand_over(KEYS[0], 1, KEYS[1].public_key, 1, "early")],
        "nothing": [],
    }
    for name, records_broken in breaks.items():
        with pytest.raises(chain.ChainError):
            chain.verify_authority_chain(charter(), records_broken)
        assert name


def test_a_record_is_checked_for_its_own_fields():
    for bad in (dict(generation=-1), dict(generation=True), dict(valid_from_seq=0), dict(reason=""), dict(reason="é")):
        fields = dict(generation=0, public_key=KEYS[0].public_key, valid_from_seq=1, reason="genesis") | bad
        with pytest.raises(chain.ChainError):
            chain.AuthorityRecord(**fields)


def test_a_head_is_valid_only_within_its_generations_sequences():
    records = history((10, 50))
    check = lambda seq, gen, signer=None: chain.verify_head(*head(seq, gen, signer), charter(), records)
    assert check(1, 0).seq == 1 and check(9, 0).seq == 9
    assert check(10, 1).seq == 10 and check(49, 1).seq == 49 and check(50, 2).seq == 50 and check(500, 2).seq == 500
    for seq, generation in ((10, 0), (50, 1), (9, 1), (49, 2), (0, 0)):
        with pytest.raises(chain.ChainError):
            check(seq, generation)


def test_a_head_signed_by_the_wrong_key_or_for_another_charter_fails():
    records = history()
    with pytest.raises(chain.ChainError):
        chain.verify_head(*head(12, 1, signer=KEYS[0]), charter(), records)
    with pytest.raises(chain.ChainError):
        chain.verify_head(*head(12, 3, signer=KEYS[3]), charter(), records)
    with pytest.raises(chain.ChainError):
        chain.verify_head(*head(12, 1, digest="9" * 64), charter(), records)
    with pytest.raises(chain.ChainError):
        chain.verify_head(*head(12, 1, issuer="test.two"), charter(), records)
    with pytest.raises(chain.ChainError):
        chain.verify_head(b"not a head", "00" * 64, charter(), records)
    raw, signature = head(12, 1)
    with pytest.raises(chain.ChainError):
        chain.verify_head(raw, signature[:-2] + "00", charter(), records)


def test_a_broken_history_stops_every_head_from_verifying():
    records = history()
    with pytest.raises(chain.ChainError):
        chain.verify_head(*head(12, 1), charter(), [records[0], records[2]])

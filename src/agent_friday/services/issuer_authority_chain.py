"""An issuer's authority history: a chain of keys, each handed on by the one before
(NS-40.4-1; design A-7, A-8). TEST CREDITS ONLY. The rotation ceremony itself is not designed here;
this checks a history that someone already made.

Generation 0 is the key named in the charter. Generation g is signed by generation g-1's key over
the canonical record, and says from which journal sequence it may sign. A head signed by generation
g is valid only from that sequence and only until the next generation takes over.

Rules and the tests that hold them (tests/unit/test_issuer_authority_chain.py):
- A valid chain passes; a missing link, a wrong signer, a reused or skipped generation, a record that
  does not start from the charter's key, and a record with no signature each fail:
  test_a_valid_chain_passes_and_each_kind_of_break_fails.
- A key signs heads only from its sequence and only until the next generation's sequence:
  test_a_head_is_valid_only_within_its_generations_sequences.
- A head signed with an earlier generation's key after the handover fails: same test.
"""
from __future__ import annotations

from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.issuer_authority import Head, HeadError, HEAD_PREFIX, SigningKey
from agent_friday.services.issuer_registry import IssuerCharter, charter_digest

RECORD_PREFIX = b"asimov-issuer-authority/v1" + bytes([0])


class ChainError(ValueError):
    """The authority history does not hold."""


@dataclass(frozen=True)
class AuthorityRecord:
    generation: int
    public_key: str
    valid_from_seq: int
    reason: str
    signed_by_previous: str | None = None

    def __post_init__(self):
        if type(self.generation) is not int or self.generation < 0:
            raise ChainError("a generation is a whole number from 0")
        if type(self.valid_from_seq) is not int or self.valid_from_seq < 1:
            raise ChainError("a record starts at a sequence of 1 or more")
        if not isinstance(self.reason, str) or not self.reason or not self.reason.isascii() or len(self.reason) > 200:
            raise ChainError("a reason is short ASCII text")

    def body(self) -> bytes:
        return wire.canonical({"generation": self.generation, "public_key": self.public_key,
                               "valid_from_seq": self.valid_from_seq, "reason": self.reason})


def hand_over(previous: SigningKey, generation: int, public_key: str, valid_from_seq: int,
              reason: str) -> AuthorityRecord:
    """The record that makes `public_key` generation `generation`, signed by the key it replaces."""
    unsigned = AuthorityRecord(generation, public_key, valid_from_seq, reason)
    return AuthorityRecord(generation, public_key, valid_from_seq, reason,
                           previous.sign(RECORD_PREFIX + unsigned.body()))


def genesis(charter: IssuerCharter) -> AuthorityRecord:
    return AuthorityRecord(0, charter.genesis_authority, 1, "genesis")


def _verify(public_key: str, data: bytes, signature: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(bytes.fromhex(signature), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify_authority_chain(charter: IssuerCharter, records: list) -> list:
    """The records in generation order when the whole history holds, else ChainError."""
    ordered = sorted(records, key=lambda r: r.generation)
    if not ordered or ordered[0].generation != 0:
        raise ChainError("the history does not start at generation 0")
    first = ordered[0]
    if first.public_key != charter.genesis_authority or first.signed_by_previous is not None:
        raise ChainError("generation 0 is not the charter's key")
    if first.valid_from_seq != 1:
        raise ChainError("generation 0 starts at sequence 1")
    for previous, record in zip(ordered, ordered[1:]):
        if record.generation != previous.generation + 1:
            raise ChainError("a generation is missing or used twice")
        if record.valid_from_seq <= previous.valid_from_seq:
            raise ChainError("a generation starts before the one it replaces")
        if record.signed_by_previous is None or not _verify(
                previous.public_key, RECORD_PREFIX + record.body(), record.signed_by_previous):
            raise ChainError("a generation was not handed on by the key before it")
    return ordered


def verify_head(raw: bytes, signature: str, charter: IssuerCharter, records: list) -> Head:
    """The head, when it was signed by the key of its own generation, within that generation's sequences."""
    chain = verify_authority_chain(charter, records)
    try:
        head = Head.parse(raw)
    except HeadError as err:
        raise ChainError("not a head") from err
    if head.charter_digest != charter_digest(charter) or head.issuer_id != charter.issuer_id:
        raise ChainError("the head belongs to another issuer or charter")
    found = [r for r in chain if r.generation == head.generation]
    if not found:
        raise ChainError("the head names a generation that has no record")
    record = found[0]
    later = [r for r in chain if r.generation == head.generation + 1]
    if head.seq < record.valid_from_seq or (later and head.seq >= later[0].valid_from_seq):
        raise ChainError("the head is outside the sequences its generation may sign")
    if not _verify(record.public_key, HEAD_PREFIX + raw, signature):
        raise ChainError("the head signature does not hold")
    return head

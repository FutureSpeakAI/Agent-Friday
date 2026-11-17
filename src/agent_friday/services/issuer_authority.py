"""The one serialized writer for one issuer, and the signed heads it produces (NS-40.4-3; design:
program/specs/DESIGN_economy_issuer_exchange_resource_2026-11-16.md, Part B and section 10.1 to 10.4).
TEST CREDITS ONLY. The key is passed in by the caller and is never written to the journal file;
where the production key lives (OS keychain or vault) is for the wiring PR.

LIMIT, stated plainly: with a single authority, equivocation (two histories under one key) is DETECTED by a peer that
pins what it has seen, not PREVENTED. Prevention would need the separately specified consensus; it is not built or
claimed here. `HeadPins` takes one public key and is keyed by issuer id: it does not use the authority chain of
services/issuer_authority_chain, which only matters once a second generation exists.

Authority generations other than 0 (rotation, `valid_from_seq`) are services/issuer_authority_chain.

Rules and the tests that hold them (tests/unit/test_issuer_authority.py):
- B-1 / 10.4 one writer per issuer: the Authority holds a lock for its life and the journal's own write path refuses
  every other writer, in this process or another:
  test_a_second_authority_is_refused_until_the_first_closes,
  test_while_an_authority_holds_an_issuer_no_other_object_can_post_to_it,
  test_the_writing_journal_is_constructed_only_where_the_guard_allows.
- B-6 a copy without the key reads "as of seq N" and can post nothing:
  test_a_copy_without_the_key_reads_as_of_a_sequence_and_posts_nothing.
- 10.1 the head commits to the receipt, so a receipt changed under a genuine head fails:
  test_a_receipt_whose_effect_or_hash_was_changed_fails_under_the_genuine_head,
  test_a_changed_receipt_head_or_signature_fails.
- 10.1 there is one head per sequence with no gaps, each with the right state:
  test_after_a_crash_that_skipped_heads_the_next_open_signs_every_missing_one_with_the_right_state,
  test_an_old_duplicate_returns_its_receipt_with_its_own_head_even_after_a_gap.
- Heads link to each other: test_each_head_links_to_the_one_before.
- A head signature is bound to its purpose, issuer and charter:
  test_a_head_signature_is_bound_to_its_purpose_issuer_and_charter.
- 10.2 equivocation is two heads at one sequence OR a head that does not link to its pinned neighbour, in any arrival
  order, reported once: test_two_heads_at_one_sequence_refuse_the_issuer_and_report_once,
  test_a_head_that_does_not_link_to_its_pinned_neighbour_is_equivocation_in_every_arrival_order,
  test_a_head_whose_neighbours_are_not_pinned_yet_is_accepted_unlinked_and_checked_later.
- 10.3 on open the last head's state and receipt hashes must equal the rebuilt journal's, else the journal is halted:
  test_a_deep_tamper_that_re_hashes_everything_is_caught_by_the_last_head_on_open.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.issuer_journal import (
    AUTHORITY_LOCK_SUFFIX, AuthorityHeld, IssuerJournal, Outcome, exclusive_lock)
from agent_friday.services.issuer_registry import IssuerCharter, charter_digest

HEAD_PREFIX = b"asimov-issuer-head/v1" + bytes([0])
GENESIS_HEAD = "0" * 64
__all__ = ["Authority", "AuthorityHeld", "Ed25519Key", "Head", "HeadError", "HeadPins", "PinOutcome", "ReadOnlyView",
           "SignedReceipt", "state_hash", "verify_chain", "verify_head", "verify_receipt"]


class HeadError(ValueError):
    """A head or receipt that does not verify."""


class SigningKey(Protocol):
    generation: int
    public_key: str

    def sign(self, data: bytes) -> str: ...


class Ed25519Key:
    """An authority key held in memory by the caller."""

    def __init__(self, seed: bytes, generation: int = 0):
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self.generation = generation
        self.public_key = self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def sign(self, data: bytes) -> str:
        return self._key.sign(data).hex()


@dataclass(frozen=True)
class Head:
    issuer_id: str
    charter_digest: str
    generation: int
    seq: int
    state_hash: str
    receipt_hash: str          # SHA-256 of the stored receipt bytes of the effect at this seq (design 10.1)
    prev_head_hash: str

    def canonical_bytes(self) -> bytes:
        return wire.canonical({"issuer_id": self.issuer_id, "charter_digest": self.charter_digest,
                               "generation": self.generation, "seq": self.seq, "state_hash": self.state_hash,
                               "receipt_hash": self.receipt_hash, "prev_head_hash": self.prev_head_hash})

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def parse(cls, raw: bytes) -> "Head":
        try:
            return cls(**wire.decode(raw))
        except (TypeError, wire.WireError) as err:
            raise HeadError("not a head") from err


def state_hash(balances: dict) -> str:
    return hashlib.sha256(wire.canonical([[a, v] for a, v in sorted(balances.items()) if v])).hexdigest()


def receipt_hash(receipt: bytes) -> str:
    return hashlib.sha256(receipt).hexdigest()


def verify_head(raw: bytes, signature: str, public_key: str) -> Head:
    """The head, when its signature holds under `public_key` for this purpose."""
    head = Head.parse(raw)
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(signature), HEAD_PREFIX + raw)
    except (InvalidSignature, ValueError, TypeError) as err:
        raise HeadError("the head signature does not hold") from err
    return head


@dataclass(frozen=True)
class SignedReceipt:
    receipt: bytes
    head: bytes | None
    signature: str | None


def verify_receipt(signed: SignedReceipt, public_key: str, expected_charter_digest: str) -> Head:
    """The head the receipt rests on, when every link holds: signature, charter, issuer, seq AND that the receipt's own
    bytes are the ones the head commits to."""
    if signed.head is None or signed.signature is None:
        raise HeadError("this receipt has no head signature")
    head = verify_head(signed.head, signed.signature, public_key)
    body = wire.decode(signed.receipt)
    if head.charter_digest != expected_charter_digest:
        raise HeadError("the head belongs to another charter")
    if body.get("issuer") != head.issuer_id or body.get("seq") != head.seq:
        raise HeadError("the receipt and the head do not agree")
    if receipt_hash(signed.receipt) != head.receipt_hash:
        raise HeadError("the receipt is not the one the head commits to")
    return head


def verify_chain(heads: list, public_key: str, expected_charter_digest: str) -> Head:
    """Every (head bytes, signature) verifies, there is one head for every sequence from 1 with none missing, and each
    links to the one before."""
    previous = None
    for raw, signature in heads:
        head = verify_head(raw, signature, public_key)
        if head.charter_digest != expected_charter_digest:
            raise HeadError("a head belongs to another charter")
        if previous is None:
            if head.seq != 1 or head.prev_head_hash != GENESIS_HEAD:
                raise HeadError("the first head does not start the chain")
        elif head.seq != previous.seq + 1 or head.prev_head_hash != previous.digest:
            raise HeadError("the heads do not link")
        previous = head
    if previous is None:
        raise HeadError("no heads")
    return previous


class Authority:
    """The only object that may post to one issuer's journal, and the only one that signs its heads."""

    def __init__(self, path, charter: IssuerCharter, key: SigningKey):
        if key.generation != 0 or key.public_key != charter.genesis_authority:
            raise ValueError("this key is not the charter's authority")
        self._path = Path(path)
        self._charter = charter
        self._key = key
        self._guard = contextlib.ExitStack()
        self._guard.enter_context(exclusive_lock(self._path.with_name(self._path.name + AUTHORITY_LOCK_SUFFIX)))
        try:
            self.journal = IssuerJournal(self._path, charter=charter, authority_held=True)
            self._lock = threading.Lock()
            with contextlib.closing(sqlite3.connect(self._path, timeout=30)) as db:
                db.execute("CREATE TABLE IF NOT EXISTS journal_heads ("
                           "seq INTEGER PRIMARY KEY, head BLOB NOT NULL, signature TEXT NOT NULL)")
                db.commit()
            self._check_existing_heads()
            self._sign_missing()
        except BaseException:
            self._guard.close()
            raise

    @property
    def issuer(self) -> str:
        return self.journal.issuer

    def balances(self) -> dict:
        return self.journal.balances()

    def close(self):
        self._guard.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def heads(self) -> list:
        with contextlib.closing(sqlite3.connect(self._path, timeout=30)) as db:
            return [(bytes(h), s) for h, s in db.execute("SELECT head, signature FROM journal_heads ORDER BY seq")]

    def _halt(self, reason: str) -> None:
        self.journal.halted = reason

    def _check_existing_heads(self) -> None:
        """Before anything new is signed: the heads on file must verify, link, and the last one must agree with the
        journal as it rebuilds now (design 10.3). A disagreement halts every write."""
        if self.journal.halted:
            return
        stored = self.heads()
        if not stored:
            return
        try:
            last = verify_chain(stored, self._key.public_key, charter_digest(self._charter))
        except HeadError:
            return self._halt("the signed heads do not verify")
        if last.seq > self.journal.head():
            return self._halt("the signed heads run past the journal")
        receipt = self.journal.receipt_at(last.seq)
        if (receipt is None or last.receipt_hash != receipt_hash(receipt)
                or last.state_hash != state_hash(self.journal.balances_at(last.seq))):
            return self._halt("the last signed head does not match the journal")

    def _sign_missing(self) -> None:
        """One signed head for every sequence number with none missing: the state at an earlier sequence is rebuilt by
        replaying the effects up to it (design 10.1)."""
        if self.journal.halted:
            return
        with contextlib.closing(sqlite3.connect(self._path, timeout=30)) as db:
            last = db.execute("SELECT seq, head FROM journal_heads ORDER BY seq DESC LIMIT 1").fetchone()
            seq, previous = (last[0], hashlib.sha256(bytes(last[1])).hexdigest()) if last else (0, GENESIS_HEAD)
            for number in range(seq + 1, self.journal.head() + 1):
                receipt = self.journal.receipt_at(number)
                if receipt is None:
                    return self._halt("an effect has no receipt to sign a head for")
                head = Head(self._charter.issuer_id, charter_digest(self._charter), self._key.generation, number,
                            state_hash(self.journal.balances_at(number)), receipt_hash(receipt), previous)
                raw = head.canonical_bytes()
                db.execute("INSERT INTO journal_heads VALUES (?,?,?)", (number, raw, self._key.sign(HEAD_PREFIX + raw)))
                previous = head.digest
            db.commit()

    def _head_at(self, seq: int):
        with contextlib.closing(sqlite3.connect(self._path, timeout=30)) as db:
            row = db.execute("SELECT head, signature FROM journal_heads WHERE seq=?", (seq,)).fetchone()
        return (bytes(row[0]), row[1]) if row else (None, None)

    def execute(self, command: dict) -> tuple[Outcome, SignedReceipt | None]:
        with self._lock:
            outcome, receipt = self.journal.execute_receipt(command)
            if receipt is None:
                return outcome, None
            if outcome is Outcome.POSTED:
                self._sign_missing()
            head, signature = self._head_at(wire.decode(receipt)["seq"])
            return outcome, SignedReceipt(receipt, head, signature)


class ReadOnlyView:
    """What a copy of the journal file shows: balances and heads "as of seq N". It holds no key and
    has no way to post."""

    def __init__(self, path):
        self._uri = Path(path).resolve().as_uri() + "?mode=ro"

    def _db(self):
        return contextlib.closing(sqlite3.connect(self._uri, uri=True, timeout=30))

    def as_of_seq(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COALESCE(MAX(seq), 0) FROM journal_effects").fetchone()[0]

    def balances(self) -> dict:
        with self._db() as db:
            return {a: v for a, v in db.execute("SELECT account, amount FROM journal_balances") if v}

    def heads(self) -> list:
        with self._db() as db:
            return [(bytes(h), s) for h, s in db.execute("SELECT head, signature FROM journal_heads ORDER BY seq")]


class PinOutcome(str, Enum):
    ACCEPTED = "accepted"
    KNOWN = "known"
    EQUIVOCATION = "equivocation"
    REFUSED = "refused"
    INVALID = "invalid"


class HeadPins:
    """The heads a peer has verified, per issuer. Two histories under one key stop that issuer for good and are
    reported once. A new head is accepted only if (design 10.2): no other head is pinned at its sequence with another
    digest; the head pinned just below it is the one it links to; and the head pinned just above it links back to it. A
    head whose neighbours are not pinned yet is accepted unlinked and checked when a neighbour arrives, in any order."""

    def __init__(self, path=None):
        self._path = Path(path) if path else None
        self._seen: dict[str, dict[int, dict]] = {}
        self._stopped: set[str] = set()
        self.reports: list[dict] = []
        if self._path and self._path.exists():
            data = json.loads(self._path.read_text(encoding="ascii"))
            self._seen = {k: {int(s): d for s, d in v.items()} for k, v in data["seen"].items()}
            self._stopped = set(data["stopped"])
            self.reports = data["reports"]

    def _save(self):
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix(".tmp")
            temp.write_text(json.dumps({"seen": self._seen, "stopped": sorted(self._stopped), "reports": self.reports},
                                       sort_keys=True), encoding="ascii")
            os.replace(temp, self._path)

    def _equivocation(self, issuer: str, head: Head, why: str) -> PinOutcome:
        self._stopped.add(issuer)
        self.reports.append({"issuer_id": issuer, "seq": head.seq, "why": why, "head": head.digest})
        self._save()
        return PinOutcome.EQUIVOCATION

    def accept(self, raw: bytes, signature: str, public_key: str, expected_charter_digest: str) -> PinOutcome:
        try:
            head = verify_head(raw, signature, public_key)
        except HeadError:
            return PinOutcome.INVALID
        if head.charter_digest != expected_charter_digest:
            return PinOutcome.INVALID
        issuer = head.issuer_id
        if issuer in self._stopped:
            return PinOutcome.REFUSED
        known = self._seen.setdefault(issuer, {})
        same = known.get(head.seq)
        if same is not None:
            if same["d"] == head.digest:
                return PinOutcome.KNOWN
            return self._equivocation(issuer, head, "another head at this sequence")
        below, above = known.get(head.seq - 1), known.get(head.seq + 1)
        if below is not None and head.prev_head_hash != below["d"]:
            return self._equivocation(issuer, head, "does not link to the pinned head below it")
        if above is not None and above["p"] != head.digest:
            return self._equivocation(issuer, head, "the pinned head above it does not link to this one")
        known[head.seq] = {"d": head.digest, "p": head.prev_head_hash}
        self._save()
        return PinOutcome.ACCEPTED

    def stopped(self, issuer_id: str) -> bool:
        return issuer_id in self._stopped

    def highest(self, issuer_id: str) -> int:
        return max(self._seen.get(issuer_id, {0: ""}))

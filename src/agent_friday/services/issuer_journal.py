"""The test-credit issuer journal and the exchange arithmetic (Federation / Economy spec,
economy section; roadmap F5). TEST CREDITS ONLY: nothing here moves real money, holds a
wallet key, talks to a processor or reads the legacy `services/economy.py` balances (those
are a separate, historical dataset and are never merged into this one).

Two parts, both pure of the network:

* Arithmetic with exact integer rules: the buyer fee on a cash trade is
  floor((C + 200) / 400) of the principal C (0.25% rounded to the nearest unit, applied to
  the CUMULATIVE gross so a split order pays what one order would), the work-side operator
  share is floor(gross * bps / 10000) on the cumulative gross (contributors keep the rest,
  never less than 99% at the default 100 bps), proportional shares go one unit at a time to
  the largest weight / (allocated + 1) with ties broken by key bytes, and a market match is
  the largest whole lot count the buyer's cash covers with its fee.
* `IssuerJournal`: one serialized authority for one issuer ID with a fixed supply. A command
  posts zero-sum integer postings once per economic effect: the same effect again is a
  duplicate (even under a fresh delivery action ID), the same effect key with different
  postings or the same action ID with a different effect is a conflict, an unbalanced
  command or one that would take any account below zero is refused, and total supply never
  changes. The commit is one SQLite transaction, so a crash leaves either all of a posting
  or none of it.

The journal commits balances exclusively; a peer event (services/peer_wire_v2) about a
payment is only a notification of it.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
from enum import Enum
from fractions import Fraction

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.issuer_registry import charter_digest

MAX_UNITS = 2 ** 53 - 1


def cash_fee(minor_units: int) -> int:
    """Buyer fee on a cash principal: 0.25%, rounded to nearest, in the same minor unit."""
    return (minor_units + 200) // 400


def incremental_fee(quantities, calc) -> list[int]:
    """The fee owed by each successive increment, so the total equals calc(total gross)."""
    gross = 0
    fees = []
    for amount in quantities:
        before = calc(gross)
        gross += amount
        fees.append(calc(gross) - before)
    return fees


def operator_share(gross: int, operator_bps: int) -> int:
    if not 0 <= operator_bps <= 100:
        raise ValueError("operator_bps must be 0..100 (the operator never takes more than 1%)")
    return gross * operator_bps // 10000


def allocate(weights: dict, units: int) -> dict:
    """Split `units` whole units in proportion to `weights`, one unit at a time to the key with
    the largest weight / (already allocated + 1); ties go to the first key in raw byte order."""
    paid = dict.fromkeys(weights, 0)
    order = sorted(weights, key=lambda key: key.encode("utf-8"))
    for _ in range(units):
        chosen = max(order, key=lambda key: Fraction(weights[key], paid[key] + 1))
        paid[chosen] += 1
    return paid


def match_lots(case: dict) -> tuple[int, int | None]:
    """(lots, price per lot) for a crossing order pair the buyer can fund, else (0, None)."""
    if case["bid"] < case["ask"]:
        return 0, None
    price = case[case["resting"]]
    lots = min(case["buy_lots"], case["sell_lots"])
    while lots and lots * price + cash_fee(lots * price) > case["cash_available"]:
        lots -= 1
    return lots, (price if lots else None)


class Outcome(str, Enum):
    """What `execute` answers. The values are the strings the bundle's issuer vector expects."""
    POSTED = "posted"
    DUPLICATE = "duplicate"
    ISSUER = "issuer"
    UNITS = "units"
    ACTION_CONFLICT = "action-conflict"
    EFFECT_CONFLICT = "effect-conflict"
    UNBALANCED = "unbalanced"
    INSUFFICIENT = "insufficient"
    CORRECTION_INVALID = "correction-invalid"
    HALTED = "halted"


# Probing the authority's lock is a try-to-take-and-let-go. Two threads probing at once would refuse each other and
# report a writer that does not exist, so probes in one process take turns; a real holder still refuses every probe
# (tests/unit/test_issuer_journal_durable.py::test_threads_of_one_journal_are_never_told_another_writer_holds_it).
_PROBE = threading.Lock()


class AuthorityHeld(RuntimeError):
    """An authority holds this issuer: no other object may write to its journal (design 10.4)."""


#: the lock file an Authority holds for its whole life; every other writer checks it before it writes
AUTHORITY_LOCK_SUFFIX = ".authority"


@contextlib.contextmanager
def exclusive_lock(path):
    """An exclusive, non-blocking OS lock on `path` for the duration of the block; AuthorityHeld if it is held."""
    handle = open(path, "a+b")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            raise AuthorityHeld("another writer holds this issuer's journal") from err
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


RECEIPT_PROTOCOL = "asimov-journal-receipt/v1"
REQUEST_PREFIX = b"asimov-journal-request/v1" + bytes([0])
_NAME = re.compile(r"[ -~]{1,200}")


def _acceptable(command: dict):
    """(effect, action, postings, corrects, reason) when the command is well formed, else None.
    Names are short printable ASCII (the canonical wire form) and amounts are whole checked integers."""
    effect, action, postings = command.get("effect"), command.get("action"), command.get("postings")
    corrects, reason = command.get("corrects"), command.get("reason")
    if not isinstance(postings, dict) or not postings:
        return None
    for name in (effect, action, *postings):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            return None
    if not all(type(v) is int and abs(v) <= MAX_UNITS for v in postings.values()) or not any(postings.values()):
        return None
    for optional in (corrects, reason):
        if optional is not None and (not isinstance(optional, str) or not _NAME.fullmatch(optional)):
            return None
    return effect, action, postings, corrects, reason


def request_hash(issuer: str, effect: str, postings: dict, corrects, reason) -> str:
    """SHA-256 of the economic request: issuer, effect, the sorted postings, and the correction link.
    The delivery action id is not in it, so a retry under a fresh action id is the same request."""
    body = {"issuer": issuer, "effect": effect, "corrects": corrects, "reason": reason,
            "postings": [[account, str(amount)] for account, amount in sorted(postings.items())]}
    return hashlib.sha256(REQUEST_PREFIX + wire.canonical(body)).hexdigest()


def _receipt(issuer: str, effect: str, seq: int, digest: str, corrects, reason) -> bytes:
    return wire.canonical({"protocol": RECEIPT_PROTOCOL, "issuer": issuer, "effect": effect, "seq": seq,
                           "request_hash": digest, "corrects": corrects, "reason": reason})


class IssuerJournal:
    """One issuer's fixed-supply test-credit ledger in a SQLite file (WAL, full sync).

    Every effect gets the next sequence number, a request hash, a stored receipt and an outbox row
    inside the one transaction that moves the balances: a crash leaves all of it or none. A
    duplicate returns the stored receipt, never a new one. A correction is a new effect that names
    an existing one and a reason; the original is never changed. On open the balances are rebuilt
    from the effects and compared with the stored ones: a mismatch halts every write (reads work).

    Rules and the tests that hold them (tests/unit/test_issuer_journal_durable.py):
    - one transaction per command, all or nothing: test_a_crash_at_every_point_leaves_all_or_none.
    - a duplicate returns the original receipt bytes: test_a_duplicate_under_a_fresh_action_returns_the_original_receipt.
    - sequence is gapless and replay reproduces the balances:
      test_every_short_sequence_keeps_supply_sequence_and_replay.
    - a correction names an existing effect and a reason: test_a_correction_names_an_existing_effect_and_a_reason.
    - tampering halts writes: test_tampering_halts_every_write_and_reads_still_work.
    - the outbox never re-runs a debit: test_an_outbox_consumer_that_fails_does_not_repeat_the_debit.
    - writes are durable before 'posted': test_posted_means_durable_after_the_process_is_killed.
    - WAL and full sync are on: test_the_journal_is_write_ahead_logged_with_full_sync.
    """

    def __init__(self, path, issuer: str | None = None, supply: int | None = None,
                 treasury: str = "treasury", *, charter=None, authority_held: bool = False):
        # The (issuer, supply) form is kept only because the bundle's issuer vector builds a journal that way
        # (test_the_issuer_vector_gives_the_oracles_answer_for_every_command) and #842 and #852 still call it; new
        # code passes a charter.
        digest = None
        if charter is not None:
            if issuer is not None or supply is not None:
                raise TypeError("give a charter, or an issuer and a supply, not both")
            issuer, supply, treasury = charter.issuer_id, charter.genesis_supply, charter.treasury_account
            digest = charter_digest(charter)
        if type(supply) is not int or not 0 <= supply <= MAX_UNITS:
            raise ValueError("supply must be a whole number of units")
        self._path = str(path)
        self._authority_held = authority_held      # only issuer_authority.Authority passes True, after taking the lock
        self.issuer = issuer
        self.supply = supply
        self._lock = threading.Lock()
        self._guard_writes()
        self._point = lambda name: None
        self.halted: str | None = None
        with contextlib.closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS journal_meta (issuer TEXT PRIMARY KEY, supply INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS journal_balances (account TEXT PRIMARY KEY, amount INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS journal_effects (
                    effect TEXT PRIMARY KEY, postings TEXT NOT NULL, posted_at REAL NOT NULL DEFAULT (strftime('%s','now')));
                CREATE TABLE IF NOT EXISTS journal_actions (action TEXT PRIMARY KEY, effect TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS journal_receipts (
                    effect TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, receipt BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS journal_outbox (
                    effect TEXT PRIMARY KEY, seq INTEGER NOT NULL, delivered_at REAL);
            """)
            self._add_columns(db, "journal_meta", (("charter_digest", "TEXT"), ("unit_code", "TEXT"),
                                                   ("precision", "INTEGER"), ("treasury", "TEXT")))
            self._add_columns(db, "journal_effects", (("seq", "INTEGER"), ("request_hash", "TEXT"),
                                                      ("corrects", "TEXT"), ("reason", "TEXT")))
            row = db.execute("SELECT supply, charter_digest, unit_code, precision FROM journal_meta WHERE issuer=?",
                             (issuer,)).fetchone()
            if row is None:
                if db.execute("SELECT 1 FROM journal_meta").fetchone():
                    raise ValueError("this journal file belongs to another issuer")
                db.execute("INSERT INTO journal_meta (issuer, supply, charter_digest, unit_code, precision, treasury) "
                           "VALUES (?,?,?,?,?,?)",
                           (issuer, supply, digest, charter.unit_code if charter else None,
                            charter.precision if charter else None, treasury))
                db.execute("INSERT INTO journal_balances VALUES (?,?)", (treasury, supply))
            else:
                if row[0] != supply:
                    raise ValueError("this journal was created with a different supply")
                stored = (row[1], row[2], row[3])
                given = (digest, charter.unit_code, charter.precision) if charter else (None, None, None)
                if stored != given:
                    raise ValueError("this journal belongs to another charter" if stored[0]
                                     else "this journal was made without a charter")
                db.execute("UPDATE journal_meta SET treasury=? WHERE issuer=? AND treasury IS NULL",
                           (treasury, issuer))
            self._backfill(db)
            db.commit()
        self.recover()

    def _guard_writes(self) -> None:
        """Every write path starts here: while an Authority holds the issuer, nothing else may write (design 10.4).
        The check is made before each write; a writer already inside its one transaction when the Authority starts
        finishes that transaction, which the Authority's own recovery then checks."""
        if not self._authority_held:
            with _PROBE, exclusive_lock(self._path + AUTHORITY_LOCK_SUFFIX):    # one prober at a time: see _PROBE
                pass

    def balances_at(self, seq: int) -> dict:
        """The balances as they stood after effect number `seq`, rebuilt by replaying the effects up to it."""
        with contextlib.closing(self._connect()) as db:
            treasury = (db.execute("SELECT treasury FROM journal_meta WHERE issuer=?", (self.issuer,)).fetchone() or [None])[0]
            rebuilt = {treasury or "treasury": self.supply}
            for postings, in db.execute("SELECT postings FROM journal_effects WHERE seq<=? ORDER BY seq", (seq,)):
                for account, amount in json.loads(postings):
                    rebuilt[account] = rebuilt.get(account, 0) + amount
        return {a: v for a, v in rebuilt.items() if v}

    def receipt_at(self, seq: int) -> bytes | None:
        """The stored receipt bytes of the effect at `seq`, or None."""
        with contextlib.closing(self._connect()) as db:
            row = db.execute("SELECT receipt FROM journal_receipts WHERE seq=?", (seq,)).fetchone()
        return None if row is None else bytes(row[0])

    @staticmethod
    def _add_columns(db, table, columns):
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns:
            if column not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def _backfill(self, db):
        """Effects written before sequence numbers existed get them, in the order they were posted."""
        old = db.execute("SELECT effect, postings, corrects, reason FROM journal_effects "
                         "WHERE seq IS NULL ORDER BY rowid").fetchall()
        if not old:
            return
        top = db.execute("SELECT COALESCE(MAX(seq), 0) FROM journal_effects").fetchone()[0]
        for offset, (effect, postings, corrects, reason) in enumerate(old, 1):
            digest = request_hash(self.issuer, effect, {a: v for a, v in json.loads(postings)}, corrects, reason)
            db.execute("UPDATE journal_effects SET seq=?, request_hash=? WHERE effect=?", (top + offset, digest, effect))
            db.execute("INSERT OR IGNORE INTO journal_receipts VALUES (?,?,?)",
                       (effect, top + offset, _receipt(self.issuer, effect, top + offset, digest, corrects, reason)))

    def _connect(self):
        db = sqlite3.connect(self._path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextlib.contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def balances(self) -> dict:
        with contextlib.closing(self._connect()) as db:
            return {a: v for a, v in db.execute("SELECT account, amount FROM journal_balances") if v}

    def head(self) -> int:
        """The highest sequence number posted (0 for none)."""
        with contextlib.closing(self._connect()) as db:
            top = db.execute("SELECT COALESCE(MAX(seq), 0) FROM journal_effects").fetchone()[0]
        return top if type(top) is int else 0        # a damaged sequence column reads as nothing posted

    def recover(self) -> str | None:
        """Check everything that can be recomputed, in sequence order. A mismatch, or any stored row that cannot be
        decoded, halts writes and returns why; recovery never raises for damaged data (design 10.3)."""
        try:
            problem = self._recover_problem()
        except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError,
                wire.WireError):
            problem = "a stored row cannot be read"
        self.halted = problem
        return problem

    def _recover_problem(self) -> str | None:
        with contextlib.closing(self._connect()) as db:
            meta = db.execute("SELECT treasury FROM journal_meta WHERE issuer=?", (self.issuer,)).fetchone()
            rebuilt = {(meta[0] if meta and meta[0] else "treasury"): self.supply}
            effects = db.execute("SELECT effect, postings, seq, request_hash, corrects, reason "
                                 "FROM journal_effects ORDER BY seq").fetchall()
            receipts = {e: (s, bytes(r)) for e, s, r in db.execute("SELECT effect, seq, receipt FROM journal_receipts")}
            if len(receipts) != len(effects):
                return "the receipts and the effects do not match"
            for number, (effect, postings, seq, stored_hash, corrects, reason) in enumerate(effects, 1):
                if type(seq) is not int or seq != number:
                    return "the sequence has a gap or a repeat"
                rows = json.loads(postings)
                if (not isinstance(rows, list) or not all(isinstance(r, list) and len(r) == 2 and isinstance(r[0], str)
                                                         and type(r[1]) is int for r in rows)):
                    return "an effect's postings cannot be read"
                digest = request_hash(self.issuer, effect, dict(rows), corrects, reason)
                if stored_hash != digest:
                    return "an effect no longer hashes to the request that was posted"
                have = receipts.get(effect)
                if have is None or have[0] != seq or have[1] != _receipt(self.issuer, effect, seq, digest, corrects, reason):
                    return "an effect has no matching receipt"
                for account, amount in rows:
                    rebuilt[account] = rebuilt.get(account, 0) + amount
            stored = {a: v for a, v in db.execute("SELECT account, amount FROM journal_balances") if v}
        rebuilt = {a: v for a, v in rebuilt.items() if v}
        if min(rebuilt.values(), default=0) < 0 or sum(rebuilt.values()) != self.supply:
            return "the effects do not keep the supply"
        if rebuilt != stored:
            return "the stored balances do not match the effects"
        return None

    def outbox_pending(self, limit: int = 100) -> list[dict]:
        """Committed effects whose notification has not been delivered, oldest first."""
        with contextlib.closing(self._connect()) as db:
            rows = db.execute("SELECT o.effect, o.seq, r.receipt FROM journal_outbox o JOIN journal_receipts r "
                              "ON r.effect=o.effect WHERE o.delivered_at IS NULL ORDER BY o.seq LIMIT ?",
                              (limit,)).fetchall()
        return [{"effect": e, "seq": s, "receipt": bytes(r)} for e, s, r in rows]

    def outbox_delivered(self, effect: str) -> None:
        if self.halted:
            return                  # a halted journal writes nothing, not even a delivery mark
        self._guard_writes()
        with self._transaction() as db:
            db.execute("UPDATE journal_outbox SET delivered_at=strftime('%s','now') WHERE effect=? "
                       "AND delivered_at IS NULL", (effect,))

    def execute(self, command: dict) -> Outcome:
        return self.execute_receipt(command)[0]

    def execute_receipt(self, command: dict) -> tuple[Outcome, bytes | None]:
        """One command {issuer, effect, action, postings[, corrects, reason]}. Returns the outcome and,
        for posted and duplicate, the stored receipt bytes."""
        self._guard_writes()
        if self.halted:
            return Outcome.HALTED, None
        if not isinstance(command, dict) or command.get("issuer") != self.issuer:
            return Outcome.ISSUER, None
        parsed = _acceptable(command)
        if parsed is None:
            return Outcome.UNITS, None
        effect_key, action, postings, corrects, reason = parsed
        encoded = json.dumps(sorted(postings.items()), separators=(",", ":"))
        digest = request_hash(self.issuer, effect_key, postings, corrects, reason)
        with self._lock, self._transaction() as db:
            known = db.execute("SELECT effect FROM journal_actions WHERE action=?", (action,)).fetchone()
            stored = db.execute("SELECT postings, request_hash FROM journal_effects WHERE effect=?",
                                (effect_key,)).fetchone()
            if known:
                first = db.execute("SELECT postings FROM journal_effects WHERE effect=?", (known[0],)).fetchone()
                if known[0] != effect_key or first is None or first[0] != encoded:
                    return Outcome.ACTION_CONFLICT, None
            if stored is not None:
                if stored[0] != encoded or stored[1] != digest:
                    return Outcome.EFFECT_CONFLICT, None
                db.execute("INSERT OR IGNORE INTO journal_actions VALUES (?,?)", (action, effect_key))
                receipt = db.execute("SELECT receipt FROM journal_receipts WHERE effect=?", (effect_key,)).fetchone()
                return Outcome.DUPLICATE, bytes(receipt[0])
            if sum(postings.values()) != 0:
                return Outcome.UNBALANCED, None
            if (corrects is None) != (reason is None) or (
                    corrects is not None and db.execute("SELECT 1 FROM journal_effects WHERE effect=?",
                                                        (corrects,)).fetchone() is None):
                return Outcome.CORRECTION_INVALID, None
            future = {}
            for account, amount in postings.items():
                row = db.execute("SELECT amount FROM journal_balances WHERE account=?", (account,)).fetchone()
                future[account] = (row[0] if row else 0) + amount
            if min(future.values(), default=0) < 0:
                return Outcome.INSUFFICIENT, None
            self._point("validated")
            for account, amount in future.items():
                db.execute("INSERT INTO journal_balances VALUES (?,?) "
                           "ON CONFLICT(account) DO UPDATE SET amount=excluded.amount", (account, amount))
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM journal_effects").fetchone()[0]
            db.execute("INSERT INTO journal_effects (effect, postings, seq, request_hash, corrects, reason) "
                       "VALUES (?,?,?,?,?,?)", (effect_key, encoded, seq, digest, corrects, reason))
            db.execute("INSERT INTO journal_actions VALUES (?,?)", (action, effect_key))
            self._point("applied")
            receipt = _receipt(self.issuer, effect_key, seq, digest, corrects, reason)
            db.execute("INSERT INTO journal_receipts VALUES (?,?,?)", (effect_key, seq, receipt))
            self._point("receipt")
            db.execute("INSERT INTO journal_outbox (effect, seq) VALUES (?,?)", (effect_key, seq))
            self._point("outbox")
            return Outcome.POSTED, receipt

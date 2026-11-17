"""The local test double for a payment provider (design D.6). SYNTHETIC CASH ONLY.

Its descriptor says test_only=True and its id is "synthetic", so it can never be taken for a live
provider. It keeps its own books in a SQLite file (they survive a restart of the node under test),
signs its events with a test key so `authenticate_event` is exercised for real, and is deterministic:
it has no clock or randomness of its own, and time and faults are injected.

Rules and the tests that hold them (tests/unit/test_exchange_fake_provider.py, which also runs the
shared contract suite in services/exchange/contract.py):
- Every call with the same key is the same request and returns the stored answer:
  ProviderContract.test_a_repeated_key_is_one_request.
- A fault does what its name says: test_each_fault_does_what_it_says.
- The double's books survive a restart: test_the_books_survive_a_restart.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from enum import Enum
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.exchange.provider import (
    BuyingPower, CashReserved, Captured, Forged, PayoutPending, ProviderDescriptor, ProviderEvent,
    ProviderStatus, Refunded, Refused, RefusalCode, Released, Uncertain)

EVENT_PREFIX = b"asimov-fake-provider/v1/event" + bytes([0])
PROVIDER_ID = "synthetic"


class Fault(str, Enum):
    TIMEOUT_AFTER_EFFECT = "timeout-after-effect"
    TIMEOUT_BEFORE_EFFECT = "timeout-before-effect"
    DUPLICATE_EVENT = "duplicate-event"
    REORDERED_EVENTS = "reordered-events"
    FORGED_EVENT = "forged-event"
    OUTAGE = "outage"
    PAYOUT_RETURNED = "payout-returned"
    CHARGEBACK = "chargeback"
    EXPIRED_BUYING_POWER = "expired-buying-power"


class FakeProvider:
    def __init__(self, path, signing_seed: bytes, clock: Callable[[], int], *, synchronous: str = "FULL"):
        if synchronous not in ("FULL", "NORMAL", "OFF"):
            raise ValueError("synchronous is FULL, NORMAL or OFF")
        self._synchronous = synchronous
        self._path = str(path)
        self._clock = clock
        self._key = Ed25519PrivateKey.from_private_bytes(signing_seed)
        self.public_key = self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        self._armed: dict[Fault, dict] = {}
        self._returning: set[str] = set()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS power (ref TEXT PRIMARY KEY, payer TEXT, currency TEXT, amount INTEGER,
                    expires_at INTEGER, reserved INTEGER NOT NULL DEFAULT 0, expired INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS ops (key TEXT PRIMARY KEY, kind TEXT, request TEXT, status TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS outbox (n INTEGER PRIMARY KEY AUTOINCREMENT, raw BLOB, delivered INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS counter (n INTEGER);
            """)
            db.commit()

    @contextlib.contextmanager
    def _db(self):
        db = sqlite3.connect(self._path, timeout=30)
        db.execute(f"PRAGMA synchronous={self._synchronous}")
        try:
            yield db
        finally:
            db.close()

    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(PROVIDER_ID, True, ("USD",), 1, "provider", True)

    def arm(self, fault: Fault, **options) -> None:
        """Script one fault. OUTAGE takes `until=<clock value>`; the others fire once, on the first
        opportunity or on a later one with `after=<opportunities to let pass>`."""
        self._armed[Fault(fault)] = dict(options)

    def _fires(self, fault: Fault) -> bool:
        """True on the opportunity the fault was armed for: the first, or the (after+1)th with `after=n`."""
        options = self._armed.get(fault)
        if options is None:
            return False
        if options.get("after", 0) > 0:
            options["after"] -= 1
            return False
        del self._armed[fault]
        return True

    def _outage(self) -> bool:
        options = self._armed.get(Fault.OUTAGE)
        if options is None:
            return False
        if self._clock() >= options.get("until", 0):
            del self._armed[Fault.OUTAGE]
            return False
        return True

    def fund(self, ref: str, payer: str, currency: str, amount_minor: int, expires_at: int) -> None:
        """Synthetic cash a buyer may spend: the double's only source of money."""
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO power VALUES (?,?,?,?,?,0,0)",
                       (ref, payer, currency, amount_minor, expires_at))
            db.commit()

    def _emit(self, db, key: str, status: ProviderStatus, *, forged=False, message=None):
        n = db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] + 1
        body = {"key": key, "message_id": message or f"m{n}", "status": status.value}
        sig = self._key.sign(EVENT_PREFIX + wire.canonical(body)).hex()
        if forged:
            sig = "00" * 64
        db.execute("INSERT INTO outbox (raw) VALUES (?)", (wire.canonical({**body, "sig": sig}),))

    def _events(self, db, key: str, status: ProviderStatus):
        self._emit(db, key, status, forged=self._fires(Fault.FORGED_EVENT))
        if self._fires(Fault.DUPLICATE_EVENT):
            self._emit(db, key, status)

    def deliver(self) -> list[bytes]:
        """The events the provider has sent since the last call. REORDERED_EVENTS reverses a batch."""
        with self._db() as db:
            rows = db.execute("SELECT n, raw FROM outbox WHERE delivered=0 ORDER BY n").fetchall()
            db.execute("UPDATE outbox SET delivered=1 WHERE delivered=0")
            db.commit()
        raws = [bytes(raw) for _, raw in rows]
        if len(raws) > 1 and self._fires(Fault.REORDERED_EVENTS):
            raws.reverse()
        return raws

    def authenticate_event(self, raw: bytes):
        try:
            body = wire.decode(raw)
            sig = body.pop("sig")
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.public_key)).verify(
                bytes.fromhex(sig), EVENT_PREFIX + wire.canonical(body))
            return ProviderEvent(body["message_id"], body["key"], ProviderStatus(body["status"]))
        except (wire.WireError, KeyError, ValueError, InvalidSignature, AttributeError, TypeError):
            return Forged()

    # -- operations -------------------------------------------------------------------------

    def _once(self, key, kind, request, effect):
        """Run `effect(db)` once per key. The same key and request returns the stored answer; the same key
        with another request is refused. Faults around the effect make the answer Uncertain."""
        if self._outage():
            return Refused(RefusalCode.OUTAGE)
        request = json.dumps(request, sort_keys=True)
        with self._db() as db:
            row = db.execute("SELECT kind, request, data FROM ops WHERE key=?", (key,)).fetchone()
            if row is not None:
                if row[0] != kind or row[1] != request:
                    return Refused(RefusalCode.INVALID)
                return self._revive(json.loads(row[2]))
            if self._fires(Fault.TIMEOUT_BEFORE_EFFECT):
                return Uncertain(key)
            answer = effect(db)
            if not isinstance(answer, tuple):
                return answer
            result, status = answer
            db.execute("INSERT INTO ops VALUES (?,?,?,?,?)", (key, kind, request, status.value, json.dumps(result)))
            self._events(db, key, status)
            db.commit()
            if self._fires(Fault.TIMEOUT_AFTER_EFFECT):
                return Uncertain(key)
            return self._revive(result)

    @staticmethod
    def _revive(result):
        kinds = {"reserved": CashReserved, "captured": Captured, "released": Released,
                 "payout": PayoutPending, "refunded": Refunded}
        kind = result.pop("kind") if "kind" in result else None
        result = dict(result)
        return kinds[kind](**result)

    def verify_buying_power(self, ref: str):
        if self._outage():
            return Refused(RefusalCode.OUTAGE)
        with self._db() as db:
            row = db.execute("SELECT payer, currency, amount, expires_at, reserved, expired FROM power WHERE ref=?",
                             (ref,)).fetchone()
            if row is None:
                return Refused(RefusalCode.UNKNOWN_REFERENCE)
            payer, currency, amount, expires_at, reserved, expired = row
            if expired or expires_at <= self._clock():
                return Refused(RefusalCode.EXPIRED)
            if self._fires(Fault.EXPIRED_BUYING_POWER):
                db.execute("UPDATE power SET expired=1 WHERE ref=?", (ref,))
                db.commit()
            return BuyingPower(ref, payer, currency, amount - reserved, expires_at, self._clock())

    def reserve_cash(self, key, buying_power_ref, amount_minor, currency):
        def effect(db):
            row = db.execute("SELECT currency, amount, reserved, expires_at, expired FROM power WHERE ref=?",
                             (buying_power_ref,)).fetchone()
            if row is None:
                return Refused(RefusalCode.UNKNOWN_REFERENCE)
            if row[4] or row[3] <= self._clock():
                return Refused(RefusalCode.EXPIRED)
            if row[0] != currency:
                return Refused(RefusalCode.CURRENCY)
            if amount_minor < 1 or row[1] - row[2] < amount_minor:
                return Refused(RefusalCode.INSUFFICIENT)
            db.execute("UPDATE power SET reserved=reserved+? WHERE ref=?", (amount_minor, buying_power_ref))
            return ({"kind": "reserved", "key": key, "reservation_ref": "res-" + key, "amount_minor": amount_minor},
                    ProviderStatus.RESERVED)
        return self._once(key, "reserve", [buying_power_ref, amount_minor, currency], effect)

    def _reservation(self, db, reservation_ref):
        if not reservation_ref.startswith("res-"):
            return None
        row = db.execute("SELECT request, status FROM ops WHERE key=? AND kind='reserve'",
                         (reservation_ref[4:],)).fetchone()
        return None if row is None else (json.loads(row[0]), row[1], reservation_ref[4:])

    def capture(self, key, reservation_ref, principal_minor, fee_minor):
        def effect(db):
            found = self._reservation(db, reservation_ref)
            if found is None or found[1] != ProviderStatus.RESERVED.value:
                return Refused(RefusalCode.INVALID)
            (power_ref, reserved_amount, _), _, reserve_key = found
            if principal_minor < 1 or fee_minor < 0 or principal_minor + fee_minor > reserved_amount:
                return Refused(RefusalCode.INSUFFICIENT)
            db.execute("UPDATE ops SET status=? WHERE key=?", (ProviderStatus.CAPTURED.value, reserve_key))
            db.execute("UPDATE power SET amount=amount-?, reserved=reserved-? WHERE ref=?",
                       (principal_minor + fee_minor, reserved_amount, power_ref))
            return ({"kind": "captured", "key": key, "capture_ref": "cap-" + key,
                     "principal_minor": principal_minor, "fee_minor": fee_minor}, ProviderStatus.CAPTURED)
        return self._once(key, "capture", [reservation_ref, principal_minor, fee_minor], effect)

    def release(self, key, reservation_ref, amount_minor):
        def effect(db):
            found = self._reservation(db, reservation_ref)
            if found is None or found[1] != ProviderStatus.RESERVED.value or found[0][1] != amount_minor:
                return Refused(RefusalCode.INVALID)
            (power_ref, reserved_amount, _), _, reserve_key = found
            db.execute("UPDATE ops SET status=? WHERE key=?", (ProviderStatus.RELEASED.value, reserve_key))
            db.execute("UPDATE power SET reserved=reserved-? WHERE ref=?", (reserved_amount, power_ref))
            return {"kind": "released", "key": key, "amount_minor": amount_minor}, ProviderStatus.RELEASED
        return self._once(key, "release", [reservation_ref, amount_minor], effect)

    def _capture(self, db, capture_ref):
        if not capture_ref.startswith("cap-"):
            return None
        row = db.execute("SELECT data, status FROM ops WHERE key=? AND kind='capture'", (capture_ref[4:],)).fetchone()
        return None if row is None else (json.loads(row[0]), row[1], capture_ref[4:])

    def payout(self, key, seller_ref, amount_minor):
        with self._db() as db:
            row = db.execute("SELECT status, request FROM ops WHERE key=? AND kind='payout'", (key,)).fetchone()
            if row and row[0] == ProviderStatus.PAYOUT_RETURNED.value and row[1] == json.dumps(
                    [seller_ref, amount_minor], sort_keys=True):
                db.execute("UPDATE ops SET status=? WHERE key=?", (ProviderStatus.PAYOUT_PENDING.value, key))
                self._events(db, key, ProviderStatus.PAYOUT_PENDING)
                db.commit()

        def effect(db):
            if amount_minor < 1 or db.execute("SELECT COUNT(*) FROM ops WHERE kind='capture'").fetchone()[0] == 0:
                return Refused(RefusalCode.INVALID)
            result = {"kind": "payout", "key": key, "payout_ref": "pay-" + key, "amount_minor": amount_minor}
            if self._fires(Fault.PAYOUT_RETURNED):
                self._returning.add(key)
            return result, ProviderStatus.PAYOUT_PENDING
        return self._once(key, "payout", [seller_ref, amount_minor], effect)

    def settle_payout(self, key: str) -> None:
        """The provider finishes a payout: confirmed, or returned when PAYOUT_RETURNED was armed for it."""
        with self._db() as db:
            returned = key in self._returning
            self._returning.discard(key)
            status = ProviderStatus.PAYOUT_RETURNED if returned else ProviderStatus.PAYOUT_CONFIRMED
            db.execute("UPDATE ops SET status=? WHERE key=? AND kind='payout'", (status.value, key))
            self._events(db, key, status)
            db.commit()

    def chargeback(self, capture_key: str) -> None:
        """The buyer's side reverses a capture after the fact."""
        with self._db() as db:
            db.execute("UPDATE ops SET status=? WHERE key=? AND kind='capture'",
                       (ProviderStatus.REVERSED.value, capture_key))
            self._events(db, capture_key, ProviderStatus.REVERSED)
            db.commit()

    def refund(self, key, capture_ref, amount_minor, reason):
        def effect(db):
            found = self._capture(db, capture_ref)
            if found is None or found[1] not in (ProviderStatus.CAPTURED.value, ProviderStatus.REVERSED.value):
                return Refused(RefusalCode.INVALID)
            captured = found[0]["principal_minor"] + found[0]["fee_minor"]
            if not 1 <= amount_minor <= captured:
                return Refused(RefusalCode.INSUFFICIENT)
            return ({"kind": "refunded", "key": key, "refund_ref": "ref-" + key, "amount_minor": amount_minor},
                    ProviderStatus.REFUNDED)
        return self._once(key, "refund", [capture_ref, amount_minor, reason], effect)

    def lookup(self, key: str) -> ProviderStatus:
        if self._outage():
            return ProviderStatus.UNREACHABLE
        with self._db() as db:
            row = db.execute("SELECT status FROM ops WHERE key=?", (key,)).fetchone()
        return ProviderStatus.UNKNOWN if row is None else ProviderStatus(row[0])

    def books(self) -> dict:
        """What the double holds, to the minor unit: for reconciliation tests."""
        with self._db() as db:
            power = {r[0]: {"amount": r[1], "reserved": r[2]} for r in db.execute("SELECT ref, amount, reserved FROM power")}
            ops = {r[0]: [r[1], r[2]] for r in db.execute("SELECT key, kind, status FROM ops")}
        return {"power": power, "ops": ops}

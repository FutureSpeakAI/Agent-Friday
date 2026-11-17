"""The durable settlement workflow for one fill (NS-40.4-13, design D.5). SYNTHETIC CASH AND TEST
CREDITS ONLY. No provider is named here: it works against the Provider protocol.

One row per fill in a SQLite file, moved one transaction per step. Provider calls go out through an
outbox row with a stable idempotency key; provider events come in through an inbox that
authenticates them first. Nothing is held in memory between calls, so a node that is killed after
any step reopens the file and carries on.

States and the one table of allowed moves are below; anything not in the table is refused.

Invariants and the tests that hold them (tests/unit/test_exchange_settlement.py):
- S-1 a fill's P ends in exactly one place, buyer or seller:
  test_every_crash_point_under_every_fault_converges_and_keeps_the_invariants.
- S-2 a fill's cash reservation ends captured or released, never both or neither: same test.
- S-3 the P transfer and the fee each happen at most once per fill: same test, and
  test_the_fee_is_charged_at_one_place_only.
- S-4 a payout in flight, returned or held is shown as an outstanding liability: test_outstanding_liabilities_are_shown.
- S-5 a refund is its own record and never edits the fill: test_a_refund_is_its_own_leg_and_returns_p_once.
- S-8 cash is never a balance in the P journal: test_cash_never_appears_in_the_p_journal.
- Uncertain is never failure or success: test_an_uncertain_capture_is_looked_up_never_released.
- A forged or unauthenticated event changes nothing and is recorded as refused:
  test_a_forged_event_changes_nothing_and_is_recorded.
- A copied signed receipt replayed on another node pays nothing: test_a_replayed_fill_on_another_node_pays_nothing.
- Fee accrual has one call site: test_the_fee_is_charged_at_one_place_only.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum

from agent_friday.services.exchange import fees
from agent_friday.services.exchange import provider as p
from agent_friday.services.exchange.orders import ExchangeId, Fill, FillId, _need
from agent_friday.services.issuer_journal import Outcome


class State(str, Enum):
    MATCHED = "matched"
    CAPTURE_REQUESTED = "capture-requested"
    CASH_CONFIRMED = "cash-confirmed"
    P_TRANSFERRED = "p-transferred"
    PAYOUT_REQUESTED = "payout-requested"
    PAYOUT_CONFIRMED = "payout-confirmed"
    PAYOUT_RETURNED = "payout-returned"
    RELEASED = "released"
    HELD = "held"
    REFUNDED = "refunded"


S = State
TRANSITIONS = {
    S.MATCHED: {S.CAPTURE_REQUESTED, S.RELEASED},
    S.CAPTURE_REQUESTED: {S.CASH_CONFIRMED, S.RELEASED},
    S.CASH_CONFIRMED: {S.P_TRANSFERRED, S.HELD},
    S.P_TRANSFERRED: {S.PAYOUT_REQUESTED, S.HELD},
    S.PAYOUT_REQUESTED: {S.PAYOUT_CONFIRMED, S.PAYOUT_RETURNED, S.HELD},
    S.PAYOUT_RETURNED: {S.PAYOUT_REQUESTED, S.HELD},
    S.PAYOUT_CONFIRMED: {S.HELD},
    S.HELD: {S.REFUNDED},
    S.RELEASED: set(),
    S.REFUNDED: set(),
}
TERMINAL = frozenset({S.PAYOUT_CONFIRMED, S.RELEASED, S.REFUNDED})
FEE_ACCRUES_AT = S.CASH_CONFIRMED


class IllegalTransition(RuntimeError):
    pass


class OpenResult(str, Enum):
    OPENED = "opened"
    ALREADY = "already"
    REFUSED = "refused"
    PENDING = "pending"


class CancelResult(str, Enum):
    CANCELLED = "cancelled"
    NOT_ALLOWED = "not-allowed"
    PENDING = "pending"


@dataclass(frozen=True)
class Accounts:
    """The journal accounts one fill moves P between."""
    reserve: str
    seller: str
    buyer: str


@dataclass(frozen=True)
class Parties:
    buying_power_ref: str
    seller_ref: str
    currency: str


def _effect(fill_id: str, what: str) -> str:
    return f"fill:{fill_id}:{what}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY, exchange_id TEXT NOT NULL, state TEXT NOT NULL, quote_hash TEXT NOT NULL,
    p_units INTEGER NOT NULL, principal INTEGER NOT NULL, fee_increment INTEGER NOT NULL,
    fee_charged INTEGER NOT NULL DEFAULT 0, reserve_account TEXT, seller_account TEXT, buyer_account TEXT,
    buying_power_ref TEXT, seller_ref TEXT, currency TEXT, cash_reservation_ref TEXT, capture_ref TEXT,
    p_at TEXT NOT NULL DEFAULT 'reserve', cash_amount INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS legs (
    key TEXT PRIMARY KEY, fill_id TEXT NOT NULL, leg TEXT NOT NULL, status TEXT NOT NULL, answer TEXT);
CREATE TABLE IF NOT EXISTS inbox (
    message_id TEXT PRIMARY KEY, key TEXT, outcome TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history (
    n INTEGER PRIMARY KEY AUTOINCREMENT, fill_id TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL);
"""


class Settlement:
    """The workflow over one store, one P journal and one provider."""

    def __init__(self, path, journal, provider, *, synchronous: str = "FULL"):
        if synchronous not in ("FULL", "NORMAL", "OFF"):
            raise ValueError("synchronous is FULL, NORMAL or OFF")
        self._synchronous = synchronous
        self._path = str(path)
        self._journal = journal
        self._provider = provider
        self._point = lambda name: None
        with contextlib.closing(sqlite3.connect(self._path, timeout=30)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.commit()

    # -- storage helpers --------------------------------------------------------------------

    @contextlib.contextmanager
    def _tx(self):
        db = sqlite3.connect(self._path, timeout=getattr(self, "_tx_timeout", 30))
        db.execute(f"PRAGMA synchronous={self._synchronous}")
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _row(self, db, fill_id):
        return db.execute("SELECT * FROM fills WHERE fill_id=?", (fill_id,)).fetchone()

    def state(self, fill_id: str) -> State | None:
        with self._tx() as db:
            row = self._row(db, fill_id)
        return None if row is None else State(row["state"])

    def _move(self, db, fill_id: str, to: State):
        row = self._row(db, fill_id)
        before = State(row["state"])
        if to not in TRANSITIONS[before]:
            raise IllegalTransition(f"{before.value} -> {to.value}")
        db.execute("UPDATE fills SET state=? WHERE fill_id=?", (to.value, fill_id))
        db.execute("INSERT INTO history (fill_id, from_state, to_state) VALUES (?,?,?)",
                   (fill_id, before.value, to.value))

    def _enqueue(self, db, fill_id: str, leg: p.Leg, scope: str = "once"):
        row = self._row(db, fill_id)
        key = p.idempotency_key(row["exchange_id"], fill_id, leg, scope)
        db.execute("INSERT OR IGNORE INTO legs VALUES (?,?,?,?,NULL)", (key, fill_id, leg.value, "pending"))
        return key

    def _key(self, row, leg: p.Leg, scope="once") -> str:
        return p.idempotency_key(row["exchange_id"], row["fill_id"], leg, scope)

    def _post(self, command: dict) -> Outcome:
        outcome = self._journal.execute(command)
        return outcome[0] if isinstance(outcome, tuple) else outcome

    def _move_p(self, row, what: str, source: str, target: str, to_where: str) -> bool:
        """One journal posting of this fill's P, once. True when P is now where it should be."""
        if row["p_units"] == 0:
            return True
        effect = _effect(row["fill_id"], what)
        outcome = self._post({"issuer": self._journal.issuer, "effect": effect, "action": effect,
                              "postings": {source: -row["p_units"], target: row["p_units"]}})
        return outcome in (Outcome.POSTED, Outcome.DUPLICATE)

    # -- opening ----------------------------------------------------------------------------

    def open_fill(self, fill: Fill, accounts: Accounts, parties: Parties, p_units: int) -> OpenResult:
        """Reserve the buyer's cash for this fill and commit the fill. Idempotent on the fill id: the
        reserve call carries a stable key, so a retry after a crash gets the stored answer."""
        _need(fill.fill_id, FillId)
        _need(fill.exchange_id, ExchangeId)
        with self._tx() as db:
            if self._row(db, fill.fill_id.value):
                return OpenResult.ALREADY
        cash = fill.principal_minor + fill.fee_increment_minor
        key = p.idempotency_key(fill.exchange_id.value, fill.fill_id.value, p.Leg.RESERVE)
        answer = self._provider.reserve_cash(key, parties.buying_power_ref, cash, parties.currency)
        if isinstance(answer, p.Uncertain) or (isinstance(answer, p.Refused) and answer.code is p.RefusalCode.OUTAGE):
            if self._provider.lookup(key) is not p.ProviderStatus.RESERVED:
                return OpenResult.PENDING
            answer = self._provider.reserve_cash(key, parties.buying_power_ref, cash, parties.currency)
        if isinstance(answer, p.Refused):
            return OpenResult.REFUSED if answer.code is not p.RefusalCode.OUTAGE else OpenResult.PENDING
        if not isinstance(answer, p.CashReserved):
            return OpenResult.PENDING
        self._point("reserved")
        with self._tx() as db:
            db.execute("INSERT INTO fills (fill_id, exchange_id, state, quote_hash, p_units, principal, fee_increment,"
                       " reserve_account, seller_account, buyer_account, buying_power_ref, seller_ref, currency,"
                       " cash_reservation_ref, cash_amount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (fill.fill_id.value, fill.exchange_id.value, State.MATCHED.value, fill.quote_hash, p_units,
                        fill.principal_minor, fill.fee_increment_minor, accounts.reserve, accounts.seller,
                        accounts.buyer, parties.buying_power_ref, parties.seller_ref, parties.currency,
                        answer.reservation_ref, cash))
            db.execute("INSERT INTO history (fill_id, from_state, to_state) VALUES (?,NULL,?)",
                       (fill.fill_id.value, State.MATCHED.value))
        self._point("opened")
        return OpenResult.OPENED

    # -- the step functions -----------------------------------------------------------------

    def advance(self, fill_id: str) -> bool:
        """One local step, if there is one. True when something moved."""
        with self._tx() as db:
            row = self._row(db, fill_id)
            if row is None:
                return False
            state = State(row["state"])
            if state is State.MATCHED:
                self._enqueue(db, fill_id, p.Leg.CAPTURE)
                self._move(db, fill_id, State.CAPTURE_REQUESTED)
            elif state is State.P_TRANSFERRED:
                self._enqueue(db, fill_id, p.Leg.PAYOUT)
                self._move(db, fill_id, State.PAYOUT_REQUESTED)
            elif state is State.PAYOUT_RETURNED:
                self._enqueue(db, fill_id, p.Leg.PAYOUT)
                self._move(db, fill_id, State.PAYOUT_REQUESTED)
                db.execute("UPDATE legs SET status='pending' WHERE key=?", (self._key(row, p.Leg.PAYOUT),))
            elif state is State.CASH_CONFIRMED:
                pass
            else:
                return False
        if state is State.CASH_CONFIRMED:
            return self._transfer_p(fill_id)
        self._point(f"moved:{state.value}")
        return True

    def _transfer_p(self, fill_id: str) -> bool:
        with self._tx() as db:
            row = self._row(db, fill_id)
        moved = self._move_p(row, "p-transfer", row["reserve_account"], row["buyer_account"], "buyer")
        self._point("p-posted")
        with self._tx() as db:
            if State(self._row(db, fill_id)["state"]) is not State.CASH_CONFIRMED:
                return False
            if moved:
                db.execute("UPDATE fills SET p_at='buyer' WHERE fill_id=?", (fill_id,))
                self._move(db, fill_id, State.P_TRANSFERRED)
            else:
                self._move(db, fill_id, State.HELD)
        self._point("moved:p-transferred")
        return True

    def _confirm_cash(self, db, fill_id: str):
        """CAPTURE_REQUESTED -> CASH_CONFIRMED, and the only place the buyer's fee is charged."""
        row = self._row(db, fill_id)
        if State(row["state"]) is not State.CAPTURE_REQUESTED:
            return
        charged = fees.fee_increment(self._settled_principal(db, row), row["principal"])
        db.execute("UPDATE fills SET fee_charged=? WHERE fill_id=?", (charged, fill_id))
        self._move(db, fill_id, FEE_ACCRUES_AT)

    @staticmethod
    def _settled_principal(db, row) -> int:
        return db.execute(
            "SELECT COALESCE(SUM(f.principal), 0) FROM fills f WHERE f.exchange_id=? AND f.fill_id<>? AND EXISTS "
            "(SELECT 1 FROM history h WHERE h.fill_id=f.fill_id AND h.to_state=?)",
            (row["exchange_id"], row["fill_id"], FEE_ACCRUES_AT.value)).fetchone()[0]

    # -- the outbox -------------------------------------------------------------------------

    def _send(self, row, leg: str, key: str):
        provider = self._provider
        if leg == p.Leg.CAPTURE.value:
            return provider.capture(key, row["cash_reservation_ref"], row["principal"], row["fee_increment"])
        if leg == p.Leg.RELEASE.value:
            return provider.release(key, row["cash_reservation_ref"], row["cash_amount"])
        if leg == p.Leg.PAYOUT.value:
            return provider.payout(key, row["seller_ref"], row["principal"])
        if leg == p.Leg.REFUND.value:
            ref = row["capture_ref"]
            if ref is None:
                again = provider.capture(self._key(row, p.Leg.CAPTURE), row["cash_reservation_ref"],
                                         row["principal"], row["fee_increment"])
                if not isinstance(again, p.Captured):
                    return again
                ref = again.capture_ref
            return provider.refund(key, ref, row["principal"] + row["fee_increment"], "refund")
        raise ValueError(leg)

    def dispatch(self, fill_id: str) -> bool:
        """Send every pending outbox row once, in order. True when anything was sent."""
        with self._tx() as db:
            pending = [dict(r) for r in db.execute(
                "SELECT * FROM legs WHERE fill_id=? AND status='pending' ORDER BY rowid", (fill_id,))]
            row = self._row(db, fill_id)
        sent = False
        for leg in pending:
            self._point(f"before-dispatch:{leg['leg']}")
            answer = self._send(row, leg["leg"], leg["key"])
            self._point(f"dispatched:{leg['leg']}")
            with self._tx() as db:
                sent = self._record_answer(db, row, leg, answer) or sent
        return sent

    def _record_answer(self, db, row, leg, answer):
        """Store what the provider said. True when that changed anything (an outage changes nothing)."""
        fill_id, kind = leg["fill_id"], leg["leg"]
        if isinstance(answer, p.Uncertain):
            db.execute("UPDATE legs SET status='sent' WHERE key=?", (leg["key"],))
        elif isinstance(answer, p.Refused):
            if answer.code is p.RefusalCode.OUTAGE:
                return False
            db.execute("UPDATE legs SET status='refused' WHERE key=?", (leg["key"],))
            self._known_failure(db, fill_id, kind)
        else:
            db.execute("UPDATE legs SET status='done', answer=? WHERE key=?",
                       (json.dumps(getattr(answer, "__dict__", {}), sort_keys=True), leg["key"]))
            if isinstance(answer, p.Captured):
                db.execute("UPDATE fills SET capture_ref=? WHERE fill_id=?", (answer.capture_ref, fill_id))
        return True

    def _known_failure(self, db, fill_id: str, leg: str):
        state = State(self._row(db, fill_id)["state"])
        if leg == p.Leg.CAPTURE.value and state in (State.MATCHED, State.CAPTURE_REQUESTED):
            self._begin_release(db, fill_id)
        elif leg in (p.Leg.PAYOUT.value, p.Leg.REFUND.value) and State.HELD in TRANSITIONS[state]:
            self._move(db, fill_id, State.HELD)

    def _begin_release(self, db, fill_id: str):
        self._move(db, fill_id, State.RELEASED)
        self._enqueue(db, fill_id, p.Leg.RELEASE)

    # -- reconciliation and events ----------------------------------------------------------

    def reconcile(self, fill_id: str) -> None:
        """Ask the provider what really happened to every leg that is not settled. A leg the provider
        has never heard of goes back to pending under the SAME key; one it did do is finished."""
        with self._tx() as db:
            row = self._row(db, fill_id)
            legs = [dict(r) for r in db.execute("SELECT * FROM legs WHERE fill_id=? ORDER BY rowid", (fill_id,))]
        for leg in legs:
            if leg["status"] == "refused":
                continue
            if leg["status"] == "done" and not self._awaiting(row, leg):
                continue
            status = self._provider.lookup(leg["key"])
            self._point(f"looked-up:{leg['leg']}")
            with self._tx() as db:
                self._apply_status(db, fill_id, leg, status)

    @staticmethod
    def _awaiting(row, leg) -> bool:
        state = State(row["state"])
        return ((leg["leg"] == p.Leg.CAPTURE.value and state is State.CAPTURE_REQUESTED)
                or (leg["leg"] == p.Leg.PAYOUT.value and state is State.PAYOUT_REQUESTED))

    def _apply_status(self, db, fill_id: str, leg: dict, status: p.ProviderStatus):
        kind, key = leg["leg"], leg["key"]
        state = State(self._row(db, fill_id)["state"])
        if status is p.ProviderStatus.UNREACHABLE:
            return      # could not ask: nothing is known, so nothing changes
        if status is p.ProviderStatus.UNKNOWN:
            if leg["status"] == "sent":
                db.execute("UPDATE legs SET status='pending' WHERE key=?", (key,))
        elif kind == p.Leg.CAPTURE.value and status is p.ProviderStatus.CAPTURED:
            db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))
            self._confirm_cash(db, fill_id)
        elif kind == p.Leg.CAPTURE.value and status is p.ProviderStatus.REVERSED:
            if State.HELD in TRANSITIONS[state]:
                self._move(db, fill_id, State.HELD)
        elif kind == p.Leg.RELEASE.value and status is p.ProviderStatus.RELEASED:
            db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))
        elif kind == p.Leg.PAYOUT.value:
            if status is p.ProviderStatus.PAYOUT_PENDING:
                db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))
            elif leg["status"] == "pending":
                pass    # a re-requested payout is not judged by the provider's answer to the last attempt
            elif status is p.ProviderStatus.PAYOUT_CONFIRMED and state is State.PAYOUT_REQUESTED:
                db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))
                self._move(db, fill_id, State.PAYOUT_CONFIRMED)
            elif status is p.ProviderStatus.PAYOUT_RETURNED and state is State.PAYOUT_REQUESTED:
                db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))
                self._move(db, fill_id, State.PAYOUT_RETURNED)
        elif kind == p.Leg.REFUND.value and status is p.ProviderStatus.REFUNDED:
            db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))

    def pump(self, raw_events) -> dict:
        """Authenticate each provider event, drop duplicates, and let the rest nudge the matching leg.
        A forged or unknown event changes nothing and is recorded as refused."""
        counts = {"applied": 0, "duplicate": 0, "refused": 0}
        for raw in raw_events:
            event = self._provider.authenticate_event(raw)
            with self._tx() as db:
                if not isinstance(event, p.ProviderEvent):
                    db.execute("INSERT OR IGNORE INTO inbox VALUES (?,?,?)",
                               ("unauthenticated:" + hashlib.sha256(bytes(raw)).hexdigest(), None, "refused"))
                    counts["refused"] += 1
                    continue
                if db.execute("SELECT 1 FROM inbox WHERE message_id=?", (event.message_id,)).fetchone():
                    counts["duplicate"] += 1
                    continue
                leg = db.execute("SELECT * FROM legs WHERE key=?", (event.key,)).fetchone()
                outcome = "applied" if leg is not None else "refused"
                db.execute("INSERT INTO inbox VALUES (?,?,?)", (event.message_id, event.key, outcome))
                if leg is not None:
                    self._apply_status(db, leg["fill_id"], dict(leg), event.status)
                counts[outcome] += 1
        return counts

    # -- running ----------------------------------------------------------------------------

    def tick(self, fill_id: str, rounds: int = 12) -> State | None:
        """Reconcile, dispatch and advance until nothing more can happen locally."""
        for _ in range(rounds):
            self.reconcile(fill_id)
            progressed = self.dispatch(fill_id)
            progressed = self.advance(fill_id) or progressed
            progressed = self._finish_release(fill_id) or progressed
            progressed = self._finish_refund(fill_id) or progressed
            if not progressed:
                break
        return self.state(fill_id)

    def _finish_release(self, fill_id: str) -> bool:
        with self._tx() as db:
            row = self._row(db, fill_id)
        if row is None or State(row["state"]) is not State.RELEASED or row["p_at"] != "reserve":
            return False
        if not self._move_p(row, "p-release", row["reserve_account"], row["seller_account"], "seller"):
            return False
        with self._tx() as db:
            db.execute("UPDATE fills SET p_at='seller' WHERE fill_id=?", (fill_id,))
        self._point("p-released")
        return True

    def cancel(self, fill_id: str) -> CancelResult:
        """Known failure before capture (for example the order expired): release this fill's P and cash.
        Refused once cash may have been captured; an uncertain capture is looked up first, never released."""
        self.reconcile(fill_id)
        with self._tx() as db:
            row = self._row(db, fill_id)
            if row is None or State(row["state"]) not in (State.MATCHED, State.CAPTURE_REQUESTED):
                return CancelResult.NOT_ALLOWED
            leg = db.execute("SELECT status FROM legs WHERE key=?", (self._key(row, p.Leg.CAPTURE),)).fetchone()
            if leg is not None and leg["status"] in ("sent", "done"):
                return CancelResult.PENDING
            self._begin_release(db, fill_id)
        self.tick(fill_id)
        return CancelResult.CANCELLED

    def refund(self, fill_id: str, authorized: bool) -> State | None:
        """HELD -> REFUNDED: the cash goes back to the buyer and the P to the seller, once. The caller
        holds the authorization (a press); without it nothing happens."""
        if authorized is not True:
            return self.state(fill_id)
        with self._tx() as db:
            row = self._row(db, fill_id)
            held = row is not None and State(row["state"]) is State.HELD
            if held:
                self._enqueue(db, fill_id, p.Leg.REFUND)
        return self.tick(fill_id) if held else self.state(fill_id)

    def _finish_refund(self, fill_id: str) -> bool:
        with self._tx() as db:
            row = self._row(db, fill_id)
            if row is None or State(row["state"]) is not State.HELD:
                return False
            leg = db.execute("SELECT status FROM legs WHERE key=?", (self._key(row, p.Leg.REFUND),)).fetchone()
        if leg is None or leg["status"] != "done":
            return False
        source = {"reserve": row["reserve_account"], "buyer": row["buyer_account"]}.get(row["p_at"])
        if source is not None and row["p_at"] != "seller" and not self._move_p(
                row, "p-return", source, row["seller_account"], "seller"):
            return False
        with self._tx() as db:
            db.execute("UPDATE fills SET p_at='seller' WHERE fill_id=?", (fill_id,))
            self._move(db, fill_id, State.REFUNDED)
        self._point("refunded")
        return True

    # -- views ------------------------------------------------------------------------------

    def settled(self, fill_id: str) -> bool:
        """True when the fill is in a state that ends or holds, with no leg left to send or look up and
        its P back where the state says it belongs."""
        with self._tx() as db:
            row = self._row(db, fill_id)
            open_legs = db.execute("SELECT COUNT(*) FROM legs WHERE fill_id=? AND status IN ('pending','sent')",
                                   (fill_id,)).fetchone()[0]
        if row is None or open_legs:
            return False
        state = State(row["state"])
        if state in (State.RELEASED, State.REFUNDED):
            return row["p_at"] == "seller"
        return state in TERMINAL or state is State.HELD

    def liabilities(self) -> list[dict]:
        """Payouts in flight, returned or held: money the exchange still owes (S-4)."""
        owed = (State.PAYOUT_REQUESTED, State.PAYOUT_RETURNED, State.HELD, State.P_TRANSFERRED)
        with self._tx() as db:
            return [{"fill_id": r["fill_id"], "state": r["state"], "owed_minor": r["principal"]}
                    for r in db.execute("SELECT * FROM fills ORDER BY fill_id") if State(r["state"]) in owed]

    def fee_charged(self, fill_id: str) -> int:
        with self._tx() as db:
            return self._row(db, fill_id)["fee_charged"]

    def view(self, fill_id: str) -> dict:
        """The stages kept apart (S-7): earned, reserved for sale, matched, processor funds reserved,
        payout pending, payout confirmed."""
        with self._tx() as db:
            row = self._row(db, fill_id)
        state = State(row["state"])
        stage = {State.MATCHED: "matched", State.CAPTURE_REQUESTED: "processor funds reserved",
                 State.CASH_CONFIRMED: "earned", State.P_TRANSFERRED: "earned", State.PAYOUT_REQUESTED: "payout pending",
                 State.PAYOUT_RETURNED: "payout pending", State.PAYOUT_CONFIRMED: "payout confirmed",
                 State.RELEASED: "released", State.HELD: "held", State.REFUNDED: "refunded"}[state]
        return {"state": state.value, "stage": stage, "p_at": row["p_at"]}

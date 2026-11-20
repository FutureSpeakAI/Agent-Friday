"""The durable settlement workflow for one fill (NS-40.4-13, design D.5 as corrected by section 10). SYNTHETIC CASH AND
TEST CREDITS ONLY. No provider is named here: it works against the Provider protocol.

One row per fill in a SQLite file, moved one transaction per step. P moves through the issuer's Authority (never a bare
journal). Provider calls go out through an outbox row with a stable idempotency key; provider events come in through an
inbox that authenticates them first. Nothing is held in memory between calls, so a node that is killed after any step
reopens the file and carries on.

Opening a fill reserves both sides, P first (design 10.6): a local INTENT row, then the seller's P is posted from the
sale's reserve into the fill's own account (P_HELD), then the buyer's cash is reserved through the outbox (MATCHED).
No provider call is made before a local row exists, and no cash is touched for a fill whose P was refused.

States and the one table of allowed moves are below; anything not in the table is refused.

Invariants and the tests that hold them (tests/unit/test_exchange_settlement.py):
- S-1 a fill's P ends in exactly one place, buyer or seller's reserve:
  test_every_crash_point_under_every_fault_converges_and_keeps_the_invariants and
  test_the_crash_matrix_with_two_fills_on_one_order_converges.
- S-2 a fill's cash reservation ends captured or released, never both or neither: the same tests.
- S-3 the P transfer and the fee each happen at most once per fill: the same tests, and
  test_the_fee_is_charged_at_one_place_only.
- S-4 a payout in flight, returned or held is shown as an outstanding liability: test_outstanding_liabilities_are_shown.
- S-5 a refund is its own record and never edits the fill: test_a_refund_is_its_own_leg_and_returns_p_once.
- S-8 cash is never a balance in the P journal: test_cash_never_appears_in_the_p_journal.
- S-9 no cash is captured for a fill whose P is not in its own fill account:
  test_no_capture_is_sent_for_a_fill_whose_p_is_not_in_its_own_account.
- S-10 a refund recovers the P first and sends the cash only if that posted:
  test_a_refund_returns_the_p_first_and_sends_no_cash_while_it_is_unrecovered.
- S-11 a reversed capture is never refunded: test_a_reversed_capture_needs_only_the_p_back_and_no_refund_is_sent.
- S-12' (design 11.1, replacing S-12) a fill's fee must be its planned increment, and the plan intervals of confirmed
  fills must not overlap; nothing else holds a fill for its fee:
  test_every_plan_of_up_to_three_fills_confirmed_in_every_order_completes_with_the_one_order_fee,
  test_two_planned_fills_confirm_in_either_order_and_the_fee_total_is_the_one_order_fee,
  test_a_fee_that_is_not_the_planned_increment_holds_the_fill,
  test_two_fills_planned_from_one_starting_point_the_second_to_confirm_is_held.
- S-13 the plan check ignores fills that were refunded or whose capture was reversed:
  test_a_refunded_fills_principal_no_longer_counts_toward_the_fee_check and the reversed twin.
- S-18 the P goes back to the seller's reserve only when the seller was not paid; otherwise it is held in
  recovered:<exchange>:<fill>, the fill stays HELD and listed (M7 is Stephen's):
  test_a_reversal_after_the_payout_recovers_the_p_into_a_held_account_not_the_sellers_reserve.
- S-19 a held fill's queued payout is cancelled and none is sent for a held or reversed fill:
  test_a_payout_queued_while_the_provider_was_down_is_never_sent_for_a_held_fill.
- S-20 the sale reserve is derived from the seller, never passed: test_a_fill_cannot_be_opened_against_any_reserve_but_its_sellers.
- S-14 a payout returned after it was confirmed holds the fill and lists it:
  test_a_payout_returned_after_confirmation_holds_the_fill_and_lists_it.
- S-15 an inbox event is `applied` only if it changed something, else `ignored` with a reason:
  test_an_event_that_changes_nothing_is_recorded_as_ignored_with_its_reason.
- S-16 settled() is true for a terminal state, or a HELD fill with no refund pressed:
  test_settled_is_false_for_a_held_fill_whose_refund_is_pressed_but_not_complete.
- S-17 a refund needs a typed press: test_a_refund_needs_a_typed_press.
- The opening order and its crash cases: test_every_opening_step_leaves_a_local_row_before_any_provider_call,
  test_a_second_fill_against_one_reservation_is_refused_at_open_and_touches_no_cash.
- Uncertain is never failure or success: test_an_uncertain_capture_is_looked_up_never_released.
- A forged or unauthenticated event changes nothing and is recorded as refused:
  test_a_forged_event_changes_nothing_and_is_recorded.
- A copied signed receipt replayed on another node pays nothing: test_a_replayed_fill_on_another_node_pays_nothing.
- The owner's kill switch stops opening and lets committed settlement finish (NS-40.1-18): tests/unit/test_exchange_kill_switch.py.
- Settlement is handed an Authority, never a bare journal: test_a_bare_journal_is_not_accepted.
- Fee accrual has one call site: test_the_fee_is_charged_at_one_place_only.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from agent_friday.services.exchange import fees
from agent_friday.services.exchange import provider as p
from agent_friday.services.exchange.orders import ExchangeId, Fill, FillId, _need
from agent_friday.services.exchange.reservation import sale_account
from agent_friday.services.issuer_journal import IssuerJournal, Outcome


class State(str, Enum):
    INTENT = "intent"
    P_HELD = "p-held"
    MATCHED = "matched"
    CAPTURE_REQUESTED = "capture-requested"
    CASH_CONFIRMED = "cash-confirmed"
    P_TRANSFERRED = "p-transferred"
    PAYOUT_REQUESTED = "payout-requested"
    PAYOUT_CONFIRMED = "payout-confirmed"
    PAYOUT_RETURNED = "payout-returned"
    RELEASED = "released"
    REFUSED = "refused"
    HELD = "held"
    REFUNDED = "refunded"


S = State
TRANSITIONS = {
    S.INTENT: {S.P_HELD, S.REFUSED},
    S.P_HELD: {S.MATCHED, S.REFUSED},
    S.MATCHED: {S.CAPTURE_REQUESTED, S.RELEASED},
    S.CAPTURE_REQUESTED: {S.CASH_CONFIRMED, S.RELEASED, S.HELD},
    S.CASH_CONFIRMED: {S.P_TRANSFERRED, S.HELD},
    S.P_TRANSFERRED: {S.PAYOUT_REQUESTED, S.HELD},
    S.PAYOUT_REQUESTED: {S.PAYOUT_CONFIRMED, S.PAYOUT_RETURNED, S.HELD},
    S.PAYOUT_RETURNED: {S.PAYOUT_REQUESTED, S.HELD},
    S.PAYOUT_CONFIRMED: {S.HELD},
    S.HELD: {S.REFUNDED},
    S.RELEASED: {S.HELD},
    S.REFUSED: set(),
    S.REFUNDED: set(),
}
TERMINAL = frozenset({S.PAYOUT_CONFIRMED, S.RELEASED, S.REFUSED, S.REFUNDED})
FEE_ACCRUES_AT = S.CASH_CONFIRMED
OPENING = (S.INTENT, S.P_HELD)

# where a fill's P is (the `p_at` column): in the sale's reserve, in the fill's own account, or with the buyer
AT_RESERVE, AT_FILL, AT_BUYER, AT_RECOVERED = "reserve", "fill", "buyer", "recovered"


class IllegalTransition(RuntimeError):
    pass


class OpenResult(str, Enum):
    OPENED = "opened"
    ALREADY = "already"
    REFUSED = "refused"
    PENDING = "pending"
    HALTED = "halted"          # the owner's kill switch is on: no new fill is opened


class CancelResult(str, Enum):
    CANCELLED = "cancelled"
    NOT_ALLOWED = "not-allowed"
    PENDING = "pending"


@dataclass(frozen=True)
class RefundPress:
    """Proof that a refund was a press on this PC's own screen, as accepting an issuer is. A boolean is not one."""
    _made_here: bool = True

    @classmethod
    def from_request(cls, request) -> "RefundPress | None":
        from agent_friday.services import screen_click
        return cls() if screen_click.screen_session(request) else None


@dataclass(frozen=True)
class Accounts:
    """Who the fill's P moves between. Every journal account but the buyer's is derived: the sale's reserve from the
    seller with the function a SaleReservation uses (S-20), the fill's own account and the recovered account from the
    exchange and fill ids."""
    seller: str
    buyer: str


@dataclass(frozen=True)
class Parties:
    buying_power_ref: str
    seller_ref: str
    currency: str


def fill_account(exchange_id: str, fill_id: str) -> str:
    return f"fill:{exchange_id}:{fill_id}"


def recovered_account(exchange_id: str, fill_id: str) -> str:
    return f"recovered:{exchange_id}:{fill_id}"


def _effect(exchange_id: str, fill_id: str, what: str) -> str:
    return f"fill:{exchange_id}:{fill_id}:{what}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY, exchange_id TEXT NOT NULL, state TEXT NOT NULL, quote_hash TEXT NOT NULL,
    p_units INTEGER NOT NULL, principal INTEGER NOT NULL, fee_increment INTEGER NOT NULL,
    fee_charged INTEGER NOT NULL DEFAULT 0, reserve_account TEXT, buyer_account TEXT,
    buying_power_ref TEXT, seller_ref TEXT, currency TEXT, cash_reservation_ref TEXT, capture_ref TEXT,
    p_at TEXT NOT NULL DEFAULT 'reserve', cash_amount INTEGER NOT NULL,
    capture_reversed INTEGER NOT NULL DEFAULT 0, refund_pressed INTEGER NOT NULL DEFAULT 0,
    planned_before INTEGER NOT NULL DEFAULT 0, payout_returned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS legs (
    key TEXT PRIMARY KEY, fill_id TEXT NOT NULL, leg TEXT NOT NULL, status TEXT NOT NULL, answer TEXT);
CREATE TABLE IF NOT EXISTS inbox (
    message_id TEXT PRIMARY KEY, key TEXT, outcome TEXT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS history (
    n INTEGER PRIMARY KEY AUTOINCREMENT, fill_id TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL);
"""

_REFUSING = frozenset({Outcome.INSUFFICIENT, Outcome.UNITS, Outcome.ISSUER, Outcome.UNBALANCED, Outcome.ACTION_CONFLICT,
                       Outcome.EFFECT_CONFLICT, Outcome.CORRECTION_INVALID})


class Settlement:
    """The workflow over one store, one issuer authority and one provider."""

    def __init__(self, path, authority, provider, *, synchronous: str = "FULL",
                 new_effects_allowed: Callable[[], bool] | None = None):
        if synchronous not in ("FULL", "NORMAL", "OFF"):
            raise ValueError("synchronous is FULL, NORMAL or OFF")
        if isinstance(authority, IssuerJournal) or not hasattr(authority, "execute") or not hasattr(authority, "issuer"):
            raise TypeError("settlement posts P through the issuer's Authority, not a bare journal")
        self._synchronous = synchronous
        self._new_effects_allowed = new_effects_allowed or (lambda: True)
        self._path = str(path)
        self._authority = authority
        self._provider = provider
        self._point = lambda name: None
        self._opened_here: set = set()
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
        if to is State.HELD:        # S-19: a held fill sends no payout, so one still queued is cancelled
            db.execute("UPDATE legs SET status='cancelled' WHERE fill_id=? AND leg=? AND status='pending'",
                       (fill_id, p.Leg.PAYOUT.value))

    def _enqueue(self, db, fill_id: str, leg: p.Leg, scope: str = "once"):
        row = self._row(db, fill_id)
        key = p.idempotency_key(row["exchange_id"], fill_id, leg, scope)
        db.execute("INSERT OR IGNORE INTO legs VALUES (?,?,?,?,NULL)", (key, fill_id, leg.value, "pending"))
        return key

    def _key(self, row, leg: p.Leg, scope="once") -> str:
        return p.idempotency_key(row["exchange_id"], row["fill_id"], leg, scope)

    def _post(self, command: dict) -> Outcome:
        outcome = self._authority.execute(command)
        return outcome[0] if isinstance(outcome, tuple) else outcome

    def _move_p(self, row, what: str, source: str, target: str) -> Outcome:
        """One journal posting of this fill's P, once, through the authority."""
        effect = _effect(row["exchange_id"], row["fill_id"], what)
        return self._post({"issuer": self._authority.issuer, "effect": effect, "action": effect,
                           "postings": {source: -row["p_units"], target: row["p_units"]}})

    @staticmethod
    def _landed(outcome: Outcome) -> bool:
        return outcome in (Outcome.POSTED, Outcome.DUPLICATE)

    # -- opening (design 10.6) --------------------------------------------------------------

    def open_fill(self, fill: Fill, accounts: Accounts, parties: Parties) -> OpenResult:
        """Store the fill locally, hold the seller's P in the fill's own account, then reserve the buyer's cash.
        Idempotent on the fill id and safe to call again after a crash or a PENDING answer: it carries on from the
        state the fill is in. The fill's P is derived from its lots and lot size."""
        _need(fill.fill_id, FillId)
        _need(fill.exchange_id, ExchangeId)
        if type(fill.lots) is not int or type(fill.lot_units) is not int or fill.lots < 1 or fill.lot_units < 1:
            raise ValueError("a fill has whole lots of a whole lot size")
        if type(fill.planned_before) is not int or fill.planned_before < 0:
            raise ValueError("a fill's place in the plan is a whole non-negative principal")
        units = fill.lots * fill.lot_units
        fill_id = fill.fill_id.value
        self._opened_here.discard(fill_id)
        with self._tx() as db:
            row = self._row(db, fill_id)
        if (row is None or State(row["state"]) in OPENING) and not self._allowed_now():
            return OpenResult.HALTED
        with self._tx() as db:
            if self._row(db, fill_id) is None:
                db.execute("INSERT INTO fills (fill_id, exchange_id, state, quote_hash, p_units, principal, fee_increment,"
                           " reserve_account, buyer_account, buying_power_ref, seller_ref, currency, cash_amount, planned_before)"
                           " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (fill_id, fill.exchange_id.value, State.INTENT.value, fill.quote_hash, units,
                            fill.principal_minor, fill.fee_increment_minor,
                            sale_account(fill.exchange_id, accounts.seller), accounts.buyer,
                            parties.buying_power_ref, parties.seller_ref, parties.currency,
                            fill.principal_minor + fill.fee_increment_minor, fill.planned_before))
                db.execute("INSERT INTO history (fill_id, from_state, to_state) VALUES (?,NULL,?)",
                           (fill_id, State.INTENT.value))
        self._point("intent")
        for _ in range(12):
            if not self._opening(fill_id):
                break
        if self.state(fill_id) is State.REFUSED:
            self._finish_release(fill_id)        # a refused cash reservation: the P goes back to the sale's reserve
        state = self.state(fill_id)
        mine = fill_id in self._opened_here       # this call is the one that moved the fill to MATCHED
        self._opened_here.discard(fill_id)
        if state is State.REFUSED:
            return OpenResult.REFUSED
        if state in OPENING:
            return OpenResult.PENDING
        return OpenResult.OPENED if mine else OpenResult.ALREADY

    def _opening(self, fill_id: str) -> bool:
        """One opening step, if the fill is still opening. True when something moved."""
        with self._tx() as db:
            row = self._row(db, fill_id)
        if row is None or State(row["state"]) not in OPENING or not self._allowed_now():
            return False
        if State(row["state"]) is State.INTENT:
            return self._hold_p(row)
        with self._tx() as db:
            self._enqueue(db, fill_id, p.Leg.RESERVE)
        sent = self.dispatch(fill_id)
        self.reconcile(fill_id)
        return sent or self.state(fill_id) not in OPENING

    def _hold_p(self, row) -> bool:
        fill_id = row["fill_id"]
        outcome = self._move_p(row, "p-hold", row["reserve_account"], fill_account(row["exchange_id"], fill_id))
        self._point("p-hold-posted")
        if outcome is Outcome.HALTED:
            return False                       # nothing is known: nothing changes, and no cash was touched
        with self._tx() as db:
            if State(self._row(db, fill_id)["state"]) is not State.INTENT:
                return True
            if self._landed(outcome):
                db.execute("UPDATE fills SET p_at=? WHERE fill_id=?", (AT_FILL, fill_id))
                self._move(db, fill_id, State.P_HELD)
            else:
                self._move(db, fill_id, State.REFUSED)    # the journal refused: no cash is touched
        self._point("p-held")
        return True

    def _allowed_now(self) -> bool:
        """The kill switch stops NEW side effects only: opening a fill, which holds P and reserves cash. Everything
        already committed carries on (confirming, paying out, releasing, refunding) and the history is kept
        (NS-40.1-18). A gate that cannot answer counts as halted. Stated limit: a RESERVE leg the provider already answered
        (reconcile saw it reserved) goes back to pending and is not re-asked while halted, so that fill waits in P_HELD
        until the switch is off (asking again would be the same effect under the same key, but the gate is conservative)."""
        try:
            return self._new_effects_allowed() is True
        except Exception:  # noqa: BLE001
            return False

    # -- the step functions -----------------------------------------------------------------

    def advance(self, fill_id: str) -> bool:
        """One local step, if there is one. True when something moved."""
        with self._tx() as db:
            row = self._row(db, fill_id)
            if row is None:
                return False
            state = State(row["state"])
            if state in OPENING:
                pass
            elif state is State.MATCHED:
                self._enqueue(db, fill_id, p.Leg.CAPTURE)
                self._move(db, fill_id, State.CAPTURE_REQUESTED)
            elif state is State.P_TRANSFERRED:
                self._enqueue(db, fill_id, p.Leg.PAYOUT)
                self._move(db, fill_id, State.PAYOUT_REQUESTED)
            elif state is State.PAYOUT_RETURNED:
                self._enqueue(db, fill_id, p.Leg.PAYOUT)
                self._move(db, fill_id, State.PAYOUT_REQUESTED)
                db.execute("UPDATE legs SET status='pending' WHERE key=?", (self._key(row, p.Leg.PAYOUT),))
                db.execute("UPDATE fills SET payout_returned=0 WHERE fill_id=?", (fill_id,))
            elif state is State.CASH_CONFIRMED:
                pass
            else:
                return False
        if state in OPENING:
            return self._opening(fill_id)
        if state is State.CASH_CONFIRMED:
            return self._transfer_p(fill_id)
        self._point(f"moved:{state.value}")
        return True

    def _transfer_p(self, fill_id: str) -> bool:
        with self._tx() as db:
            row = self._row(db, fill_id)
        outcome = self._move_p(row, "p-transfer", fill_account(row["exchange_id"], fill_id), row["buyer_account"])
        self._point("p-posted")
        moved = self._landed(outcome)
        with self._tx() as db:
            if moved:
                db.execute("UPDATE fills SET p_at=? WHERE fill_id=?", (AT_BUYER, fill_id))   # where the journal put it
            if State(self._row(db, fill_id)["state"]) is not State.CASH_CONFIRMED:
                return False
            self._move(db, fill_id, State.P_TRANSFERRED if moved else State.HELD)
        self._point("moved:p-transferred")
        return True

    def _confirm_cash(self, db, fill_id: str):
        """CAPTURE_REQUESTED -> CASH_CONFIRMED, and the only place the buyer's fee is charged (S-12', S-13).

        The fee is the increment planned at match time and must equal the increment for the fill's place in the plan
        (`planned_before`). The plan intervals [planned_before, planned_before + principal) of the order's confirmed
        fills must not overlap. A fill that breaks either is held; nothing else holds a fill for its fee. Fills that were
        refunded or whose capture was reversed no longer count."""
        row = self._row(db, fill_id)
        if State(row["state"]) is not State.CAPTURE_REQUESTED:
            return
        others = db.execute(
            "SELECT f.planned_before, f.principal FROM fills f WHERE f.exchange_id=? AND f.fill_id<>? AND f.state<>? "
            "AND f.capture_reversed=0 AND EXISTS (SELECT 1 FROM history h WHERE h.fill_id=f.fill_id AND h.to_state=?)",
            (row["exchange_id"], fill_id, State.REFUNDED.value, FEE_ACCRUES_AT.value)).fetchall()
        intervals = [(r[0], r[1]) for r in others] + [(row["planned_before"], row["principal"])]
        if (row["fee_increment"] != fees.fee_increment(row["planned_before"], row["principal"])
                or fees.plan_overlaps(intervals)):
            self._move(db, fill_id, State.HELD)    # the fee captured is not the planned one, or it was planned twice
            return
        db.execute("UPDATE fills SET fee_charged=? WHERE fill_id=?", (row["fee_increment"], fill_id))
        self._move(db, fill_id, FEE_ACCRUES_AT)

    # -- the outbox -------------------------------------------------------------------------

    def _capture_ref(self, row):
        """The capture's reference: stored, or asked for again under the capture's own key (an idempotent call)."""
        if row["capture_ref"] is not None:
            return row["capture_ref"]
        again = self._provider.capture(self._key(row, p.Leg.CAPTURE), row["cash_reservation_ref"],
                                       row["principal"], row["fee_increment"])
        return again.capture_ref if isinstance(again, p.Captured) else again

    def _send(self, row, leg: str, key: str):
        provider = self._provider
        if leg == p.Leg.RESERVE.value:
            return provider.reserve_cash(key, row["buying_power_ref"], row["cash_amount"], row["currency"])
        if leg == p.Leg.CAPTURE.value:
            if row["p_at"] != AT_FILL:
                return p.Refused(p.RefusalCode.INVALID)      # S-9: no cash for a fill whose P is not held in its own account
            return provider.capture(key, row["cash_reservation_ref"], row["principal"], row["fee_increment"])
        if leg == p.Leg.RELEASE.value:
            return provider.release(key, row["cash_reservation_ref"], row["cash_amount"])
        if leg == p.Leg.PAYOUT.value:
            ref = self._capture_ref(row)
            if not isinstance(ref, str):
                return ref
            return provider.payout(key, ref, row["seller_ref"], row["principal"])
        if leg == p.Leg.REFUND.value:
            ref = self._capture_ref(row)
            if not isinstance(ref, str):
                return ref
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
            with self._tx() as db:
                row = self._row(db, fill_id)
                if leg["leg"] == p.Leg.PAYOUT.value and (State(row["state"]) is State.HELD or row["capture_reversed"]):
                    db.execute("UPDATE legs SET status='cancelled' WHERE key=?", (leg["key"],))    # S-19
                    continue
            if leg["leg"] == p.Leg.RESERVE.value and not self._allowed_now():
                continue       # reserving cash is a new effect: a leg left pending by a crash waits while halted
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
            elif isinstance(answer, p.CashReserved):
                self._cash_reserved(db, fill_id, answer.reservation_ref)
        return True

    def _cash_reserved(self, db, fill_id: str, reservation_ref: str):
        """P_HELD -> MATCHED, and only now: the cash reservation's reference is stored with the move."""
        if State(self._row(db, fill_id)["state"]) is not State.P_HELD:
            return
        db.execute("UPDATE fills SET cash_reservation_ref=? WHERE fill_id=?", (reservation_ref, fill_id))
        self._move(db, fill_id, State.MATCHED)
        self._opened_here.add(fill_id)

    def _known_failure(self, db, fill_id: str, leg: str):
        state = State(self._row(db, fill_id)["state"])
        if leg == p.Leg.RESERVE.value and state is State.P_HELD:
            self._move(db, fill_id, State.REFUSED)       # the buyer's cash was refused: the P goes back, below
        elif leg == p.Leg.CAPTURE.value and state in (State.MATCHED, State.CAPTURE_REQUESTED):
            self._begin_release(db, fill_id)
        elif leg in (p.Leg.PAYOUT.value, p.Leg.REFUND.value) and State.HELD in TRANSITIONS[state]:
            self._move(db, fill_id, State.HELD)

    def _begin_release(self, db, fill_id: str):
        self._move(db, fill_id, State.RELEASED)
        self._enqueue(db, fill_id, p.Leg.RELEASE)
        db.execute("UPDATE legs SET status='cancelled' WHERE fill_id=? AND leg=? AND status='pending'",
                   (fill_id, p.Leg.CAPTURE.value))

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
            if leg["status"] in ("done", "cancelled") and not self._awaiting(row, leg):
                continue
            status = self._provider.lookup(leg["key"])
            self._point(f"looked-up:{leg['leg']}")
            with self._tx() as db:
                self._apply_status(db, fill_id, leg, status)

    @staticmethod
    def _awaiting(row, leg) -> bool:
        state = State(row["state"])
        return ((leg["leg"] == p.Leg.CAPTURE.value and state in (State.CAPTURE_REQUESTED, State.RELEASED))
                or (leg["leg"] == p.Leg.PAYOUT.value and state is State.PAYOUT_REQUESTED))

    def _apply_status(self, db, fill_id: str, leg: dict, status: p.ProviderStatus):
        kind, key = leg["leg"], leg["key"]
        state = State(self._row(db, fill_id)["state"])
        if status is p.ProviderStatus.UNREACHABLE:
            return      # could not ask: nothing is known, so nothing changes
        if status is p.ProviderStatus.UNKNOWN:
            if leg["status"] == "sent":
                db.execute("UPDATE legs SET status='pending' WHERE key=?", (key,))
        elif kind == p.Leg.RESERVE.value:
            if status is p.ProviderStatus.RESERVED and leg["status"] == "sent":
                db.execute("UPDATE legs SET status='pending' WHERE key=?", (key,))   # asking again returns the stored answer
        elif kind == p.Leg.CAPTURE.value and status is p.ProviderStatus.CAPTURED:
            db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))
            if state is State.RELEASED:
                self._move(db, fill_id, State.HELD)    # the cash was captured after all: it needs a refund
            else:
                self._confirm_cash(db, fill_id)
        elif kind == p.Leg.CAPTURE.value and status is p.ProviderStatus.REVERSED:
            db.execute("UPDATE fills SET capture_reversed=1 WHERE fill_id=?", (fill_id,))
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
            elif status is p.ProviderStatus.PAYOUT_RETURNED:
                db.execute("UPDATE fills SET payout_returned=1 WHERE fill_id=?", (fill_id,))
                if state is State.PAYOUT_REQUESTED:
                    db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))
                    self._move(db, fill_id, State.PAYOUT_RETURNED)
                elif state is State.PAYOUT_CONFIRMED:
                    self._move(db, fill_id, State.HELD)    # S-14: returned after it was confirmed: the seller was not paid
        elif kind == p.Leg.REFUND.value and status is p.ProviderStatus.REFUNDED:
            db.execute("UPDATE legs SET status='done' WHERE key=?", (key,))

    @staticmethod
    def _snapshot(db, fill_id: str):
        return (tuple(db.execute("SELECT * FROM fills WHERE fill_id=?", (fill_id,)).fetchone()),
                tuple(tuple(r) for r in db.execute("SELECT key, status FROM legs WHERE fill_id=? ORDER BY key", (fill_id,))))

    def pump(self, raw_events) -> dict:
        """Authenticate each provider event, drop duplicates, and let the rest nudge the matching leg. A forged or
        unknown event changes nothing and is recorded as refused; an event that changes nothing is `ignored` with its
        reason, and only one that changed something is `applied` (S-15)."""
        counts = {"applied": 0, "ignored": 0, "duplicate": 0, "refused": 0}
        for raw in raw_events:
            event = self._provider.authenticate_event(raw)
            with self._tx() as db:
                if not isinstance(event, p.ProviderEvent):
                    db.execute("INSERT OR IGNORE INTO inbox VALUES (?,?,?,?)",
                               ("unauthenticated:" + hashlib.sha256(bytes(raw)).hexdigest(), None, "refused",
                                "not authentic"))
                    counts["refused"] += 1
                    continue
                if db.execute("SELECT 1 FROM inbox WHERE message_id=?", (event.message_id,)).fetchone():
                    counts["duplicate"] += 1
                    continue
                leg = db.execute("SELECT * FROM legs WHERE key=?", (event.key,)).fetchone()
                if leg is None:
                    outcome, reason = "refused", "no leg has this key"
                else:
                    before = self._snapshot(db, leg["fill_id"])
                    self._apply_status(db, leg["fill_id"], dict(leg), event.status)
                    changed = self._snapshot(db, leg["fill_id"]) != before
                    outcome = "applied" if changed else "ignored"
                    reason = None if changed else f"{event.status.value} changes nothing for the {leg['leg']} leg"
                db.execute("INSERT INTO inbox VALUES (?,?,?,?)", (event.message_id, event.key, outcome, reason))
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
            progressed = self._progress_refund(fill_id) or progressed
            if not progressed:
                break
        return self.state(fill_id)

    def _finish_release(self, fill_id: str) -> bool:
        """After a release or a refusal, the P goes from the fill's account back to the seller's sale reserve."""
        with self._tx() as db:
            row = self._row(db, fill_id)
        if row is None or State(row["state"]) not in (State.RELEASED, State.REFUSED) or row["p_at"] != AT_FILL:
            return False
        outcome = self._move_p(row, "p-release", fill_account(row["exchange_id"], fill_id), row["reserve_account"])
        if not self._landed(outcome):
            return False
        with self._tx() as db:
            db.execute("UPDATE fills SET p_at=? WHERE fill_id=?", (AT_RESERVE, fill_id))
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

    # -- refund (S-10, S-11, S-17) ----------------------------------------------------------

    def refund(self, fill_id: str, press) -> State | None:
        """Press a refund on a HELD fill. Only a RefundPress counts (S-17). The P is recovered first and the cash
        refund is sent only once it is (S-10); a capture the provider reversed needs no cash refund (S-11)."""
        if not isinstance(press, RefundPress):
            return self.state(fill_id)
        with self._tx() as db:
            row = self._row(db, fill_id)
            held = row is not None and State(row["state"]) is State.HELD
            if held:
                db.execute("UPDATE fills SET refund_pressed=1 WHERE fill_id=?", (fill_id,))
                db.execute("UPDATE legs SET status='pending' WHERE fill_id=? AND leg=? AND status='refused'",
                           (fill_id, p.Leg.REFUND.value))
        return self.tick(fill_id) if held else self.state(fill_id)

    def _payout_stands(self, row) -> bool:
        """True when the seller has been, or may have been, paid for this fill and the payout was not returned (S-18)."""
        with self._tx() as db:
            leg = db.execute("SELECT status FROM legs WHERE key=?", (self._key(row, p.Leg.PAYOUT),)).fetchone()
        return leg is not None and leg["status"] in ("done", "sent") and not row["payout_returned"]

    def _progress_refund(self, fill_id: str) -> bool:
        with self._tx() as db:
            row = self._row(db, fill_id)
        if row is None or State(row["state"]) is not State.HELD or not row["refund_pressed"]:
            return False
        stands = self._payout_stands(row)
        if row["p_at"] == AT_RECOVERED:
            if stands:
                return False        # M7 is Stephen's: until it is answered the fill stays held and listed
            outcome = self._move_p(row, "p-restore", recovered_account(row["exchange_id"], fill_id), row["reserve_account"])
            if not self._landed(outcome):
                return False
            with self._tx() as db:
                db.execute("UPDATE fills SET p_at=? WHERE fill_id=?", (AT_RESERVE, fill_id))
            return True
        if row["p_at"] != AT_RESERVE:
            source = {AT_BUYER: row["buyer_account"], AT_FILL: fill_account(row["exchange_id"], fill_id)}[row["p_at"]]
            # S-18: to the seller's reserve only if the seller was not paid; otherwise held where it can be asked for
            target, where, what = ((recovered_account(row["exchange_id"], fill_id), AT_RECOVERED, "p-recover") if stands
                                   else (row["reserve_account"], AT_RESERVE, "p-return"))
            outcome = self._move_p(row, what, source, target)
            if not self._landed(outcome):
                return False        # the P is not recoverable yet: no cash moves, and the fill stays held and listed
            with self._tx() as db:
                db.execute("UPDATE fills SET p_at=? WHERE fill_id=?", (where, fill_id))
            self._point("p-returned")
            return True
        if stands:
            return False            # the seller's payout stands: no cash is refunded and the P is not given back to them
        if row["capture_reversed"]:
            with self._tx() as db:
                if State(self._row(db, fill_id)["state"]) is State.HELD:
                    self._move(db, fill_id, State.REFUNDED)
            self._point("refunded")
            return True
        with self._tx() as db:
            leg = db.execute("SELECT status FROM legs WHERE key=?", (self._key(row, p.Leg.REFUND),)).fetchone()
            if leg is None:
                self._enqueue(db, fill_id, p.Leg.REFUND)
                return True                 # queued: the next round sends it
            if leg["status"] != "done":
                return False
            self._move(db, fill_id, State.REFUNDED)
        self._point("refunded")
        return True

    # -- views ------------------------------------------------------------------------------

    def settled(self, fill_id: str) -> bool:
        """True when nothing is left to send or look up and the state is final (S-16): a terminal state with its P where
        it belongs, or a HELD fill that nobody has pressed a refund for. A HELD fill whose refund is pressed and not
        complete is not settled."""
        with self._tx() as db:
            row = self._row(db, fill_id)
            open_legs = db.execute("SELECT COUNT(*) FROM legs WHERE fill_id=? AND status IN ('pending','sent')",
                                   (fill_id,)).fetchone()[0]
        if row is None or open_legs:
            return False
        state = State(row["state"])
        if state in (State.RELEASED, State.REFUNDED, State.REFUSED):
            return row["p_at"] == AT_RESERVE
        if state is State.HELD:
            return not row["refund_pressed"]
        return state in TERMINAL

    def liabilities(self) -> list[dict]:
        """Cash captured, or a payout in flight, returned or held: money the exchange still owes (S-4)."""
        owed = (State.CASH_CONFIRMED, State.PAYOUT_REQUESTED, State.PAYOUT_RETURNED, State.HELD, State.P_TRANSFERRED)
        with self._tx() as db:
            return [{"fill_id": r["fill_id"], "state": r["state"], "owed_minor": r["principal"]}
                    for r in db.execute("SELECT * FROM fills ORDER BY fill_id") if State(r["state"]) in owed]

    def fee_charged(self, fill_id: str) -> int:
        with self._tx() as db:
            return self._row(db, fill_id)["fee_charged"]

    def view(self, fill_id: str) -> dict:
        """The stages kept apart (S-7), in the spec's words (design 10.7): matched, processor funds reserved, payout
        pending, payout confirmed. "Earned" belongs to the work order and is never shown for a sale."""
        with self._tx() as db:
            row = self._row(db, fill_id)
        state = State(row["state"])
        stage = {State.INTENT: "matched", State.P_HELD: "matched", State.MATCHED: "matched",
                 State.CAPTURE_REQUESTED: "processor funds reserved",
                 State.CASH_CONFIRMED: "payout pending", State.P_TRANSFERRED: "payout pending",
                 State.PAYOUT_REQUESTED: "payout pending", State.PAYOUT_RETURNED: "payout pending",
                 State.PAYOUT_CONFIRMED: "payout confirmed", State.RELEASED: "released", State.REFUSED: "refused",
                 State.HELD: "held", State.REFUNDED: "refunded"}[state]
        return {"state": state.value, "stage": stage, "p_at": row["p_at"]}


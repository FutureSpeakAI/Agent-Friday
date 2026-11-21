"""A sale reservation: the seller's P held in the sale's own account, proved by a signed receipt (design 10.5, D-1').

TEST CREDITS ONLY. A sell order is accepted only with one of these. It is built where the journal posts the
reservation (`reserve_for_sale`), so it carries what the journal actually did: the seller's account, the sale's account,
the units, and the receipt with its head. `verify_reservation` checks that the receipt verifies under its head, that it
is the receipt of THIS posting (the request hash is recomputed from the stated postings), and that the account is the
sale's own account for this seller.

Rules and the tests that hold them (tests/unit/test_exchange_orders.py):
- A reservation is the seller's, covers its units, and verifies under its head:
  test_a_sell_order_is_accepted_only_with_a_verified_reservation_that_covers_it,
  test_a_sell_order_on_an_unrelated_receipt_or_a_forged_one_is_refused.
- Reserving moves the units into the sale's own account once:
  test_reserving_for_a_sale_moves_the_units_into_the_sales_own_account_once.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent_friday.services import issuer_authority as auth
from agent_friday.services import issuer_journal as ij
from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.exchange.provider import opaque
from agent_friday.services.issuer_registry import IssuerRef


class ReservationError(ValueError):
    """`code` is a fixed word; nothing of the reservation is echoed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def sale_account(exchange_id, seller: str) -> str:
    return f"sale:{exchange_id.value}:{seller}"


def reserve_effect(exchange_id, seller: str) -> str:
    return f"reserve:{exchange_id.value}:{seller}"


@dataclass(frozen=True)
class SaleReservation:
    ref: IssuerRef
    exchange_id: object
    seller: str
    account: str          # the sale's own account the units were posted into
    units: int
    effect: str
    from_account: str     # the seller's account they came from
    signed: auth.SignedReceipt


def reserve_for_sale(poster, ref: IssuerRef, exchange_id, seller: str, from_account: str,
                     units: int) -> SaleReservation:
    """Post the seller's units into the sale's own account through the authority and return what was posted. The same
    reservation again is a duplicate and returns the same receipt."""
    opaque(seller, "seller")
    if type(units) is not int or units < 1:
        raise ReservationError("units")
    effect, account = reserve_effect(exchange_id, seller), sale_account(exchange_id, seller)
    outcome, signed = poster.execute({"issuer": ref.issuer_id, "effect": effect, "action": effect,
                                      "postings": {from_account: -units, account: units}})
    if outcome not in (ij.Outcome.POSTED, ij.Outcome.DUPLICATE) or signed is None:
        raise ReservationError(outcome.value)
    return SaleReservation(ref, exchange_id, seller, account, units, effect, from_account, signed)


def verify_reservation(r, public_key: str) -> None:
    """Raises ReservationError unless this is a reservation of exactly the stated units, for this seller, that the
    authority's signed head commits to."""
    if not isinstance(r, SaleReservation):
        raise ReservationError("not_a_reservation")
    try:
        if type(r.units) is not int or r.units < 1 or r.account != sale_account(r.exchange_id, r.seller):
            raise ReservationError("account")
        auth.verify_receipt(r.signed, public_key, r.ref.charter_digest)
        body = wire.decode(r.signed.receipt)
        if (body["issuer"] != r.ref.issuer_id or body["effect"] != r.effect
                or r.effect != reserve_effect(r.exchange_id, r.seller)):
            raise ReservationError("effect")
        postings = {r.from_account: -r.units, r.account: r.units}
        if body["request_hash"] != ij.request_hash(r.ref.issuer_id, r.effect, postings, None, None):
            raise ReservationError("request")
    except ReservationError:
        raise
    except (auth.HeadError, wire.WireError, KeyError, TypeError, AttributeError, ValueError) as err:
        raise ReservationError("receipt") from err

"""Exchange orders, quotes, fills and matching (NS-40.4-11, design D.2). Pure: no network, no
storage, no provider call. TEST CREDITS AND SYNTHETIC CASH ONLY.

Rules and the tests that hold them (tests/unit/test_exchange_orders.py):
- D-1 a sell order exists only with a posted reservation receipt:
  test_a_sell_order_needs_a_posted_reservation_receipt_of_its_own_issuer.
- D-2 a buy order needs verified, unexpired buying power covering principal plus the maximum fee, and
  expiry is checked again at match time: test_a_buy_order_needs_enough_unexpired_buying_power,
  test_buying_power_that_expired_before_the_match_stops_it.
- D-3 orders match only for the same issuer, unit, currency and provider with crossing limits, at the
  resting order's price: test_the_bundle_market_vectors_pass_through_matching,
  test_orders_of_another_issuer_unit_currency_or_provider_never_match.
- D-4 a lot price is a whole number of minor units, refused at entry otherwise:
  test_a_lot_price_that_is_not_whole_minor_units_is_refused_at_entry.
- D-5 a quote is frozen and hashed, and a fill names it: test_a_quote_is_hashed_over_every_field.
- D-6 an unmatched balance has no executable quote: test_an_unmatched_holding_has_no_executable_quote.
- D-7 a work order and a sale never share an id: test_a_work_order_id_is_not_an_exchange_id.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, fields
from enum import Enum

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.exchange import fees
from agent_friday.services.exchange.provider import BuyingPower, _minor, opaque
from agent_friday.services.issuer_journal import RECEIPT_PROTOCOL, cash_fee
from agent_friday.services.issuer_registry import IssuerRef

QUOTE_PREFIX = b"asimov-exchange-quote/v1" + bytes([0])
_CURRENCY = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True)
class _Id:
    value: str

    def __post_init__(self):
        opaque(self.value, "id")


class ExchangeId(_Id):
    """A sale's own id."""


class OrderId(_Id):
    pass


class FillId(_Id):
    pass


class WorkOrderId(_Id):
    """A work order's id. It is never accepted where an ExchangeId is."""


def _need(value, kind):
    if type(value) is not kind:
        raise TypeError(f"expected {kind.__name__}, not {type(value).__name__}")
    return value


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderRefusal(str, Enum):
    PRICE_NOT_WHOLE = "price-not-whole"
    BAD_FIELD = "bad-field"
    NO_BUYING_POWER = "no-buying-power"
    BUYING_POWER_EXPIRED = "buying-power-expired"
    BUYING_POWER_SHORT = "buying-power-short"
    NO_RESERVATION = "no-reservation"
    EXPIRED = "expired"


class NoMatch(str, Enum):
    NOT_A_PAIR = "not-a-pair"
    DIFFERENT_MARKET = "different-market"
    LOT_SIZE = "lot-size"
    EXPIRED = "expired"
    BUYING_POWER_EXPIRED = "buying-power-expired"
    NO_CROSS = "no-cross"
    UNFUNDED = "unfunded"


@dataclass(frozen=True)
class Order:
    order_id: OrderId
    side: Side
    party: str
    ref: IssuerRef
    quote_currency: str
    lot_units: int
    lots: int
    limit_price_per_lot_minor: int
    expiry: int
    provider_id: str
    committed_seq: int
    buying_power: BuyingPower | None = None
    reserve_effect: str | None = None

    def __post_init__(self):
        _need(self.order_id, OrderId)
        opaque(self.party, "party")
        opaque(self.provider_id, "provider id")
        if not isinstance(self.side, Side) or not isinstance(self.ref, IssuerRef):
            raise ValueError("side and issuer are typed")
        if not _CURRENCY.fullmatch(self.quote_currency or ""):
            raise ValueError("currency is an ISO-style code")
        for name in ("lot_units", "lots", "limit_price_per_lot_minor", "committed_seq"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a whole number of at least 1")
        _minor(self.expiry, "expiry")
        if self.side is Side.BUY and (self.buying_power is None or self.reserve_effect is not None):
            raise ValueError("a buy order carries buying power and no reservation")
        if self.side is Side.SELL and (self.reserve_effect is None or self.buying_power is not None):
            raise ValueError("a sell order carries a reservation and no buying power")

    def market(self) -> tuple:
        return (self.ref, self.quote_currency, self.provider_id, self.lot_units)


def _entry(fields_given: dict, now: int):
    price = fields_given.get("limit_price_per_lot_minor")
    if type(price) is not int:
        return OrderRefusal.PRICE_NOT_WHOLE
    if type(now) is not int or type(fields_given.get("expiry")) is not int or fields_given["expiry"] <= now:
        return OrderRefusal.EXPIRED
    return None


def place_buy(*, order_id: OrderId, party: str, ref: IssuerRef, quote_currency: str, lot_units: int, lots: int,
              limit_price_per_lot_minor, expiry: int, provider_id: str, committed_seq: int,
              buying_power, now: int) -> "Order | OrderRefusal":
    """A buy order, or the reason it is refused. The buying power must cover principal plus the maximum fee."""
    refused = _entry({"limit_price_per_lot_minor": limit_price_per_lot_minor, "expiry": expiry}, now)
    if refused:
        return refused
    if not isinstance(buying_power, BuyingPower):
        return OrderRefusal.NO_BUYING_POWER
    if buying_power.expires_at <= now:
        return OrderRefusal.BUYING_POWER_EXPIRED
    try:
        order = Order(order_id, Side.BUY, party, ref, quote_currency, lot_units, lots, limit_price_per_lot_minor,
                      expiry, provider_id, committed_seq, buying_power=buying_power)
    except (ValueError, TypeError):
        return OrderRefusal.BAD_FIELD
    principal = lots * limit_price_per_lot_minor
    if (buying_power.currency != quote_currency or buying_power.payer != party
            or buying_power.amount_minor < principal + cash_fee(principal)):
        return OrderRefusal.BUYING_POWER_SHORT
    return order


def place_sell(*, order_id: OrderId, party: str, ref: IssuerRef, quote_currency: str, lot_units: int, lots: int,
               limit_price_per_lot_minor, expiry: int, provider_id: str, reserve_receipt, now: int
               ) -> "Order | OrderRefusal":
    """A sell order, accepted only with a posted reservation receipt of this issuer. The order's
    committed sequence is the receipt's own sequence."""
    refused = _entry({"limit_price_per_lot_minor": limit_price_per_lot_minor, "expiry": expiry}, now)
    if refused:
        return refused
    try:
        body = wire.decode(reserve_receipt)
    except (wire.WireError, TypeError):
        return OrderRefusal.NO_RESERVATION
    if (not isinstance(body, dict) or body.get("protocol") != RECEIPT_PROTOCOL
            or body.get("issuer") != ref.issuer_id or type(body.get("seq")) is not int or body["seq"] < 1
            or not isinstance(body.get("effect"), str)):
        return OrderRefusal.NO_RESERVATION
    try:
        return Order(order_id, Side.SELL, party, ref, quote_currency, lot_units, lots, limit_price_per_lot_minor,
                     expiry, provider_id, body["seq"], reserve_effect=body["effect"])
    except (ValueError, TypeError):
        return OrderRefusal.BAD_FIELD


@dataclass(frozen=True)
class Match:
    lots: int
    price_per_lot_minor: int
    principal_minor: int
    maximum_fee_minor: int
    resting: Side


def match(buy: Order, sell: Order, now: int) -> "Match | NoMatch":
    """The largest whole number of lots the buyer's verified cash covers with its fee, at the price of
    the order that has waited longer. Buying power is re-checked here, not only at order time."""
    if buy.side is not Side.BUY or sell.side is not Side.SELL:
        return NoMatch.NOT_A_PAIR
    if buy.market()[:3] != sell.market()[:3]:
        return NoMatch.DIFFERENT_MARKET
    if buy.lot_units != sell.lot_units:
        return NoMatch.LOT_SIZE
    if buy.expiry <= now or sell.expiry <= now:
        return NoMatch.EXPIRED
    if buy.buying_power.expires_at <= now:
        return NoMatch.BUYING_POWER_EXPIRED
    if buy.limit_price_per_lot_minor < sell.limit_price_per_lot_minor:
        return NoMatch.NO_CROSS
    resting = Side.SELL if sell.committed_seq < buy.committed_seq else Side.BUY
    price = sell.limit_price_per_lot_minor if resting is Side.SELL else buy.limit_price_per_lot_minor
    cash = buy.buying_power.amount_minor
    lots = min(buy.lots, sell.lots, cash // price)
    while lots and lots * price + cash_fee(lots * price) > cash:
        lots -= 1
    if not lots:
        return NoMatch.UNFUNDED
    return Match(lots, price, lots * price, cash_fee(lots * price), resting)


def best_counterparty(order: Order, book: list, now: int):
    """The best crossing order in `book` for `order` (best price for it, then earliest committed
    sequence), with its match; None when nothing matches."""
    best = None
    for other in book:
        pair = (order, other) if order.side is Side.BUY else (other, order)
        try:
            found = match(pair[0], pair[1], now)
        except AttributeError:
            continue
        if isinstance(found, Match):
            rank = (found.price_per_lot_minor if order.side is Side.BUY else -found.price_per_lot_minor,
                    other.committed_seq)
            if best is None or rank < best[0]:
                best = (rank, other, found)
    return None if best is None else (best[1], best[2])


@dataclass(frozen=True)
class Quote:
    """What an accepted trade fixes. Hashed; a fill names the hash."""
    price_per_lot_minor: int
    lots: int
    currency: str
    fee_base_minor: int
    cash_recipient: str
    payer: str
    expiry: int

    def __post_init__(self):
        for name in ("price_per_lot_minor", "lots", "fee_base_minor", "expiry"):
            _minor(getattr(self, name), name)
        opaque(self.cash_recipient, "recipient")
        opaque(self.payer, "payer")

    def to_wire(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def quote_hash(quote: Quote) -> str:
    return hashlib.sha256(QUOTE_PREFIX + wire.canonical(quote.to_wire())).hexdigest()


def executable_quote(sell: Order, bids: list, now: int) -> "Quote | None":
    """A quote only while a funded buyer crosses; an unmatched balance has none (D-6)."""
    found = best_counterparty(sell, bids, now)
    if found is None:
        return None
    bid, matched = found
    return Quote(matched.price_per_lot_minor, matched.lots, sell.quote_currency, matched.principal_minor,
                 sell.party, bid.party, min(sell.expiry, bid.expiry, bid.buying_power.expires_at))


@dataclass(frozen=True)
class Fill:
    fill_id: FillId
    exchange_id: ExchangeId
    quote_hash: str
    lots: int
    price_per_lot_minor: int
    principal_minor: int
    fee_increment_minor: int


def plan_fill(exchange_id: ExchangeId, fill_id: FillId, quote: Quote, settled_principal_before: int) -> Fill:
    """A fill of an accepted quote. The fee increment is PLANNED from the principal already settled on
    this exchange order; nothing accrues until the fill is confirmed (settlement, a later PR)."""
    _need(exchange_id, ExchangeId)
    _need(fill_id, FillId)
    principal = quote.lots * quote.price_per_lot_minor
    return Fill(fill_id, exchange_id, quote_hash(quote), quote.lots, quote.price_per_lot_minor, principal,
                fees.fee_increment(settled_principal_before, principal))

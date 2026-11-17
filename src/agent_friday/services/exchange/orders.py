"""Exchange orders, quotes, fills and matching (NS-40.4-11, design D.2 and section 10.5). Pure: no network, no
storage, no provider call. TEST CREDITS AND SYNTHETIC CASH ONLY.

Rules and the tests that hold them (tests/unit/test_exchange_orders.py):
- D-1' a sell order exists only with a verified SaleReservation that is the seller's, in this issuer, and covers
  lots * lot_units: test_a_sell_order_is_accepted_only_with_a_verified_reservation_that_covers_it,
  test_a_sell_order_on_an_unrelated_receipt_or_a_forged_one_is_refused.
- D-2' a buy order needs verified, unexpired buying power of ITS OWN provider covering principal plus the maximum fee,
  and expiry is checked again at match time: test_a_buy_order_needs_enough_unexpired_buying_power_of_its_own_provider,
  test_buying_power_from_one_provider_is_refused_on_an_order_for_another,
  test_buying_power_that_expired_before_the_match_stops_it.
- D-3' both sides take their place from ONE counter, assigned when the order is committed; "waited longer" compares
  `book_seq` only: test_sell_committed_first_buy_later_the_price_is_the_ask_whatever_the_journal_sequence,
  test_both_sides_take_their_place_from_one_counter. Orders match only for the same issuer, unit, currency and provider
  with crossing limits, at the resting order's price: test_orders_of_another_issuer_unit_currency_or_provider_never_match.
- D-4 a lot price is a whole number of minor units, refused at entry otherwise:
  test_a_lot_price_that_is_not_whole_minor_units_is_refused_at_entry.
- D-5' a quote is frozen and hashed over its issuer, unit, lot size, provider and exchange as well as its numbers, and a
  fill names it: test_a_quote_is_hashed_over_every_field_including_issuer_lot_size_provider_and_exchange.
- D-6 an unmatched balance has no executable quote: test_an_unmatched_holding_has_no_executable_quote.
- D-7 a work order and a sale never share an id: test_a_work_order_id_is_not_an_exchange_id.
- D-8 an Order is built only by place_buy and place_sell: tests/unit/test_exchange_guards.py::test_an_order_is_built_only_by_place_buy_and_place_sell.
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, fields
from enum import Enum

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.exchange import fees
from agent_friday.services.exchange import reservation as res
from agent_friday.services.exchange.provider import BuyingPower, _minor, opaque
from agent_friday.services.issuer_journal import cash_fee
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
    BUYING_POWER_PROVIDER = "buying-power-is-another-providers"
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


class OrderBook:
    """The one counter both sides of the book take their place from, when an order is committed."""

    def __init__(self):
        self._last = 0
        self._lock = threading.Lock()

    def assign(self) -> int:
        with self._lock:
            self._last += 1
            return self._last

    @property
    def last(self) -> int:
        return self._last


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
    book_seq: int
    buying_power: BuyingPower | None = None
    reservation: res.SaleReservation | None = None

    def __post_init__(self):
        _need(self.order_id, OrderId)
        opaque(self.party, "party")
        opaque(self.provider_id, "provider id")
        if not isinstance(self.side, Side) or not isinstance(self.ref, IssuerRef):
            raise ValueError("side and issuer are typed")
        if not _CURRENCY.fullmatch(self.quote_currency or ""):
            raise ValueError("currency is an ISO-style code")
        for name in ("lot_units", "lots", "limit_price_per_lot_minor", "book_seq"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a whole number of at least 1")
        _minor(self.expiry, "expiry")
        if self.side is Side.BUY and (not isinstance(self.buying_power, BuyingPower) or self.reservation is not None):
            raise ValueError("a buy order carries buying power and no reservation")
        if self.side is Side.SELL and (not isinstance(self.reservation, res.SaleReservation)
                                       or self.buying_power is not None):
            raise ValueError("a sell order carries a reservation and no buying power")

    def market(self) -> tuple:
        return (self.ref, self.quote_currency, self.provider_id, self.lot_units)


def _entry(price, expiry, now: int):
    if type(price) is not int:
        return OrderRefusal.PRICE_NOT_WHOLE
    if type(now) is not int or type(expiry) is not int or expiry <= now:
        return OrderRefusal.EXPIRED
    return None


def place_buy(*, book: OrderBook, order_id: OrderId, party: str, ref: IssuerRef, quote_currency: str, lot_units: int,
              lots: int, limit_price_per_lot_minor, expiry: int, provider_id: str, buying_power,
              now: int) -> "Order | OrderRefusal":
    """A buy order, or the reason it is refused. The buying power must be this provider's, unexpired, and cover
    principal plus the maximum fee. A refused order takes no place in the book."""
    refused = _entry(limit_price_per_lot_minor, expiry, now)
    if refused:
        return refused
    if not isinstance(buying_power, BuyingPower):
        return OrderRefusal.NO_BUYING_POWER
    if buying_power.provider_id != provider_id:
        return OrderRefusal.BUYING_POWER_PROVIDER
    if buying_power.expires_at <= now:
        return OrderRefusal.BUYING_POWER_EXPIRED
    try:
        principal = lots * limit_price_per_lot_minor
        covered = (buying_power.currency == quote_currency and buying_power.payer == party
                   and buying_power.amount_minor >= principal + cash_fee(principal))
        if not covered:
            return OrderRefusal.BUYING_POWER_SHORT
        return Order(order_id, Side.BUY, party, ref, quote_currency, lot_units, lots, limit_price_per_lot_minor, expiry,
                     provider_id, book.assign(), buying_power=buying_power)
    except (ValueError, TypeError):
        return OrderRefusal.BAD_FIELD


def place_sell(*, book: OrderBook, order_id: OrderId, party: str, ref: IssuerRef, quote_currency: str, lot_units: int,
               lots: int, limit_price_per_lot_minor, expiry: int, provider_id: str, reservation, public_key: str,
               now: int) -> "Order | OrderRefusal":
    """A sell order, accepted only with a verified reservation of this seller's, in this issuer, that covers the
    order's units. A refused order takes no place in the book."""
    refused = _entry(limit_price_per_lot_minor, expiry, now)
    if refused:
        return refused
    try:
        res.verify_reservation(reservation, public_key)
        if (reservation.seller != party or reservation.ref != ref or type(lots) is not int or type(lot_units) is not int
                or reservation.units < lots * lot_units):
            return OrderRefusal.NO_RESERVATION
    except res.ReservationError:
        return OrderRefusal.NO_RESERVATION
    try:
        return Order(order_id, Side.SELL, party, ref, quote_currency, lot_units, lots, limit_price_per_lot_minor, expiry,
                     provider_id, book.assign(), reservation=reservation)
    except (ValueError, TypeError):
        return OrderRefusal.BAD_FIELD


@dataclass(frozen=True)
class Match:
    lots: int
    price_per_lot_minor: int
    principal_minor: int
    maximum_fee_minor: int
    resting: Side | None = None


def size_fill(price, buy_lots: int, sell_lots: int, cash: int) -> "Match | None":
    """The largest whole number of lots the buyer's cash covers with its fee at `price`, or None. A price of None is
    no crossing."""
    if price is None:
        return None
    lots = min(buy_lots, sell_lots, cash // price)
    while lots and lots * price + cash_fee(lots * price) > cash:
        lots -= 1
    if not lots:
        return None
    return Match(lots, price, lots * price, cash_fee(lots * price))


def match(buy: Order, sell: Order, now: int) -> "Match | NoMatch":
    """The largest whole number of lots the buyer's cash covers with its fee, at the price of the order that was
    committed first (the one with the lower book sequence). Buying power is re-checked here, not only at order time."""
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
    resting = Side.SELL if sell.book_seq < buy.book_seq else Side.BUY
    price = sell.limit_price_per_lot_minor if resting is Side.SELL else buy.limit_price_per_lot_minor
    sized = size_fill(price, buy.lots, sell.lots, buy.buying_power.amount_minor)
    if sized is None:
        return NoMatch.UNFUNDED
    return Match(sized.lots, sized.price_per_lot_minor, sized.principal_minor, sized.maximum_fee_minor, resting)


def best_counterparty(order: Order, book: list, now: int):
    """The best crossing order in `book` for `order` (best price for it, then earliest book sequence), with its match;
    None when nothing matches."""
    best = None
    for other in book:
        pair = (order, other) if order.side is Side.BUY else (other, order)
        try:
            found = match(pair[0], pair[1], now)
        except AttributeError:
            continue
        if isinstance(found, Match):
            # best for `order` is the counterparty's own limit: the lowest ask for a buyer, the highest bid for a seller
            rank = (other.limit_price_per_lot_minor if order.side is Side.BUY else -other.limit_price_per_lot_minor,
                    other.book_seq)
            if best is None or rank < best[0]:
                best = (rank, other, found)
    return None if best is None else (best[1], best[2])


@dataclass(frozen=True)
class Quote:
    """What an accepted trade fixes: the market it is in (issuer and unit, lot size, provider, exchange) and the
    trade. Hashed; a fill names the hash."""
    ref: IssuerRef
    lot_units: int
    provider_id: str
    exchange_id: str
    price_per_lot_minor: int
    lots: int
    currency: str
    fee_base_minor: int
    cash_recipient: str
    payer: str
    expiry: int

    def __post_init__(self):
        if not isinstance(self.ref, IssuerRef):
            raise ValueError("a quote names its issuer")
        for name in ("lot_units", "price_per_lot_minor", "lots", "fee_base_minor", "expiry"):
            _minor(getattr(self, name), name)
        for name in ("provider_id", "exchange_id", "cash_recipient", "payer"):
            opaque(getattr(self, name), name)
        if not _CURRENCY.fullmatch(self.currency or ""):
            raise ValueError("currency is an ISO-style code")

    def to_wire(self) -> dict:
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "ref"}
        out["ref"] = {"issuer_id": self.ref.issuer_id, "charter_digest": self.ref.charter_digest,
                      "unit_code": self.ref.unit_code}
        return out


def quote_hash(quote: Quote) -> str:
    return hashlib.sha256(QUOTE_PREFIX + wire.canonical(quote.to_wire())).hexdigest()


def executable_quote(sell: Order, bids: list, now: int) -> "Quote | None":
    """A quote only while a funded buyer crosses; an unmatched balance has none (D-6)."""
    found = best_counterparty(sell, bids, now)
    if found is None:
        return None
    bid, matched = found
    return Quote(sell.ref, sell.lot_units, sell.provider_id, sell.reservation.exchange_id.value,
                 matched.price_per_lot_minor, matched.lots, sell.quote_currency, matched.principal_minor, sell.party,
                 bid.party, min(sell.expiry, bid.expiry, bid.buying_power.expires_at))


@dataclass(frozen=True)
class Fill:
    fill_id: FillId
    exchange_id: ExchangeId
    quote_hash: str
    lots: int
    price_per_lot_minor: int
    principal_minor: int
    fee_increment_minor: int
    lot_units: int          # the order's lot size: a fill's units are lots * lot_units, derived and never passed beside it


def plan_fill(exchange_id: ExchangeId, fill_id: FillId, quote: Quote, settled_principal_before: int) -> Fill:
    """A fill of an accepted quote. The fee increment is PLANNED from the principal already settled on this exchange
    order; nothing accrues until the fill's cash is confirmed (settlement)."""
    _need(exchange_id, ExchangeId)
    _need(fill_id, FillId)
    principal = quote.lots * quote.price_per_lot_minor
    return Fill(fill_id, exchange_id, quote_hash(quote), quote.lots, quote.price_per_lot_minor, principal,
                fees.fee_increment(settled_principal_before, principal), quote.lot_units)

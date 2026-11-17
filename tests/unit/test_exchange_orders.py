"""Exchange orders, quotes, fills, fees and provider registration (NS-40.4-11/-12, design Part D and section 10.5).
Synthetic cash and test credits only."""
import dataclasses
import itertools
import json
from pathlib import Path

import pytest

from agent_friday.services import issuer_authority as auth
from agent_friday.services import issuer_journal as ij
from agent_friday.services.exchange import fees, orders, provider
from agent_friday.services.exchange import reservation as res
from agent_friday.services.issuer_registry import IssuerCharter, IssuerRef

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "economy-vectors.json").read_text(encoding="utf-8"))
NOW = 1_000
KEY = auth.Ed25519Key(bytes(range(32)))
CHARTER = IssuerCharter(issuer_id="test.one", unit_code="TESTP", precision=0, genesis_supply=1_000_000,
                        treasury_account="treasury", cash_claim=False, genesis_authority=KEY.public_key,
                        created="2026-11-16T00:00:00Z")
REF = IssuerRef.of(CHARTER)
XID = orders.ExchangeId("x1")


@pytest.fixture
def authority(tmp_path):
    with auth.Authority(tmp_path / "j.sqlite", CHARTER, KEY) as a:
        for who, units in (("seller-1", 10_000), ("seller-2", 10_000)):
            outcome, _ = a.execute({"issuer": "test.one", "effect": f"fund-{who}", "action": f"fund-{who}",
                                    "postings": {"treasury": -units, f"seller:{who}": units}})
            assert outcome is ij.Outcome.POSTED
        yield a


def reserve(a, seller="seller-1", units=1000, exchange=XID):
    return res.reserve_for_sale(a, REF, exchange, seller, f"seller:{seller}", units)


def power(amount, expires=NOW + 500, payer="buyer-1", currency="USD", provider_id="synthetic"):
    return provider.BuyingPower(provider_id, "bp-1", payer, currency, amount, expires, NOW)


def common(book, **over):
    base = dict(book=book, party="buyer-1", ref=REF, quote_currency="USD", lot_units=1, lots=10,
                limit_price_per_lot_minor=100, expiry=NOW + 900, provider_id="synthetic", now=NOW)
    base.update(over)
    return base


def buy(book, price, lots, cash, **over):
    kw = common(book, order_id=orders.OrderId("b1"), limit_price_per_lot_minor=price, lots=lots,
                buying_power=power(cash), **over)
    return orders.place_buy(**kw)


_exchanges = itertools.count(100)


def sell(book, a, price, lots, seller="seller-1", **over):
    lot_units = over.get("lot_units", 1)
    if "reservation" in over:
        reservation = over.pop("reservation")
    else:                                   # a fresh sale of its own, unless the test names the reservation
        reservation = reserve(a, seller, lots * lot_units, exchange=orders.ExchangeId(f"x{next(_exchanges)}"))
    kw = common(book, order_id=orders.OrderId("s1"), party=seller, limit_price_per_lot_minor=price, lots=lots,
                reservation=reservation, public_key=KEY.public_key, **over)
    kw.pop("buying_power", None)
    return orders.place_sell(**kw)


def test_the_bundle_market_vectors_pass_through_fill_sizing():
    """The vectors let the buyer's cash cover fewer lots than ordered; entry is strict (D-2'), sizing is not."""
    for case in VECTORS["market"]:
        price = case[case["resting"]] if case["bid"] >= case["ask"] else None
        found = orders.size_fill(price, case["buy_lots"], case["sell_lots"], case["cash_available"])
        if case["match_lots"] == 0:
            assert found is None, case["name"]
            continue
        assert (found.lots, found.price_per_lot_minor) == (case["match_lots"], case["price_per_lot"]), case["name"]
        assert found.principal_minor == case["expected_seller"] and found.maximum_fee_minor == case["expected_fee"]


def test_the_bundle_cash_vectors_pass_through_fees():
    for case in VECTORS["cash"]:
        charged, before = [], 0
        for principal in case["minor_increments"]:
            charged.append(fees.fee_increment(before, principal))
            before += principal
        assert charged == case["incremental_fees"], case["name"]
        assert before + sum(charged) == case["buyer"], case["name"]


def test_a_hundred_dollars_filled_as_fifty_and_fifty_charges_thirteen_then_twelve():
    assert [fees.fee_increment(0, 5000), fees.fee_increment(5000, 5000)] == [13, 12]
    assert fees.fee_increment(0, 10000) == 25 == ij.cash_fee(10000)
    for bad in (-1, 1.5, True, "5"):
        with pytest.raises(ValueError):
            fees.fee_increment(0, bad)


def test_the_fee_total_never_exceeds_the_cumulative_fee_in_any_confirmation_order():
    """S-12: charged in any order, the sum is what one order would pay; a fee charged twice is over."""
    principals = [5000, 5000, 1234, 7]
    for order in ([0, 1, 2, 3], [3, 2, 1, 0], [1, 3, 0, 2]):
        charged = [fees.planned_increments(principals)[i] for i in order]
        assert fees.within_cumulative(charged, principals)
    assert fees.within_cumulative([13, 12], [5000, 5000]) and fees.within_cumulative([0, 0], [5000, 5000])
    assert not fees.within_cumulative([13, 13], [5000, 5000]) and not fees.within_cumulative([26], [5000, 5000])
    assert fees.planned_increments([5000, 5000]) == [13, 12]
    assert not hasattr(fees, "FeeBook"), "the in-memory FeeBook is gone: settlement accounts in its own store"


# ── entry rules ──────────────────────────────────────────────────────────────────────────────────

def test_a_lot_price_that_is_not_whole_minor_units_is_refused_at_entry(authority):
    book = orders.OrderBook()
    for price in (2.5, "2", None, True, 0.1 + 0.2):
        assert buy(book, price, 1, 10_000) is orders.OrderRefusal.PRICE_NOT_WHOLE
        assert sell(book, authority, price, 1) is orders.OrderRefusal.PRICE_NOT_WHOLE
    assert book.last == 0, "a refused order takes no place in the book"


def test_a_buy_order_needs_enough_unexpired_buying_power_of_its_own_provider():
    book = orders.OrderBook()
    needed = 10 * 100 + ij.cash_fee(1000)
    assert isinstance(buy(book, 100, 10, needed), orders.Order)
    assert buy(book, 100, 10, needed - 1) is orders.OrderRefusal.BUYING_POWER_SHORT
    assert orders.place_buy(**common(book, order_id=orders.OrderId("b1"), buying_power=None))         is orders.OrderRefusal.NO_BUYING_POWER
    kw = common(book, order_id=orders.OrderId("b1"), buying_power={"amount": 10 ** 9})
    assert orders.place_buy(**kw) is orders.OrderRefusal.NO_BUYING_POWER
    assert orders.place_buy(**common(book, order_id=orders.OrderId("b1"), buying_power=power(needed, expires=NOW))) \
        is orders.OrderRefusal.BUYING_POWER_EXPIRED
    for wrong in (power(needed, payer="x"), power(needed, currency="EUR")):
        assert orders.place_buy(**common(book, order_id=orders.OrderId("b1"), buying_power=wrong)) \
            is orders.OrderRefusal.BUYING_POWER_SHORT
    assert orders.place_buy(**common(book, order_id=orders.OrderId("b1"), expiry=NOW, buying_power=power(needed))) \
        is orders.OrderRefusal.EXPIRED


def test_buying_power_from_one_provider_is_refused_on_an_order_for_another():
    """T11 / D-2'."""
    needed = 10 * 100 + ij.cash_fee(1000)
    book = orders.OrderBook()
    other = power(needed, provider_id="another-provider")
    assert orders.place_buy(**common(book, order_id=orders.OrderId("b1"), buying_power=other)) \
        is orders.OrderRefusal.BUYING_POWER_PROVIDER
    assert isinstance(orders.place_buy(**common(book, order_id=orders.OrderId("b1"), buying_power=power(needed))), orders.Order)


def test_buying_power_that_expired_before_the_match_stops_it(authority):
    book = orders.OrderBook()
    s = sell(book, authority, 10, 5)
    b = buy(book, 10, 5, 1000)
    assert isinstance(orders.match(b, s, NOW), orders.Match)
    assert orders.match(b, s, NOW + 500) is orders.NoMatch.BUYING_POWER_EXPIRED
    assert orders.match(b, s, NOW + 900) is orders.NoMatch.EXPIRED


# ── T8: a sell order needs a verified reservation that is the seller's and covers its units ──────────

def test_a_sell_order_is_accepted_only_with_a_verified_reservation_that_covers_it(authority):
    book = orders.OrderBook()
    ok = sell(book, authority, 10, 5)
    assert isinstance(ok, orders.Order) and ok.reservation.seller == "seller-1" and ok.reservation.units == 5
    # more lots than the reservation holds
    covered = reserve(authority, units=5, exchange=orders.ExchangeId("x2"))
    assert sell(book, authority, 10, 6, reservation=covered) is orders.OrderRefusal.NO_RESERVATION
    assert isinstance(sell(book, authority, 10, 5, reservation=covered), orders.Order)
    # lot size multiplies: 5 lots of 2 units need 10 units
    assert sell(book, authority, 10, 5, lot_units=2, reservation=covered) is orders.OrderRefusal.NO_RESERVATION
    # another seller's reservation
    theirs = reserve(authority, seller="seller-2", units=100, exchange=orders.ExchangeId("x3"))
    assert sell(book, authority, 10, 5, seller="seller-1", reservation=theirs) is orders.OrderRefusal.NO_RESERVATION


def test_a_sell_order_on_an_unrelated_receipt_or_a_forged_one_is_refused(authority):
    """T8: the receipt of an unrelated effect, or one whose head does not commit to it, buys nothing."""
    book = orders.OrderBook()
    _, unrelated = authority.execute({"issuer": "test.one", "effect": "pay-the-baker", "action": "pay-the-baker",
                                      "postings": {"treasury": -7, "baker": 7}})
    real = reserve(authority, units=5)
    wrong_receipt = res.SaleReservation(real.ref, real.exchange_id, "mallory", real.account, 999_999, "pay-the-baker",
                                        real.from_account, unrelated)
    assert sell(book, authority, 10, 999_999, seller="mallory", reservation=wrong_receipt) is orders.OrderRefusal.NO_RESERVATION
    other_head = res.SaleReservation(real.ref, real.exchange_id, real.seller, real.account, real.units, real.effect,
                                     real.from_account, auth.SignedReceipt(real.signed.receipt, unrelated.head, unrelated.signature))
    assert sell(book, authority, 10, 5, reservation=other_head) is orders.OrderRefusal.NO_RESERVATION
    claims_more = res.SaleReservation(real.ref, real.exchange_id, real.seller, real.account, 500, real.effect,
                                      real.from_account, real.signed)
    assert sell(book, authority, 10, 400, reservation=claims_more) is orders.OrderRefusal.NO_RESERVATION
    other_issuer = res.SaleReservation(IssuerRef("test.two", REF.charter_digest, "TESTP"), real.exchange_id, real.seller,
                                       real.account, real.units, real.effect, real.from_account, real.signed)
    assert sell(book, authority, 10, 5, reservation=other_issuer) is orders.OrderRefusal.NO_RESERVATION
    assert sell(book, authority, 10, 5, reservation=None) is orders.OrderRefusal.NO_RESERVATION
    assert sell(book, authority, 10, 5, reservation="a receipt") is orders.OrderRefusal.NO_RESERVATION
    assert book.last == 0


def test_reserving_for_a_sale_moves_the_units_into_the_sales_own_account_once(authority):
    first = reserve(authority, units=40)
    again = reserve(authority, units=40)
    assert again.signed == first.signed, "the same reservation again is a duplicate with the same receipt"
    balances = authority.balances()
    assert balances["sale:x1:seller-1"] == 40 and balances["seller:seller-1"] == 9_960
    res.verify_reservation(first, KEY.public_key)
    with pytest.raises(res.ReservationError):
        reserve(authority, seller="seller-1", units=10 ** 9)        # more than the seller has
    with pytest.raises(res.ReservationError):
        res.verify_reservation(first, auth.Ed25519Key(bytes([9] * 32)).public_key)


# ── T9: one book sequence for both sides ─────────────────────────────────────────────────────────

def test_sell_committed_first_buy_later_the_price_is_the_ask_whatever_the_journal_sequence(authority):
    """T9: the sell's journal sequence is large, the buy's book sequence small; the price was still the ask."""
    for n in range(60):                                            # push the journal's own sequence up
        authority.execute({"issuer": "test.one", "effect": f"noise-{n}", "action": f"noise-{n}",
                           "postings": {"treasury": -1, f"noise:{n}": 1}})
    book = orders.OrderBook()
    s = sell(book, authority, 14, 5)
    b = buy(book, 16, 5, 1000)
    assert authority.journal.head() > 50 and s.book_seq == 1 and b.book_seq == 2
    found = orders.match(b, s, NOW)
    assert found.resting is orders.Side.SELL and found.price_per_lot_minor == 14


def test_the_waiting_order_sets_the_price_and_ties_go_to_the_earlier_one(authority):
    for bid, ask in ((16, 14), (14, 14), (20, 3)):
        book = orders.OrderBook()
        s, b = sell(book, authority, ask, 1), buy(book, bid, 1, 1000)
        assert orders.match(b, s, NOW).price_per_lot_minor == ask
        book = orders.OrderBook()
        b, s = buy(book, bid, 1, 1000), sell(book, authority, ask, 1)
        assert orders.match(b, s, NOW).price_per_lot_minor == bid and orders.match(b, s, NOW).resting is orders.Side.BUY


def test_both_sides_take_their_place_from_one_counter(authority):
    book = orders.OrderBook()
    seqs = [buy(book, 5, 1, 100).book_seq, sell(book, authority, 5, 1).book_seq, buy(book, 5, 1, 100).book_seq]
    assert seqs == [1, 2, 3] and book.last == 3
    assert not hasattr(orders.Order, "committed_seq")


def test_an_order_is_not_a_member_of_two_books_by_sharing_a_number(authority):
    a, b = orders.OrderBook(), orders.OrderBook()
    assert buy(a, 5, 1, 100).book_seq == 1 and buy(b, 5, 1, 100).book_seq == 1


# ── matching ────────────────────────────────────────────────────────────────────────────────────

def test_orders_of_another_issuer_unit_currency_or_provider_never_match(authority):
    book = orders.OrderBook()
    b = buy(book, 10, 5, 1000)
    assert isinstance(orders.match(b, sell(book, authority, 10, 5), NOW), orders.Match)
    s_eur = sell(book, authority, 10, 5, quote_currency="EUR")
    s_other = sell(book, authority, 10, 5, provider_id="other")
    assert orders.match(b, s_eur, NOW) is orders.NoMatch.DIFFERENT_MARKET
    assert orders.match(b, s_other, NOW) is orders.NoMatch.DIFFERENT_MARKET
    other_issuer = buy(book, 10, 5, 1000, ref=IssuerRef("test.two", REF.charter_digest, "TESTP"))
    assert orders.match(other_issuer, sell(book, authority, 10, 5), NOW) is orders.NoMatch.DIFFERENT_MARKET
    other_unit = buy(book, 10, 5, 1000, ref=IssuerRef("test.one", REF.charter_digest, "OTHERP"))
    assert orders.match(other_unit, sell(book, authority, 10, 5), NOW) is orders.NoMatch.DIFFERENT_MARKET
    assert orders.match(b, sell(book, authority, 10, 5, lot_units=2), NOW) is orders.NoMatch.LOT_SIZE
    assert orders.match(sell(book, authority, 10, 5), b, NOW) is orders.NoMatch.NOT_A_PAIR


def test_bid_below_ask_gives_no_fill_and_no_quote(authority):
    book = orders.OrderBook()
    b, s = buy(book, 9, 5, 1000), sell(book, authority, 10, 5)
    assert orders.match(b, s, NOW) is orders.NoMatch.NO_CROSS
    assert orders.executable_quote(s, [b], NOW) is None


def test_an_unmatched_holding_has_no_executable_quote(authority):
    book = orders.OrderBook()
    s = sell(book, authority, 10, 5)
    assert orders.executable_quote(s, [], NOW) is None
    quote = orders.executable_quote(s, [buy(book, 12, 5, 1000)], NOW)
    assert quote.price_per_lot_minor == 10 and quote.lots == 5 and quote.cash_recipient == "seller-1"


def test_the_best_price_then_the_earliest_sequence_wins(authority):
    book = orders.OrderBook()
    s = sell(book, authority, 10, 5)
    low = buy(book, 11, 5, 1000)
    high = buy(book, 12, 5, 1000)
    tie = buy(book, 12, 5, 1000)
    chosen, found = orders.best_counterparty(s, [low, high, tie], NOW)
    assert chosen is high, "the earlier of two equal bids"
    assert found.price_per_lot_minor == 10, "the sell order waited, so its price"


# ── T10: a quote names everything it is bound to ─────────────────────────────────────────────────

def quote(**over):
    base = dict(ref=REF, lot_units=1, provider_id="synthetic", exchange_id="x1", price_per_lot_minor=10, lots=5,
                currency="USD", fee_base_minor=50, cash_recipient="s1", payer="b1", expiry=2000)
    base.update(over)
    return orders.Quote(**base)


def test_a_quote_is_hashed_over_every_field_including_issuer_lot_size_provider_and_exchange():
    first = orders.quote_hash(quote())
    changes = dict(ref=IssuerRef("test.two", REF.charter_digest, "TESTP"), lot_units=2, provider_id="other",
                   exchange_id="x2", price_per_lot_minor=11, lots=6, currency="EUR", fee_base_minor=51,
                   cash_recipient="s2", payer="b2", expiry=2001)
    assert set(changes) == {f.name for f in dataclasses.fields(orders.Quote)}
    for name, value in changes.items():
        assert orders.quote_hash(quote(**{name: value})) != first, name
    for other_ref in (IssuerRef("test.one", "e" * 64, "TESTP"), IssuerRef("test.one", REF.charter_digest, "OTHERP")):
        assert orders.quote_hash(quote(ref=other_ref)) != first


def test_the_quote_of_an_executable_match_carries_the_orders_market(authority):
    book = orders.OrderBook()
    s = sell(book, authority, 10, 5)
    q = orders.executable_quote(s, [buy(book, 12, 5, 1000)], NOW)
    assert (q.ref, q.lot_units, q.provider_id, q.exchange_id) == (REF, 1, "synthetic", s.reservation.exchange_id.value)


def test_a_fill_names_its_quote_and_planning_accrues_nothing():
    q = quote(price_per_lot_minor=50, lots=100, fee_base_minor=5000)
    first = orders.plan_fill(orders.ExchangeId("x1"), orders.FillId("f1"), q, 0)
    second = orders.plan_fill(orders.ExchangeId("x1"), orders.FillId("f2"), q, 5000)
    assert (first.fee_increment_minor, second.fee_increment_minor) == (13, 12)
    assert first.quote_hash == orders.quote_hash(q) and first.principal_minor == 5000


def test_a_work_order_id_is_not_an_exchange_id():
    q = quote()
    work = orders.WorkOrderId("job-7")
    with pytest.raises(TypeError):
        orders.plan_fill(work, orders.FillId("f1"), q, 0)
    with pytest.raises(TypeError):
        orders.plan_fill(orders.ExchangeId("x1"), work, q, 0)
    assert orders.ExchangeId("a") != orders.WorkOrderId("a") and orders.OrderId("a") != orders.FillId("a")
    with pytest.raises(ValueError):
        orders.ExchangeId("has space")


# ── provider registration and the interface ──────────────────────────────────────────────────────

def desc(test_only=True, pid="synthetic"):
    return provider.ProviderDescriptor(pid, test_only, ("USD",), 1, "provider", True)


def test_a_live_provider_is_refused_whatever_the_federation_flags_say(monkeypatch):
    from agent_friday.services import held_features
    registry = {}
    for flags in ({}, {"federation": True}, {"federation": True, "fed_live_cash": True}):
        monkeypatch.setattr(held_features, "enabled", lambda feature, settings=None, f=flags: bool(f.get(feature)))
        with pytest.raises(provider.LiveProviderRefused):
            provider.register_provider(desc(test_only=False, pid="real"), registry)
    assert registry == {}
    provider.register_provider(desc(), registry)
    with pytest.raises(ValueError):
        provider.register_provider(desc(), registry)


def test_an_idempotency_key_is_stable_and_names_its_leg():
    key = provider.idempotency_key("x1", "f1", provider.Leg.CAPTURE)
    assert key == provider.idempotency_key("x1", "f1", provider.Leg.CAPTURE) == "x1/f1/capture/once"
    assert len({provider.idempotency_key("x1", "f1", leg) for leg in provider.Leg}) == len(provider.Leg)
    for bad in ("", "a b", None):
        with pytest.raises(ValueError):
            provider.idempotency_key(bad, "f1", provider.Leg.CAPTURE)


def test_uncertain_is_not_a_refusal():
    assert not isinstance(provider.Uncertain("k"), provider.Refused)
    assert not isinstance(provider.Refused(provider.RefusalCode.OUTAGE), provider.Uncertain)
    with pytest.raises(ValueError):
        provider.Uncertain("")


def test_buying_power_carries_its_provider():
    assert power(5).provider_id == "synthetic"
    with pytest.raises(ValueError):
        provider.BuyingPower("bad id", "bp-1", "b", "USD", 5, 10, 1)

"""Exchange orders, quotes, fills, fees and provider registration (NS-40.4-11/-12, design Part D, PR 6).
Synthetic cash and test credits only."""
import json
from pathlib import Path

import pytest

from agent_friday.services import issuer_journal as ij
from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.exchange import fees, orders, provider
from agent_friday.services.issuer_registry import IssuerRef

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                      / "economy-vectors.json").read_text(encoding="utf-8"))
REF = IssuerRef("test.one", "d" * 64, "TESTP")
NOW = 1_000


def receipt(seq, issuer="test.one", effect="reserve-1", protocol=ij.RECEIPT_PROTOCOL):
    return wire.canonical({"protocol": protocol, "issuer": issuer, "effect": effect, "seq": seq,
                           "request_hash": "a" * 64, "corrects": None, "reason": None})


def power(amount, expires=NOW + 500, payer="buyer-1", currency="USD"):
    return provider.BuyingPower("bp-1", payer, currency, amount, expires, NOW)


def buy(price, lots, cash, seq=2, **over):
    base = dict(order_id=orders.OrderId("b1"), side=orders.Side.BUY, party="buyer-1", ref=REF, quote_currency="USD",
                lot_units=1, lots=lots, limit_price_per_lot_minor=price, expiry=NOW + 900, provider_id="synthetic",
                committed_seq=seq, buying_power=power(cash))
    return orders.Order(**{**base, **over})


def sell(price, lots, seq=1, **over):
    base = dict(order_id=orders.OrderId("s1"), side=orders.Side.SELL, party="seller-1", ref=REF, quote_currency="USD",
                lot_units=1, lots=lots, limit_price_per_lot_minor=price, expiry=NOW + 900, provider_id="synthetic",
                committed_seq=seq, reserve_effect="reserve-1")
    return orders.Order(**{**base, **over})


def test_the_bundle_market_vectors_pass_through_matching():
    for case in VECTORS["market"]:
        ask_waited = case["resting"] == "ask"
        b = buy(case["bid"], case["buy_lots"], case["cash_available"], seq=2 if ask_waited else 1,
                lot_units=case["p_per_lot"])
        s = sell(case["ask"], case["sell_lots"], seq=1 if ask_waited else 2, lot_units=case["p_per_lot"])
        found = orders.match(b, s, NOW)
        if case["match_lots"] == 0:
            assert isinstance(found, orders.NoMatch), case["name"]
            continue
        assert found.lots == case["match_lots"] and found.price_per_lot_minor == case["price_per_lot"], case["name"]
        assert found.principal_minor == case["expected_seller"], case["name"]
        assert found.maximum_fee_minor == case["expected_fee"], case["name"]


def test_the_bundle_cash_vectors_pass_through_fees():
    for case in VECTORS["cash"]:
        book, charged = fees.FeeBook(), []
        for n, principal in enumerate(case["minor_increments"]):
            charged.append(book.accrue("x1", f"f{n}", principal))
        assert charged == case["incremental_fees"], case["name"]
        assert sum(case["minor_increments"]) == case["seller"]
        assert sum(case["minor_increments"]) + book.total("x1") == case["buyer"], case["name"]


def test_a_hundred_dollars_filled_as_fifty_and_fifty_charges_thirteen_then_twelve_and_replay_charges_nothing():
    book = fees.FeeBook()
    assert [book.accrue("x1", "f1", 5000), book.accrue("x1", "f2", 5000)] == [13, 12]
    assert book.accrue("x1", "f1", 5000) == 0 and book.accrue("x1", "f2", 5000) == 0
    assert book.total("x1") == 25 == ij.cash_fee(10000)
    assert book.accrue("x2", "f1", 10000) == 25, "another order has its own cumulative"


def test_an_unfilled_order_charges_nothing_and_bad_principals_are_refused():
    assert fees.FeeBook().total("x1") == 0
    for bad in (-1, 1.5, True, "5"):
        with pytest.raises(ValueError):
            fees.fee_increment(0, bad)


def test_the_planned_fee_matches_the_book_and_planning_accrues_nothing():
    quote = orders.Quote(50, 100, "USD", 5000, "seller-1", "buyer-1", NOW + 100)
    first = orders.plan_fill(orders.ExchangeId("x1"), orders.FillId("f1"), quote, 0)
    second = orders.plan_fill(orders.ExchangeId("x1"), orders.FillId("f2"), quote, 5000)
    assert (first.fee_increment_minor, second.fee_increment_minor) == (13, 12)
    assert first.quote_hash == orders.quote_hash(quote) and first.principal_minor == 5000


def test_a_lot_price_that_is_not_whole_minor_units_is_refused_at_entry():
    common = dict(order_id=orders.OrderId("b1"), party="buyer-1", ref=REF, quote_currency="USD", lot_units=1, lots=1,
                  expiry=NOW + 10, provider_id="synthetic", committed_seq=1, now=NOW)
    for price in (2.5, "2", None, True, 0.1 + 0.2):
        assert orders.place_buy(**common, limit_price_per_lot_minor=price,
                                buying_power=power(10_000)) is orders.OrderRefusal.PRICE_NOT_WHOLE
    sale = {k: v for k, v in common.items() if k not in ("committed_seq",)}
    assert orders.place_sell(**sale, limit_price_per_lot_minor=2.5,
                             reserve_receipt=receipt(1)) is orders.OrderRefusal.PRICE_NOT_WHOLE


def test_a_buy_order_needs_enough_unexpired_buying_power():
    common = dict(order_id=orders.OrderId("b1"), party="buyer-1", ref=REF, quote_currency="USD", lot_units=1, lots=10,
                  limit_price_per_lot_minor=100, expiry=NOW + 10, provider_id="synthetic", committed_seq=1, now=NOW)
    needed = 10 * 100 + ij.cash_fee(1000)
    assert isinstance(orders.place_buy(**common, buying_power=power(needed)), orders.Order)
    assert orders.place_buy(**common, buying_power=power(needed - 1)) is orders.OrderRefusal.BUYING_POWER_SHORT
    assert orders.place_buy(**common, buying_power=None) is orders.OrderRefusal.NO_BUYING_POWER
    assert orders.place_buy(**common, buying_power={"amount": 10 ** 9}) is orders.OrderRefusal.NO_BUYING_POWER
    assert orders.place_buy(**common, buying_power=power(needed, expires=NOW)) is orders.OrderRefusal.BUYING_POWER_EXPIRED
    assert orders.place_buy(**common, buying_power=power(needed, payer="someone-else")) is orders.OrderRefusal.BUYING_POWER_SHORT
    assert orders.place_buy(**common, buying_power=power(needed, currency="EUR")) is orders.OrderRefusal.BUYING_POWER_SHORT
    assert orders.place_buy(**{**common, "expiry": NOW}, buying_power=power(needed)) is orders.OrderRefusal.EXPIRED


def test_buying_power_that_expired_before_the_match_stops_it():
    b = buy(10, 5, 1000)
    s = sell(10, 5)
    assert isinstance(orders.match(b, s, NOW), orders.Match)
    assert orders.match(b, s, NOW + 500) is orders.NoMatch.BUYING_POWER_EXPIRED
    assert orders.match(b, s, NOW + 900) is orders.NoMatch.EXPIRED


def test_a_sell_order_needs_a_posted_reservation_receipt_of_its_own_issuer():
    common = dict(order_id=orders.OrderId("s1"), party="seller-1", ref=REF, quote_currency="USD", lot_units=1, lots=5,
                  limit_price_per_lot_minor=10, expiry=NOW + 10, provider_id="synthetic", now=NOW)
    ok = orders.place_sell(**common, reserve_receipt=receipt(7))
    assert isinstance(ok, orders.Order) and ok.committed_seq == 7 and ok.reserve_effect == "reserve-1"
    for bad in (b"", b"{}", None, receipt(7, issuer="test.two"), receipt(7, protocol="other"), receipt(0),
                wire.canonical({"protocol": ij.RECEIPT_PROTOCOL, "issuer": "test.one", "seq": 3})):
        assert orders.place_sell(**common, reserve_receipt=bad) is orders.OrderRefusal.NO_RESERVATION


def test_orders_of_another_issuer_unit_currency_or_provider_never_match():
    b, s = buy(10, 5, 1000), sell(10, 5)
    assert isinstance(orders.match(b, s, NOW), orders.Match)
    others = [sell(10, 5, ref=IssuerRef("test.two", "d" * 64, "TESTP")),
              sell(10, 5, ref=IssuerRef("test.one", "e" * 64, "TESTP")),
              sell(10, 5, ref=IssuerRef("test.one", "d" * 64, "OTHER")),
              sell(10, 5, quote_currency="EUR"), sell(10, 5, provider_id="other")]
    for other in others:
        assert orders.match(b, other, NOW) is orders.NoMatch.DIFFERENT_MARKET
    assert orders.match(b, sell(10, 5, lot_units=2), NOW) is orders.NoMatch.LOT_SIZE
    assert orders.match(s, b, NOW) is orders.NoMatch.NOT_A_PAIR


def test_the_waiting_order_sets_the_price_and_ties_go_to_the_earlier_one():
    for bid, ask in ((16, 14), (14, 14), (20, 3)):
        sale_waited = orders.match(buy(bid, 1, 1000, seq=2), sell(ask, 1, seq=1), NOW)
        buy_waited = orders.match(buy(bid, 1, 1000, seq=1), sell(ask, 1, seq=2), NOW)
        assert sale_waited.price_per_lot_minor == ask and sale_waited.resting is orders.Side.SELL
        assert buy_waited.price_per_lot_minor == bid and buy_waited.resting is orders.Side.BUY


def test_bid_below_ask_gives_no_fill_and_no_quote():
    assert orders.match(buy(9, 5, 1000), sell(10, 5), NOW) is orders.NoMatch.NO_CROSS
    assert orders.executable_quote(sell(10, 5), [buy(9, 5, 1000)], NOW) is None


def test_an_unmatched_holding_has_no_executable_quote():
    assert orders.executable_quote(sell(10, 5), [], NOW) is None
    assert orders.executable_quote(sell(10, 5), [buy(10, 5, 0)], NOW) is None
    quote = orders.executable_quote(sell(10, 5), [buy(12, 5, 1000, seq=2)], NOW)
    assert quote.price_per_lot_minor == 10 and quote.lots == 5 and quote.cash_recipient == "seller-1"


def test_the_best_price_then_the_earliest_sequence_wins():
    s = sell(10, 5, seq=1)
    low, high, tie = buy(11, 5, 1000, seq=5), buy(12, 5, 1000, seq=6), buy(12, 5, 1000, seq=4)
    low = orders.Order(**{**low.__dict__, "order_id": orders.OrderId("b-low")})
    chosen, found = orders.best_counterparty(s, [low, high, tie], NOW)
    assert chosen is tie or chosen == tie
    assert found.price_per_lot_minor == 10, "the sell order waited, so its price"
    seller_waited_less = sell(10, 5, seq=9)
    chosen, found = orders.best_counterparty(seller_waited_less, [low, high], NOW)
    assert chosen == high and found.price_per_lot_minor == 12


def test_a_quote_is_hashed_over_every_field():
    base = dict(price_per_lot_minor=10, lots=5, currency="USD", fee_base_minor=50, cash_recipient="s1", payer="b1",
                expiry=2000)
    first = orders.quote_hash(orders.Quote(**base))
    changes = dict(price_per_lot_minor=11, lots=6, currency="EUR", fee_base_minor=51, cash_recipient="s2", payer="b2",
                   expiry=2001)
    assert set(changes) == set(base)
    for name, value in changes.items():
        assert orders.quote_hash(orders.Quote(**{**base, name: value})) != first, name


def test_a_work_order_id_is_not_an_exchange_id():
    quote = orders.Quote(10, 5, "USD", 50, "s1", "b1", 2000)
    work = orders.WorkOrderId("job-7")
    with pytest.raises(TypeError):
        orders.plan_fill(work, orders.FillId("f1"), quote, 0)
    with pytest.raises(TypeError):
        orders.plan_fill(orders.ExchangeId("x1"), work, quote, 0)
    assert orders.ExchangeId("a") != orders.WorkOrderId("a") and orders.OrderId("a") != orders.FillId("a")
    with pytest.raises(ValueError):
        orders.ExchangeId("has space")


def desc(test_only=True, pid="synthetic"):
    return provider.ProviderDescriptor(pid, test_only, ("USD",), 1, "provider", True)


def test_a_live_provider_is_refused_under_every_federation_flag(monkeypatch):
    from agent_friday.services import held_features
    registry = {}
    for flags in ({}, {"federation": True}, {"federation": True, "fed_live_cash": True}):
        monkeypatch.setattr(held_features, "enabled", lambda feature, settings=None, f=flags: bool(f.get(feature)))
        with pytest.raises(provider.LiveProviderRefused):
            provider.register_provider(desc(test_only=False, pid="real"), registry)
    assert registry == {}
    provider.register_provider(desc(), registry)
    assert list(registry) == ["synthetic"]
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

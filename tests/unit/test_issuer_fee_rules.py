"""Fee and split rules of the issuer journal, checked over every small case, not three examples.

Rows: NS-40.4-9 (99/1 split is cumulative and never negative), NS-40.4-10 (allocation is exact
and does not depend on input order), NS-40.4-12 (trial cash fee: increments sum to one fee,
a repeated or empty fill earns nothing).
"""
import itertools

import pytest

from agent_friday.services.issuer_journal import (
    allocate, cash_fee, incremental_fee, operator_share)

SPLITS = [parts for n in range(1, 5) for parts in itertools.product(range(0, 6), repeat=n)]


def compositions(total):
    """Every ordered way to cut `total` into positive increments."""
    if total == 0:
        yield ()
        return
    for first in range(1, total + 1):
        for rest in compositions(total - first):
            yield (first, *rest)


@pytest.mark.parametrize("total", range(0, 13))
def test_fills_of_any_shape_pay_the_same_total_fee_as_one_fill(total):
    one_fill = cash_fee(total)
    for parts in compositions(total):
        assert sum(incremental_fee(parts, cash_fee)) == one_fill, parts


def test_no_fill_ever_pays_a_negative_fee():
    for parts in SPLITS:
        assert all(fee >= 0 for fee in incremental_fee(parts, cash_fee)), parts


def test_a_fill_of_nothing_earns_nothing():
    assert incremental_fee([0, 0, 0], cash_fee) == [0, 0, 0]
    assert incremental_fee([400, 0], cash_fee)[1] == 0


def test_fee_is_a_quarter_percent_rounded_to_nearest():
    assert [cash_fee(c) for c in (0, 199, 200, 599, 600, 40000)] == [0, 0, 1, 1, 2, 100]


@pytest.mark.parametrize("bps", [0, 1, 50, 99, 100])
def test_operators_together_never_take_more_than_one_percent(bps):
    for gross in range(0, 2001, 7):
        take = operator_share(gross, bps)
        assert 0 <= take <= gross // 100, (gross, bps)
        assert gross - take >= gross * 99 // 100


@pytest.mark.parametrize("bps", [1, 50, 100])
def test_a_later_stage_never_takes_back_a_unit_from_an_earlier_one(bps):
    for parts in SPLITS:
        assert all(fee >= 0 for fee in incremental_fee(
            parts, lambda gross: operator_share(gross, bps))), parts


@pytest.mark.parametrize("bps", [-1, 101, 10000])
def test_an_operator_rate_over_one_percent_is_refused(bps):
    with pytest.raises(ValueError):
        operator_share(1000, bps)


def test_allocation_pays_out_exactly_the_units_and_nothing_else():
    weight_sets = [{"a": 1}, {"a": 1, "b": 1}, {"a": 3, "b": 1}, {"a": 1, "b": 2, "c": 3}]
    for weights in weight_sets:
        for units in range(0, 25):
            paid = allocate(weights, units)
            assert sum(paid.values()) == units and set(paid) == set(weights)


def test_allocation_does_not_depend_on_the_order_keys_arrive_in():
    weights = {"b": 1, "a": 1, "c": 1}
    for units in range(0, 10):
        reference = allocate(weights, units)
        for order in itertools.permutations(weights):
            assert allocate({k: weights[k] for k in order}, units) == reference


def test_ties_go_to_the_lowest_payee_id_first():
    assert allocate({"b": 1, "a": 1, "c": 1}, 1) == {"a": 1, "b": 0, "c": 0}
    assert allocate({"b": 1, "a": 1, "c": 1}, 2) == {"a": 1, "b": 1, "c": 0}


def test_a_heavier_weight_never_receives_less_than_a_lighter_one():
    for units in range(0, 30):
        paid = allocate({"light": 1, "heavy": 4}, units)
        assert paid["heavy"] >= paid["light"]

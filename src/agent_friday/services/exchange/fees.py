"""The buyer's cash fee on an exchange order (NS-40.4-12): 0.25% of settled principal, rounded to the
nearest minor unit on the CUMULATIVE principal, so a split order pays what one order would.

Pure functions only: settlement keeps its own durable accounting in its store, so there is no in-memory fee book.

Rules and the tests that hold them (tests/unit/test_exchange_orders.py):
- Each fill is planned the increment, and the increments sum to the one-order fee:
  test_the_bundle_cash_vectors_pass_through_fees.
- A fill's fee is its planned increment; the plan intervals of confirmed fills must not overlap (S-12', design 11.1):
  test_a_plan_charges_each_fill_its_increment_and_two_fills_from_one_starting_point_overlap, and in settlement
  test_every_plan_of_up_to_three_fills_confirmed_in_every_order_completes_with_the_one_order_fee.
- Only a confirmed capture may charge, in one place: services/exchange/settlement.py (its own tests).
"""
from __future__ import annotations

from agent_friday.services.issuer_journal import cash_fee


def fee_increment(cumulative_before: int, principal: int) -> int:
    """The fee owed for `principal` more, given what was already settled on this order."""
    if type(cumulative_before) is not int or type(principal) is not int or cumulative_before < 0 or principal < 0:
        raise ValueError("principal is whole non-negative minor units")
    return cash_fee(cumulative_before + principal) - cash_fee(cumulative_before)


def planned_increments(principals) -> list:
    """The increment each fill is charged when the fills are planned in this order."""
    out, before = [], 0
    for principal in principals:
        out.append(fee_increment(before, principal))
        before += principal
    return out


def plan_overlaps(intervals) -> bool:
    """True when any two plan intervals (planned_before, principal) of an order's fills overlap: two fills planned from the
    same starting point would each be charged the full fee. The interval is [planned_before, planned_before + principal)."""
    spans = sorted((start, start + size) for start, size in intervals)
    return any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1))

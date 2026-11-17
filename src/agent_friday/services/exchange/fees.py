"""The buyer's cash fee on an exchange order (NS-40.4-12): 0.25% of settled principal, rounded to the
nearest minor unit on the CUMULATIVE principal, so a split order pays what one order would.

Pure functions only: settlement keeps its own durable accounting in its store, so there is no in-memory fee book.

Rules and the tests that hold them (tests/unit/test_exchange_orders.py):
- Each fill is planned the increment, and the increments sum to the one-order fee:
  test_the_bundle_cash_vectors_pass_through_fees.
- In any confirmation order the sum charged never exceeds the fee on the cumulative principal (S-12):
  test_the_fee_total_never_exceeds_the_cumulative_fee_in_any_confirmation_order.
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


def within_cumulative(charged, principals) -> bool:
    """True when what was charged never exceeds the fee on the cumulative principal of these fills."""
    return all(type(c) is int and c >= 0 for c in charged) and sum(charged) <= cash_fee(sum(principals))
